/**
 * The cold start (step 12, item 85): when /api/v1/health has not answered
 * within 3 seconds, the page says the server is waking; the notice goes
 * when the server answers.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { WakingView } from "../components/frame/Waking";
import { ApiError } from "./api";
import { WAKING, WAKING_AFTER_MS, watchWaking } from "./waking";

const read = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");

/** A health call the test answers when it chooses. */
function pending() {
  const calls: { resolve: (v: unknown) => void; reject: (e: unknown) => void }[] = [];
  const health = () => new Promise((resolve, reject) => calls.push({ resolve, reject }));
  return { health, calls };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

test("the words are the brief's, and the wait is 3 seconds", () => {
  expect(WAKING).toBe(
    "Waking the server. Free hosting sleeps after 15 minutes without visitors; the first request can take up to a minute.",
  );
  expect(WAKING_AFTER_MS).toBe(3000);
});

test("an answer within 3 seconds shows nothing", async () => {
  const { health, calls } = pending();
  const shown: boolean[] = [];
  watchWaking(health, (on) => shown.push(on));
  await vi.advanceTimersByTimeAsync(2999);
  calls[0].resolve({ status: "ok" });
  await vi.advanceTimersByTimeAsync(10_000);
  expect(shown.filter(Boolean)).toEqual([]);
});

test("no answer by 3 seconds shows the notice, and the answer takes it away", async () => {
  const { health, calls } = pending();
  const shown: boolean[] = [];
  watchWaking(health, (on) => shown.push(on));
  await vi.advanceTimersByTimeAsync(3000);
  expect(shown.at(-1)).toBe(true);
  calls[0].resolve({ status: "ok" });
  await vi.advanceTimersByTimeAsync(0);
  expect(shown.at(-1)).toBe(false);
});

test("an error the server sent is an answer: it is awake", async () => {
  const { health, calls } = pending();
  const shown: boolean[] = [];
  watchWaking(health, (on) => shown.push(on));
  await vi.advanceTimersByTimeAsync(3000);
  calls[0].reject(new ApiError(503, "not_ready", "not yet"));
  await vi.advanceTimersByTimeAsync(0);
  expect(shown.at(-1)).toBe(false);
  expect(calls).toHaveLength(1);
});

test("no answer at all keeps the notice and asks again", async () => {
  const { health, calls } = pending();
  const shown: boolean[] = [];
  watchWaking(health, (on) => shown.push(on));
  await vi.advanceTimersByTimeAsync(3000);
  calls[0].reject(new ApiError(0, "unreachable", "The backend could not be reached."));
  await vi.advanceTimersByTimeAsync(5000);
  expect(shown.at(-1)).toBe(true);
  expect(calls).toHaveLength(2);
  calls[1].resolve({ status: "ok" });
  await vi.advanceTimersByTimeAsync(0);
  expect(shown.at(-1)).toBe(false);
});

test("stopped, it shows nothing more and asks nothing more", async () => {
  const { health, calls } = pending();
  const shown: boolean[] = [];
  const stop = watchWaking(health, (on) => shown.push(on));
  stop();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(shown.filter(Boolean)).toEqual([]);
  calls[0].reject(new ApiError(0, "unreachable", "x"));
  await vi.advanceTimersByTimeAsync(10_000);
  expect(calls).toHaveLength(1);
});

test("the notice is a polite status, and nothing when not shown", () => {
  const html = renderToStaticMarkup(createElement(WakingView, { shown: true }));
  expect(html).toContain('role="status"');
  expect(html).toContain(WAKING);
  expect(renderToStaticMarkup(createElement(WakingView, { shown: false }))).toBe("");
});

test("every page has it: the layout renders it", () => {
  const layout = read("app/layout.tsx");
  expect(layout).toMatch(/import \{ WakingNotice \} from "@\/components\/frame\/Waking"/);
  expect(layout).toContain("<WakingNotice />");
});
