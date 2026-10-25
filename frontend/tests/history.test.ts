/**
 * /history (the brief's decision 7; FR-34, IR-08): the signed-in user's
 * own questions, newest first, each with when it was asked, how it ended,
 * and a mark where a choice had no basis. Each opens /history/[id].
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";

import { HistoryList } from "../components/history/HistoryList";
import type { Listed } from "../lib/api";
import { formatWhen } from "../lib/ask/format";
import { MARK, OUTCOME_WORDS } from "../lib/history";

const read = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");

const LISTED: Listed[] = [
  { query_id: "33333333-0000-4000-8000-000000000003", question: "Third, the newest?", created_at: "2026-10-25T09:03:00Z", status: "answered", had_ambiguity: true },
  { query_id: "22222222-0000-4000-8000-000000000002", question: "Second?", created_at: "2026-10-25T09:02:00Z", status: "not_answerable", had_ambiguity: false },
  { query_id: "11111111-0000-4000-8000-000000000001", question: "First <b>?</b>", created_at: "2026-10-25T09:01:00Z", status: "validation_failed", had_ambiguity: false },
];

const html = renderToStaticMarkup(createElement(HistoryList, { listed: LISTED }));

test("each question is listed in the order the API gave, newest first, as a link to its page", () => {
  const links = [...html.matchAll(/<a[^>]*href="([^"]+)"[^>]*>([^<]*)/g)].map((m) => [m[1], m[2]]);
  expect(links).toEqual([
    ["/history/33333333-0000-4000-8000-000000000003", "Third, the newest?"],
    ["/history/22222222-0000-4000-8000-000000000002", "Second?"],
    ["/history/11111111-0000-4000-8000-000000000001", "First &lt;b&gt;?&lt;/b&gt;"],
  ]);
});

test("each says when it was asked, as the answer page writes a moment", () => {
  for (const row of LISTED) expect(html).toContain(formatWhen(row.created_at));
});

test("each says how it ended, in words", () => {
  expect(OUTCOME_WORDS).toEqual({
    answered: "Answered",
    not_answerable: "Declined",
    validation_failed: "Refused before it ran",
    model_failed: "No usable reply from the model",
    execution_failed: "Did not complete",
  });
  const order = ["Answered", "Declined", "Refused before it ran"].map((words) => html.indexOf(words));
  expect(order.every((at) => at > 0)).toBe(true);
  expect([...order].sort((a, b) => a - b)).toEqual(order);
});

test("a choice without basis is marked, and only there", () => {
  expect(MARK).toBe("A choice had no basis");
  expect(html.split(MARK).length - 1).toBe(1);
  expect(html.indexOf(MARK)).toBeLessThan(html.indexOf("Second?"));
});

test("an empty history says so and sends the reader to Ask", () => {
  const empty = renderToStaticMarkup(createElement(HistoryList, { listed: [] }));
  expect(empty).toContain("No questions yet.");
  expect(empty).toContain('href="/ask"');
});

test("the list is a list", () => {
  expect(html.startsWith("<ol")).toBe(true);
  expect(html.match(/<li[\s>]/g)?.length).toBe(3);
});

test("the history pages read through the client, the list and the one", () => {
  expect(read("app/history/page.tsx")).toMatch(/\bapi\s*\.history\(\)/);
  expect(read("app/history/[id]/page.tsx")).toMatch(/\bapi\s*\.query\(id\)/);
  expect(read("app/history/[id]/page.tsx")).toContain("<Answer ");
});
