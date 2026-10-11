/**
 * The only module in the frontend that talks to the backend (IR-16).
 *
 * Every request in the application goes through `request` below. That is
 * what makes step 11's session handling a change to one file rather than
 * a sweep through every call site, and it is where T-08's credentials
 * mode is decided once.
 */

import type { QueryOut } from "./trace/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1";

/** How long the browser waits for one answer. A question is one request
 *  and one response (D-05); the warehouse's own limit is 30 seconds and
 *  the model's is beyond our control. */
export const TIMEOUT_MS = 90_000;

/** One thing wrong with a request, as a 422 lists them. */
export type Detail = { where?: string; why?: string };

/**
 * Mirrors IR-05: a machine-readable code alongside a human-readable
 * message. Two codes are the browser's own, for an answer that never
 * came: `unreachable` and `timeout`, both with status 0.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    /** A 422's list of what was wrong. */
    readonly detail: Detail[] | null = null,
    /** A 500 `trace_not_persisted` names the query it could not keep. */
    readonly queryId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);

  // Only on requests that carry a body. Setting it on a GET would make
  // application/json a non-safelisted header and force a CORS preflight
  // before every read.
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers,
      // Does nothing today; required once sessions arrive (T-08).
      credentials: "include",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "TimeoutError") {
      throw new ApiError(0, "timeout", `No answer came back within ${TIMEOUT_MS / 1000} seconds.`);
    }
    throw new ApiError(0, "unreachable", "The backend could not be reached.");
  }

  if (!response.ok) {
    let code = "unknown";
    let message = response.statusText;
    let detail: Detail[] | null = null;
    let queryId: string | null = null;
    try {
      const body = await response.json();
      code = body.code ?? code;
      message = body.message ?? (typeof body.detail === "string" ? body.detail : message);
      detail = Array.isArray(body.detail) ? body.detail : null;
      queryId = body.query_id ?? null;
    } catch {
      // Error body was not JSON. Keep the status text.
    }
    throw new ApiError(response.status, code, message, detail, queryId);
  }

  return (await response.json()) as T;
}

export type Health = { status: string };

export type Check = { ok: boolean; detail: string };

export type Readiness = {
  status: "ready" | "not_ready";
  checks: Record<string, Check>;
};

export const api = {
  health: () => request<Health>("/health"),
  ready: () => request<Readiness>("/ready"),
  /** IR-02: one question, one response, with its trace. */
  ask: (question: string) => request<QueryOut>("/queries", { method: "POST", body: JSON.stringify({ question }) }),
  /** One stored query with its trace. Its rows are not stored (DR-15). */
  query: (id: string) => request<QueryOut>(`/queries/${encodeURIComponent(id)}`),
};
