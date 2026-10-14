/**
 * The page frame (polish A): what can be held without a browser. The
 * rendered page, its focus order and its console are checked in a driven
 * browser at each stop.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import { LINE_WIDTH, POINTS, POINT_RADIUS, VIEW } from "../lib/frame/glyph";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

test("the favicon is the header's glyph: the same points, joined in the same order", () => {
  const icon = read("app/icon.svg");
  expect(icon).toContain(`viewBox="0 0 ${VIEW} ${VIEW}"`);
  const circles = [...icon.matchAll(/<circle cx="(\d+)" cy="(\d+)" r="(\d+)"/g)].map((m) => m.slice(1).map(Number));
  expect(circles).toEqual(POINTS.map(([x, y]) => [x, y, POINT_RADIUS]));
  const path = POINTS.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x} ${y}`).join(" ");
  expect(icon).toContain(`d="${path}"`);
  expect(icon).toContain(`stroke-width="${LINE_WIDTH}"`);
});

test("the layout gives every page its landmarks, a skip link to main, and English", () => {
  const layout = read("app/layout.tsx");
  expect(layout).toContain('<html lang="en">');
  expect(layout).toContain('<main id="main" tabIndex={-1}');
  expect(read("components/frame/Frame.tsx")).toContain('href="#main"');
  expect(read("components/frame/Frame.tsx")).toMatch(/<header[\s>]/);
  expect(read("components/frame/Frame.tsx")).toMatch(/<footer[\s>]/);
});

function sources(directory: string): string[] {
  return readdirSync(join(ROOT, directory)).flatMap((name) => {
    const path = join(directory, name);
    return statSync(join(ROOT, path)).isDirectory() ? sources(path) : /\.tsx$/.test(name) ? [path] : [];
  });
}

test("each page has exactly one h1, and no page brings a main landmark of its own", () => {
  const pages = sources("app").filter((path) => /(page|not-found)\.tsx$/.test(path));
  expect(pages.length).toBeGreaterThanOrEqual(3);
  for (const page of pages) {
    const text = read(page);
    expect((text.match(/<h1[\s>]/g) ?? []).length, page).toBe(1);
    expect(text, page).not.toMatch(/<main[\s>]/);
  }
  expect(read("components/frame/Frame.tsx")).not.toMatch(/<h[1-6][\s>]/);
});

// Tailwind's amber classes, and the amber hex values of its palette. The
// colours, not the word: a comment may name amber.
const AMBER_COLOUR =
  /\b(?:bg|text|border|ring|outline|fill|stroke|from|via|to|decoration|shadow|accent|caret|divide)-amber-\d{2,3}\b|#(?:fffbeb|fef3c7|fde68a|fcd34d|fbbf24|f59e0b|d97706|b45309|92400e|78350f|451a03)\b/i;

test("the frame keeps amber for the loud warnings: no amber colour in it", () => {
  for (const path of ["components/frame/Frame.tsx", "components/frame/Glyph.tsx", "app/layout.tsx", "app/not-found.tsx", "app/globals.css", "app/icon.svg"]) {
    expect(read(path), path).not.toMatch(AMBER_COLOUR);
  }
});

test("the amber test looks for colours, not the word", () => {
  expect("/* amber is kept for the warnings */").not.toMatch(AMBER_COLOUR);
  for (const colour of ["bg-amber-50", "border-amber-400", "text-amber-950", "#f59e0b", "#FBBF24"]) {
    expect(`className="x ${colour}"`).toMatch(AMBER_COLOUR);
  }
});

test("the main landmark draws no outline when the skip link focuses it", () => {
  expect(read("app/globals.css")).toMatch(/main:focus,\s*main:focus-visible\s*\{\s*outline: none;/);
});

test("the 404 page sends the reader back to Ask", () => {
  expect(read("app/not-found.tsx")).toContain('href="/ask"');
});
