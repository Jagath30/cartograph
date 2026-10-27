/**
 * Security headers on every page (step 12, the brief's decision 8): set
 * in next.config, so they hold wherever the build is served. Each pinned.
 */
import { expect, test } from "vitest";

import config, { SECURITY_HEADERS } from "../next.config";

const PINNED = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Frame-Options": "DENY",
  "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Strict-Transport-Security": "max-age=63072000",
};

test("each header is the one pinned, and there are no others", () => {
  expect(Object.fromEntries(SECURITY_HEADERS.map(({ key, value }) => [key, value]))).toEqual(PINNED);
  expect(SECURITY_HEADERS).toHaveLength(6);
});

test("they are set on every path, so on every page", async () => {
  const rules = await config.headers!();
  expect(rules).toEqual([{ source: "/:path*", headers: SECURITY_HEADERS }]);
});
