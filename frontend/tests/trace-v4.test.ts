/**
 * Trace version 4 (step 14): `served_from_cache`, on an answer served from
 * the answer cache. The page says so plainly; the trace panel draws the
 * rest as it draws version 3. Fixture made by the backend's own
 * app.core.answer_cache (backend/tests/stored_traces/make_v4_fixture.py).
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";

import { CACHED_NOTICE, ServedFromCache } from "../components/ask/Answer";
import storedV3 from "../fixtures/trace-v3-catalog-customers.json";
import hitV4 from "../fixtures/trace-v4-served-from-cache.json";
import { READABLE_VERSIONS, type TraceDocument } from "../lib/trace/types";
import { drawn } from "./rendered-versions.test";

const hit = hitV4 as unknown as TraceDocument;
const v3 = storedV3 as unknown as TraceDocument;

test("this page reads version 4", () => {
  expect(READABLE_VERSIONS).toContain(4);
  expect(hit.trace_version).toBe(4);
});

test("a hit is marked, at no cost, and holds nothing of the first asker", () => {
  expect(hit.served_from_cache).toBe(true);
  expect([hit.cost_usd, hit.tokens_in, hit.tokens_out]).toEqual([0, 0, 0]);
  expect(hit.user_id).not.toBe(v3.user_id);
  expect(hit.query_id).not.toBe(v3.query_id);
  expect(hit.created_at).not.toBe(v3.created_at);
});

test("the notice says plainly that it was served from the cache, and that it cost nothing", () => {
  const html = renderToStaticMarkup(createElement(ServedFromCache));
  expect(html).toContain("Served from the cache");
  expect(html).toContain("no model was called and it cost nothing");
  expect(CACHED_NOTICE.line).toContain("24 hours");
});

test("a version 3 answer carries no mark, and the panel draws a hit as it draws the answer it copies", () => {
  expect(v3.served_from_cache).toBeUndefined();
  const strip = (trace: TraceDocument) =>
    ({ ...trace, trace_version: 3, query_id: "-", cost_usd: 0, tokens_in: 0, tokens_out: 0, duration_ms: 0 }) as TraceDocument;
  const { served_from_cache: _, ...rest } = hit;
  expect(drawn(strip(rest as TraceDocument))).toBe(drawn(strip(v3)));
});
