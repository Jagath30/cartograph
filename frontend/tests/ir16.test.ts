/**
 * IR-16, and the owner's point h at stop 1 of step 9: every request the
 * frontend makes goes through lib/api.ts, straight from the browser.
 *
 * No route handler and no server action stands between the page and the
 * API: either would run on the host that serves the page, and bring that
 * host's time limit to a question that may take longer than it allows.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SOURCE = ["app", "components", "lib"];
const CLIENT = join("lib", "api.ts");

function files(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return files(path);
    return /\.(ts|tsx|js|jsx|mjs|mts)$/.test(name) ? [path] : [];
  });
}

const all = SOURCE.flatMap((directory) => files(join(ROOT, directory))).map((path) => relative(ROOT, path));
const code = all.filter((path) => !path.endsWith(".test.ts"));

test("there is source to read, and the client is among it", () => {
  expect(code.length).toBeGreaterThan(5);
  expect(code).toContain(CLIENT);
});

test("nothing but lib/api.ts makes a request", () => {
  const ways = /\bfetch\s*\(|XMLHttpRequest|sendBeacon|new\s+(EventSource|WebSocket)\b|from\s+["']axios["']/;
  const callers = code.filter((path) => ways.test(readFileSync(join(ROOT, path), "utf8")));
  expect(callers).toEqual([CLIENT]);
});

test("nothing but lib/api.ts knows where the API is", () => {
  const knows = code.filter((path) => /NEXT_PUBLIC_API_BASE_URL|\/api\/v1/.test(readFileSync(join(ROOT, path), "utf8")));
  expect(knows).toEqual([CLIENT]);
});

test("no route handler stands between the page and the API", () => {
  const handlers = all.filter((path) => /(^|[\\/])route\.(ts|tsx|js|jsx|mjs)$/.test(path));
  expect(handlers).toEqual([]);
  expect(all.filter((path) => path.split(sep)[0] === "pages")).toEqual([]);
});

test("no server action, and no proxy", () => {
  const actions = code.filter((path) => /^\s*["']use server["']/m.test(readFileSync(join(ROOT, path), "utf8")));
  expect(actions).toEqual([]);
  for (const name of ["proxy.ts", "proxy.js", "middleware.ts", "middleware.js"]) {
    expect(() => statSync(join(ROOT, name))).toThrow();
  }
});

test("every page is a client component", () => {
  const pages = code.filter((path) => /(^|[\\/])page\.tsx$/.test(path));
  expect(pages.length).toBeGreaterThan(0);
  for (const page of pages) {
    expect(readFileSync(join(ROOT, page), "utf8").trimStart().startsWith('"use client"')).toBe(true);
  }
});
