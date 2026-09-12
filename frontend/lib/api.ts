/**
 * The only module in the frontend that talks to the backend (IR-16).
 *
 * Every request in the application goes through `request` below. That is
 * what makes step 11's session handling a change to one file rather than
 * a sweep through every call site, and it is where T-08's credentials
 * mode is decided once.
 */

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1";

/** Mirrors IR-05: a machine-readable code alongside a human-readable message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
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

  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
    // Does nothing today; required once sessions arrive (T-08).
    credentials: "include",
  });

  if (!response.ok) {
    let code = "unknown";
    let message = response.statusText;
    try {
      const body = await response.json();
      code = body.code ?? code;
      message = body.message ?? body.detail ?? message;
    } catch {
      // Error body was not JSON. Keep the status text.
    }
    throw new ApiError(response.status, code, message);
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
};
