/** Narrow screens, below 1024 px (polish F). The page itself is measured
 *  in a driven browser at 390, 768 and 1023 px; these hold the rules. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

const read = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");

test("the answer is one column below 1024 px, the account before the results", () => {
  const page = read("components/ask/Answer.tsx");
  expect(page).toContain('className="grid items-start gap-8 lg:grid-cols-2"');
  expect(page.indexOf("<Narrative trace={trace} />")).toBeLessThan(page.indexOf('data-ask="results"'));
  expect(page).not.toMatch(/\border-(first|last|\d)/);
});

test("wide tables and code scroll sideways inside their own box", () => {
  const panel = read("components/ask/TracePanel.tsx");
  expect(panel).toContain('const SIDEWAYS = "mt-2 overflow-x-auto";');
  expect(panel.match(/<div className=\{SIDEWAYS\} role="region" aria-label="[^"]+" tabIndex=\{0\}>/g)?.length).toBe(2);
  expect(panel).toContain('overflow-x-auto whitespace-pre rounded');
  expect(read("components/ask/ResultTable.tsx")).toContain("overflow-auto");
});

test("on a narrow screen the graph keeps a readable zoom, is dragged, and says so", () => {
  const view = read("components/graph/GraphView.tsx");
  expect(view).toContain('const WIDE = "(min-width: 1024px)";');
  expect(view).toContain("const NARROW_MIN_ZOOM = 0.8;");
  expect(view).toContain("Drag to move around the graph; it reads best on a wider screen.");
});

test("on a narrow screen the graph opens at the picture's top left, where the plan starts", () => {
  const view = read("components/graph/GraphView.tsx");
  expect(view).toContain("fitView={wide}");
  expect(view).toContain("flow.setViewport({ x: -x * zoom, y: -y * zoom, zoom });");
});
