/** Polish D, the accessibility audit (axe-core 4.13.0, WCAG 2.1 A and AA,
 *  and a keyboard walk, in a driven browser). These hold what it fixed. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

const read = (path: string) => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), "utf8");

test("a block of code that may scroll sideways takes focus, and has a name", () => {
  const panel = read("components/ask/TracePanel.tsx");
  expect(panel).toContain('<pre className={CODE} role="region" aria-label={label} tabIndex={0}>');
  // Every block of code goes through it.
  expect(panel.match(/<pre /g)?.length).toBe(1);
});

test("a table's identifier in the graph is dark enough on the header of a table the plan started from", () => {
  // gray-500 on slate-100 is 4.41:1; gray-600 passes 4.5:1.
  expect(read("components/graph/TableNode.tsx")).toContain('<span className="truncate font-mono text-[12px] text-gray-600">{node.id}</span>');
});

test("React Flow's attribution stays, in a grey that can be read", () => {
  expect(read("app/globals.css")).toMatch(/\.react-flow \.react-flow__attribution a \{\s*color: #4b5563;/);
});

test("a line of the graph is not a tab stop", () => {
  expect(read("components/graph/GraphView.tsx")).toContain("edgesFocusable={false}");
});
