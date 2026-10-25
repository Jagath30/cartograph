/**
 * Step 11: one origin (T-08, the brief's decision 2). The browser calls
 * only the frontend's own origin, at /api/v1/...; next.config rewrites
 * that path to the backend. So the client's URLs are relative, the
 * session cookie is first-party, and NEXT_PUBLIC_API_BASE_URL is gone.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { api, TIMEOUT_MS } from "../lib/api";
import config, { backendOrigin, PROXY_TIMEOUT_MS } from "../next.config";

const read = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");

type Sent = { url: string; init: RequestInit };
let sent: Sent[] = [];

beforeEach(() => {
  sent = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    sent.push({ url, init });
    if (init.method === "POST" && url.endsWith("/auth/logout")) return new Response(null, { status: 204 });
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("every call goes to this origin's /api/v1, by a relative URL", async () => {
  await api.health();
  await api.ask("Where are buyers billed?");
  await api.query("abc");
  await api.history();
  await api.rerun("abc");
  await api.register("ada@example.com", "correct horse battery");
  await api.login("ada@example.com", "correct horse battery");
  await api.me();
  await api.logout();
  expect(sent.map(({ url, init }) => `${init.method ?? "GET"} ${url}`)).toEqual([
    "GET /api/v1/health",
    "POST /api/v1/queries",
    "GET /api/v1/queries/abc",
    "GET /api/v1/queries",
    "POST /api/v1/queries/abc/rerun",
    "POST /api/v1/auth/register",
    "POST /api/v1/auth/login",
    "GET /api/v1/auth/me",
    "POST /api/v1/auth/logout",
  ]);
});

test("the cookie goes with every call, and only to this origin", async () => {
  await api.history();
  await api.login("ada@example.com", "correct horse battery");
  for (const { init } of sent) expect(init.credentials).toBe("same-origin");
});

test("a body is JSON and declared so; a call without one declares nothing", async () => {
  await api.login("ada@example.com", "correct horse battery");
  await api.rerun("abc");
  await api.logout();
  const [login, rerun, logout] = sent;
  expect(new Headers(login.init.headers).get("Content-Type")).toBe("application/json");
  expect(JSON.parse(login.init.body as string)).toEqual({ email: "ada@example.com", password: "correct horse battery" });
  for (const bodiless of [rerun, logout]) {
    expect(bodiless.init.body).toBeUndefined();
    expect(new Headers(bodiless.init.headers).has("Content-Type")).toBe(false);
  }
});

test("an id is encoded into its path", async () => {
  await api.query("a/b?c");
  await api.rerun("a/b?c");
  expect(sent.map(({ url }) => url)).toEqual(["/api/v1/queries/a%2Fb%3Fc", "/api/v1/queries/a%2Fb%3Fc/rerun"]);
});

test("a 204 is an answer with no body", async () => {
  await expect(api.logout()).resolves.toBeUndefined();
});

test("the client names no host, and NEXT_PUBLIC_API_BASE_URL is gone", () => {
  const client = read("lib/api.ts");
  expect(client).toContain('const BASE_URL = "/api/v1";');
  expect(client).not.toMatch(/https?:\/\//);
  for (const path of ["lib/api.ts", "next.config.ts", "Dockerfile"]) {
    expect(read(path), path).not.toContain("NEXT_PUBLIC_API_BASE_URL");
  }
});

test("next.config rewrites /api/v1 to the backend's origin, path and all", async () => {
  const rules = await config.rewrites!();
  expect(rules).toEqual([{ source: "/api/v1/:path*", destination: `${backendOrigin()}/api/v1/:path*` }]);
});

test("the backend's origin comes from BACKEND_ORIGIN, and locally falls back to the compose port", () => {
  expect(backendOrigin({ BACKEND_ORIGIN: "http://backend:8000" })).toBe("http://backend:8000");
  expect(backendOrigin({ BACKEND_ORIGIN: "https://api.example.com/" })).toBe("https://api.example.com");
  expect(backendOrigin({})).toBe("http://127.0.0.1:8000");
});

test("on Vercel, a build without BACKEND_ORIGIN fails rather than proxy to nowhere", () => {
  expect(() => backendOrigin({ VERCEL: "1" })).toThrow(/BACKEND_ORIGIN/);
  expect(backendOrigin({ VERCEL: "1", BACKEND_ORIGIN: "https://api.example.com" })).toBe("https://api.example.com");
});

test("next's own proxy waits longer than the browser does (locally its default is 30 s)", () => {
  expect(PROXY_TIMEOUT_MS).toBe(120_000);
  expect(PROXY_TIMEOUT_MS).toBeGreaterThan(TIMEOUT_MS);
  expect(config.experimental?.proxyTimeout).toBe(PROXY_TIMEOUT_MS);
});

test("the build is the standard one: rewrites do not survive a static export", () => {
  expect(config.output).toBeUndefined();
});
