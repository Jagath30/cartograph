/**
 * What the sign-in and register forms share (step 11).
 */
import { ApiError } from "@/lib/api";

export const FIELD = "mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-[15px] focus:border-gray-500";
export const SUBMIT =
  "rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 disabled:cursor-wait disabled:bg-gray-500";

/** What the server said, or that it could not be reached. Never what was
 *  typed: the API does not echo it, and nothing here adds it. */
export function said(cause: unknown): string {
  if (!(cause instanceof ApiError)) return "Something went wrong. Try again.";
  const why = (cause.detail ?? []).map((entry) => entry.why).filter(Boolean);
  return why.length > 0 ? why.join(" ") : cause.message;
}
