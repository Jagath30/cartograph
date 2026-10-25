/**
 * A page that needs a session sends the visitor to /login and back again
 * (the brief's decision 7; the owner's ruling 2 at the plan stop).
 *
 * `next` is the page to go back to. It is taken only as a path beginning
 * with exactly one slash; anything else, "//", "/\", a scheme or a host,
 * falls back to /ask. Sent to router.replace, a bad one would leave the
 * site or run script.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";

import { AccountView } from "../components/frame/Session";
import { FALLBACK, loginHref, safeNext, whereTo } from "../lib/auth/next";

const read = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");

test.each([
  ["/ask", "/ask"],
  ["/history", "/history"],
  ["/history/3f2b8c1e-0000-4000-8000-000000000000", "/history/3f2b8c1e-0000-4000-8000-000000000000"],
  ["/ask?id=abc", "/ask?id=abc"],
  ["/history#top", "/history#top"],
  ["/a/b/c?x=1&y=2", "/a/b/c?x=1&y=2"],
])("a path on this site is taken: %s", (given, taken) => {
  expect(safeNext(given)).toBe(taken);
});

test.each([
  ["no next at all", null],
  ["empty", ""],
  ["two slashes, another host", "//evil.example"],
  ["two slashes and a path", "//evil.example/ask"],
  ["slash backslash", "/\\evil.example"],
  ["backslash slash", "\\/evil.example"],
  ["a backslash anywhere", "/ask\\..\\x"],
  ["a scheme", "https://evil.example/ask"],
  ["a scheme in capitals", "HTTPS://evil.example"],
  ["javascript", "javascript:alert(1)"],
  ["a data URL", "data:text/html,<script>alert(1)</script>"],
  ["no leading slash", "evil.example"],
  ["a relative path", "history"],
  ["a tab the browser drops", "/\t/evil.example"],
  ["a newline the browser drops", "/\n/evil.example"],
  ["a space first", " /ask"],
  ["three slashes", "///evil.example"],
  ["an encoded second slash", "/%2F/evil.example"],
  ["an encoded backslash", "/%5Cevil.example"],
  ["the login page itself", "/login"],
  ["the register page", "/register?next=/ask"],
])("anything else falls back to /ask: %s", (_, given) => {
  expect(safeNext(given)).toBe(FALLBACK);
  expect(FALLBACK).toBe("/ask");
});

test("the way to /login carries the page to come back to, encoded", () => {
  expect(loginHref("/history", "")).toBe("/login?next=%2Fhistory");
  expect(loginHref("/ask", "?id=abc")).toBe("/login?next=%2Fask%3Fid%3Dabc");
  expect(safeNext(new URLSearchParams(loginHref("/ask", "?id=abc").split("?")[1]).get("next"))).toBe("/ask?id=abc");
});

test("a page that needs a session waits while it is checked, shows when signed in, and sends to /login when not", () => {
  expect(whereTo({ kind: "checking" }, "/history", "")).toEqual({ show: false, redirect: null });
  expect(whereTo({ kind: "in", email: "ada@example.com" }, "/history", "")).toEqual({ show: true, redirect: null });
  expect(whereTo({ kind: "out" }, "/history/abc", "")).toEqual({ show: false, redirect: "/login?next=%2Fhistory%2Fabc" });
});

test("the pages that need a session ask for one, and login and register do not", () => {
  for (const page of ["app/ask/page.tsx", "app/history/page.tsx", "app/history/[id]/page.tsx"]) {
    expect(read(page), page).toContain("useRequiredSession()");
  }
  for (const page of ["app/login/page.tsx", "app/register/page.tsx"]) {
    expect(read(page), page).not.toContain("useRequiredSession");
    expect(read(page), page).toContain("safeNext(");
  }
});

test("nothing navigates to a next that has not been through safeNext", () => {
  for (const page of ["app/login/page.tsx", "app/register/page.tsx"]) {
    const text = read(page);
    expect(text, page).not.toMatch(/router\.(push|replace)\(\s*next\b/);
    expect(text, page).not.toMatch(/router\.(push|replace)\([^)]*get\("next"\)/);
  }
});

// --------------------------------------------------------------------------
// The header: who is signed in, with Sign out
// --------------------------------------------------------------------------

test("signed in, the header names the account and offers Sign out", () => {
  const html = renderToStaticMarkup(createElement(AccountView, { session: { kind: "in", email: "ada@example.com" }, onSignOut: () => {} }));
  expect(html).toContain("ada@example.com");
  expect(html).toMatch(/<button[^>]*type="button"[^>]*>Sign out<\/button>/);
  expect(html).toContain('href="/history"');
});

test("signed out, the header offers Sign in and no Sign out", () => {
  const html = renderToStaticMarkup(createElement(AccountView, { session: { kind: "out" }, onSignOut: () => {} }));
  expect(html).toContain('href="/login"');
  expect(html).toContain("Sign in");
  expect(html).not.toContain("Sign out");
});

test("while the session is checked, the header says nothing of it", () => {
  const html = renderToStaticMarkup(createElement(AccountView, { session: { kind: "checking" }, onSignOut: () => {} }));
  expect(html).not.toMatch(/Sign (in|out)/);
});

test("the header holds the account, and the layout holds the session for every page", () => {
  expect(read("components/frame/Frame.tsx")).toContain("<Account />");
  expect(read("app/layout.tsx")).toContain("<SessionProvider>");
});

test("the forms say what the browser should fill in", () => {
  const login = read("app/login/page.tsx");
  expect(login).toContain('autoComplete="email"');
  expect(login).toContain('autoComplete="current-password"');
  const register = read("app/register/page.tsx");
  expect(register).toContain('autoComplete="email"');
  expect(register).toContain('autoComplete="new-password"');
  expect(register).toContain("minLength={8}");
  expect(register).toContain("maxLength={128}");
});
