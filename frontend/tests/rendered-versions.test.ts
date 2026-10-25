/** A trace stored at version 1 or 2 is drawn as it was before version 3
 *  (DD-06; the plan approved at stop F1): the amber block, the narrative,
 *  both disclosures and the graph, compared with what the page drew
 *  before step 10b's adoption, written then into fixtures/rendered/. */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";

import { AmberBlock, Disclosures, Narrative } from "../components/ask/TracePanel";
import storedV1 from "../fixtures/trace-v1-days-of-the-week.json";
import storedV2 from "../fixtures/trace-v2-gender-diverged.json";
import { fromTrace } from "../lib/graph/fromTrace";
import type { TraceDocument } from "../lib/trace/types";

const path = (name: string) => fileURLToPath(new URL(`../fixtures/rendered/${name}`, import.meta.url));

export function drawn(trace: TraceDocument): string {
  const parts = [AmberBlock, Narrative, Disclosures].map((part) => renderToStaticMarkup(createElement(part, { trace })));
  return [...parts, JSON.stringify(fromTrace(trace), null, 2)].join("\n\n") + "\n";
}

for (const [name, trace] of [
  ["trace-v1-days-of-the-week", storedV1],
  ["trace-v2-gender-diverged", storedV2],
] as const) {
  test(`a stored ${name.slice(6, 8)} trace is drawn as it was before version 3`, () => {
    const now = drawn(trace as unknown as TraceDocument);
    if (process.env.WRITE_RENDERED === "1") writeFileSync(path(`${name}.html`), now);
    expect(now).toBe(readFileSync(path(`${name}.html`), "utf8"));
  });
}
