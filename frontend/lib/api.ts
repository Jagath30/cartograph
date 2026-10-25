/**
 * The only module in the frontend that talks to the backend (IR-16).
 *
 * Every request in the application goes through `request` below, and
 * goes to this page's own origin (step 11, T-08): next.config rewrites
 * /api/v1 to the backend. So the session cookie is first-party, sent with
 * every call and to nowhere else.
 */

import type { Outcome, QueryOut, Value } from "./trace/types";

const BASE_URL = "/api/v1";

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
      // The session cookie, to this origin only.
      credentials: "same-origin",
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

  // A 204 has no body: logout.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export type Health = { status: string };

export type Check = { ok: boolean; detail: string };

export type Readiness = {
  status: "ready" | "not_ready";
  checks: Record<string, Check>;
};

export type Account = { email: string };

/** One row of a history: no trace (DD-17). */
export type Listed = {
  query_id: string;
  question: string;
  created_at: string;
  status: Outcome;
  /** A choice made without a basis touched the answer (DD-21). */
  had_ambiguity: boolean;
};

export type History = { queries: Listed[] };

/** A stored answer's SQL run again (item 88): the rows as of `ran_at`. */
export type RerunOut = {
  query_id: string;
  ran_at: string;
  status: "answered" | "validation_failed" | "execution_failed";
  code: string | null;
  message: string | null;
  columns: string[];
  rows: Value[][] | null;
  row_count: number | null;
  truncated: boolean | null;
};

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/health"),
  ready: () => request<Readiness>("/ready"),
  /** IR-02: one question, one response, with its trace. */
  ask: (question: string) => request<QueryOut>("/queries", post({ question })),
  /** One of the caller's stored queries with its trace. Its rows are not stored (DR-15). */
  query: (id: string) => request<QueryOut>(`/queries/${encodeURIComponent(id)}`),
  /** IR-08: the caller's newest 100, newest first. */
  history: () => request<History>("/queries"),
  /** Item 88: the stored SQL validated again and run now. No body. */
  rerun: (id: string) => request<RerunOut>(`/queries/${encodeURIComponent(id)}/rerun`, { method: "POST" }),
  /** IR-09. Registering does not sign in. */
  register: (email: string, password: string) => request<Account>("/auth/register", post({ email, password })),
  login: (email: string, password: string) => request<Account>("/auth/login", post({ email, password })),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  /** Who the session is, or a 401. */
  me: () => request<Account>("/auth/me"),
};
