/** A line drawn with the amber band says so first, wherever it is told:
 *  the strip above the graph, the tooltip, and the joins in words (close
 *  of step 9). */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

const read = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");

test("an amber line is first called a choice made with no basis", () => {
  expect(read("components/graph/styles.ts")).toContain('export const NO_BASIS = "A choice made with no basis";');
  const view = read("components/graph/GraphView.tsx");
  for (const line of ["shown", "pointed", "edge"]) {
    expect(view).toContain(`{${line}.loud && <span className="font-medium">{NO_BASIS}. </span>}`);
  }
});
