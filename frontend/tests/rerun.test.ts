/**
 * Item 88: a stored answer keeps no rows. Reopened, it shows its
 * explanation, graph and SQL, and a button, "Run this SQL again". The
 * rows that come back are the data as of now, not as of the question,
 * and the page says so. Nothing is stored.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";

import { RerunView } from "../components/ask/Rerun";
import storedV3 from "../fixtures/trace-v3-catalog-customers.json";
import declined from "../fixtures/trace-v3-declined-at-linking.json";
import { ApiError, type RerunOut } from "../lib/api";
import { formatWhen } from "../lib/ask/format";
import { canRerun, RERUN_LABEL, asOfNow } from "../lib/rerun";
import type { QueryOut, TraceDocument } from "../lib/trace/types";

const read = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");

const trace = storedV3 as unknown as TraceDocument;
const STORED: QueryOut = {
  query_id: trace.query_id, status: trace.outcome, code: null, message: null, columns: trace.execution?.columns ?? [],
  rows: null, row_count: trace.execution?.row_count ?? null, truncated: trace.execution?.truncated ?? null, trace,
};
const AGAIN: RerunOut = {
  query_id: trace.query_id, ran_at: "2026-10-26T08:15:00Z", status: "answered", code: null, message: null,
  columns: ["state", "customers"], rows: [["TX", 12345], ["CA", 99]], row_count: 2, truncated: false,
};

const view = (state: Parameters<typeof RerunView>[0]["state"]) =>
  renderToStaticMarkup(createElement(RerunView, { stored: STORED, state, onRun: () => {} }));

test("the button's words are the brief's", () => {
  expect(RERUN_LABEL).toBe("Run this SQL again");
  expect(view({ kind: "idle" })).toMatch(/<button[^>]*type="button"[^>]*>Run this SQL again<\/button>/);
});

test("before it is run, the page says the rows were not kept and what running it does", () => {
  const html = view({ kind: "idle" });
  expect(html).toContain("The rows are not stored.");
  expect(html).toContain("runs the stored SQL against the data as it is now");
});

test("while it runs, the button waits and says so", () => {
  const html = view({ kind: "running" });
  expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Running/);
});

test("the rows that come back are shown, and said to be as of now, not as of the question", () => {
  const html = view({ kind: "done", result: AGAIN });
  expect(html).toContain("12,345");
  expect(html).toContain(">TX<");
  expect(asOfNow(AGAIN.ran_at)).toBe(`This is the data as of now (${formatWhen(AGAIN.ran_at)}), not as of the question.`);
  expect(html).toContain(asOfNow(AGAIN.ran_at));
  expect(html).toMatch(/>Run this SQL again<\/button>/);
});

test("a rerun the validator refuses is said to be refused, and shows no rows", () => {
  const refused: RerunOut = { ...AGAIN, status: "validation_failed", code: "validation_failure", message: "The SQL was not run: it writes.",
    columns: [], rows: null, row_count: null, truncated: null };  // prettier-ignore
  const html = view({ kind: "done", result: refused });
  expect(html).toContain("The SQL was refused when it was checked again");
  expect(html).toContain("The SQL was not run: it writes.");
  expect(html).not.toContain("<table");
  expect(html).not.toContain("as of now");
  expect(html).toContain("The rows are not stored.");
});

test("a rerun that fails to reach an answer says what the server said", () => {
  const html = view({ kind: "failed", error: new ApiError(503, "not_ready", "the application store holds no schema snapshot") });
  expect(html).toContain("the application store holds no schema snapshot");
});

test("only an answer with SQL can be run again", () => {
  expect(canRerun(STORED)).toBe(true);
  const none = declined as unknown as TraceDocument;
  expect(canRerun({ ...STORED, status: none.outcome, trace: none })).toBe(false);
});

test("the stored answer offers the rerun, and a fresh answer does not", () => {
  const answer = read("components/ask/Answer.tsx");
  expect(answer).toMatch(/\{stored && canRerun\(result\) && <Rerun stored=\{result\} \/>\}/);
});

test("the rerun goes through the client", () => {
  expect(read("components/ask/Rerun.tsx")).toMatch(/\bapi\s*\.rerun\(stored\.query_id\)/);
});
