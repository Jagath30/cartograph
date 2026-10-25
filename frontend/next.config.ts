import type { NextConfig } from "next";

/**
 * Where the backend is, for the rewrite below. Read when the config is
 * loaded: by `next dev` at start, and by `next build` at build time, so
 * on Vercel BACKEND_ORIGIN is a build variable (step 12). Locally it is
 * the backend's compose address.
 */
export function backendOrigin(env: Record<string, string | undefined> = process.env): string {
  const origin = env.BACKEND_ORIGIN?.trim();
  if (origin) return origin.replace(/\/+$/, "");
  // A deployment that forgot it must fail to build, not proxy to nowhere.
  if (env.VERCEL) throw new Error("BACKEND_ORIGIN must be set at build time: the rewrite of /api/v1 needs it.");
  return "http://127.0.0.1:8000";
}

/** Next's own proxy gives up on an external rewrite after 30 s unless told
 *  otherwise (locally: `next dev`, `next start`). A question may take
 *  longer, and the client waits 90 s. Vercel's CDN proxies the rewrite
 *  itself and does not read this. */
export const PROXY_TIMEOUT_MS = 120_000;

const nextConfig: NextConfig = {
  // Development only. `next dev` serves its own assets to `localhost` and
  // to the host it was started with, which in the container is 0.0.0.0.
  // The compose file, the smoke check and the API's CORS list all use
  // 127.0.0.1, and without this entry a page opened there is served and
  // never comes alive: its scripts are refused.
  allowedDevOrigins: ["127.0.0.1"],
  experimental: { proxyTimeout: PROXY_TIMEOUT_MS },
  // The application is /ask. Temporary (307), not permanent: "/" may
  // become something else.
  async redirects() {
    return [{ source: "/", destination: "/ask", permanent: false }];
  },
  // One origin (step 11, T-08): the browser calls /api/v1 here, and this
  // carries it to the backend, so the session cookie is first-party. A
  // rewrite, never a route handler, middleware or server action: those
  // run as functions, under the host's function time limit.
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${backendOrigin()}/api/v1/:path*` }];
  },
};

export default nextConfig;
