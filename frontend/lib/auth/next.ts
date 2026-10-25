/**
 * Where a visitor sent to /login goes back to (step 11; the owner's
 * ruling 2 at the plan stop). Pure.
 *
 * `next` is taken only as a path on this site: it begins with exactly
 * one slash. Refused, and replaced by /ask: "//" and "/\" (another host,
 * to a browser), a backslash anywhere, an encoded slash or backslash, a
 * scheme ("https:", "javascript:"), anything not starting with "/", any
 * space or control character (a browser drops a tab or a newline, and
 * "/\t/host" becomes "//host"), and the login and register pages
 * themselves. What is taken is safe to hand to router.replace.
 */

export const FALLBACK = "/ask";

const SIGN_IN_PAGES = ["/login", "/register"];

export function safeNext(raw: string | null | undefined): string {
  if (typeof raw !== "string" || raw === "") return FALLBACK;
  if (!raw.startsWith("/") || raw.startsWith("//")) return FALLBACK;
  if (raw.includes("\\") || /%(2f|5c)/i.test(raw)) return FALLBACK;
  // Any space or control character, as a browser may drop or fold it.
  if (/[\s\u0000-\u001f\u007f]/.test(raw)) return FALLBACK;
  const path = raw.split(/[?#]/, 1)[0];
  if (path.includes(":")) return FALLBACK;
  if (SIGN_IN_PAGES.some((page) => path === page || path.startsWith(`${page}/`))) return FALLBACK;
  return raw;
}

/** The way to /login from a page, carrying that page to come back to. */
export function loginHref(pathname: string, search: string): string {
  return `/login?next=${encodeURIComponent(`${pathname}${search}`)}`;
}

export type Session = { kind: "checking" } | { kind: "in"; email: string } | { kind: "out" };

/** What a page that needs a session does: wait while it is checked, show
 *  itself when signed in, or go to /login and back again. */
export function whereTo(session: Session, pathname: string, search: string): { show: boolean; redirect: string | null } {
  if (session.kind === "in") return { show: true, redirect: null };
  if (session.kind === "out") return { show: false, redirect: loginHref(pathname, search) };
  return { show: false, redirect: null };
}
