/** The empty page (polish B): what it says, and how its cards behave. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import { EXAMPLES } from "../lib/ask/examples";
import { DATA, LEAD, STEPS } from "../lib/ask/intro";

const read = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");
const intro = read("components/ask/Intro.tsx");

test("the copy, as the owner approved it at stop F1 (v2.1 adopted)", () => {
  expect(LEAD).toBe(
    "Cartograph answers plain-English questions about a retail data warehouse. It shows which tables it used, how it joined them, and how each choice of join was made: by a rule, by a language model reading your question, or with no basis.",
  );
  expect(STEPS).toEqual([
    "Has a language model name the tables your question needs, out of the warehouse's 24.",
    "Picks the joins between them by its rules, asks the model where the rules leave a choice open, and says which other routes it set aside, and why.",
    "Has the model write the SQL, then checks it against the plan and runs it on a read-only connection.",
  ]);
  expect(STEPS.join(" ")).not.toContain("own keys");
  expect(DATA).toBe("The data is a synthetic TPC-DS retail warehouse: sales and returns through stores, catalogs and the web.");
});

test("three steps in a row at desktop width", () => {
  expect(intro).toContain('className="grid gap-4 lg:grid-cols-3"');
});

test("each example is one card, a button named by its question, showing what it shows", () => {
  expect(intro).toContain("aria-label={example.question}");
  expect(intro).toContain("onClick={() => onPick(example.question)}");
  expect(intro).toContain("Shows: {example.shows}");
  // As asked live at stop F3 of step 10b; the gender question was replaced by the original set's Q3 (ruling 4).
  expect(EXAMPLES.map((example) => example.question)).toEqual([
    "Where are our catalog customers located?",
    "Which five states spent the most on the web?",
    "How many customers were born in each country?",
    "Which of our suppliers deliver late most often?",
  ]);
  expect(EXAMPLES.map((example) => example.shows)).toEqual([
    "a choice made with no basis",
    "a choice made with no basis",
    "a quiet answer",
    "an honest decline",
  ]);
});

test("no amber on the empty page", () => {
  expect(intro).not.toMatch(/-amber-\d|#f59e0b|#fbbf24|#d97706/i);
});

test("the page shows the empty page only before anything is asked or read", () => {
  expect(read("app/ask/page.tsx")).toContain('state.kind === "idle" && !reading ? <Intro');
});

test("the meta description", () => {
  expect(read("app/layout.tsx")).toContain(
    'description: "Plain-English questions over a data warehouse, answered in SQL, with every join explained."',
  );
});
