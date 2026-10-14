/** The zoom buttons sit above the picture, never on it (step 9). */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

const view = readFileSync(fileURLToPath(new URL("../components/graph/GraphView.tsx", import.meta.url)), "utf8");

test("React Flow's own controls are not drawn on the canvas", () => {
  expect(view).not.toMatch(/<Controls[\s/>]/);
});

test("the three buttons keep React Flow's names", () => {
  for (const name of ["Zoom In", "Zoom Out", "Fit View"]) expect(view).toContain(`aria-label="${name}"`);
  expect(view).toContain('role="toolbar"');
});
