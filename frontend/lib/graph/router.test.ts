/**
 * Where a line goes (the owner's ruling on the graph at step 9): never
 * across a table it does not join.
 */
import { describe, expect, test } from "vitest";

import {
  BRACKET,
  BRACKET_EDGE,
  BRACKET_STEP,
  bracketOffset,
  CLEARANCE,
  OUTSIDE,
  columnsOf,
  crossingHeight,
  freeGaps,
  pathOf,
  route,
  sample,
  tablesUnder,
  type Box,
} from "./router";

const box = (id: string, x: number, y: number, height = 100, width = 200): Box => ({ id, x, y, width, height });

// Three columns, 100 apart: 0-200, 300-500, 600-800.
const A = box("a", 0, 100);
const M1 = box("m1", 300, 0, 120);
const M2 = box("m2", 300, 200, 120);
const B = box("b", 600, 150);
const THREE = columnsOf([A, M1, M2, B]);

describe("the columns", () => {
  test("left to right, each with its tables top to bottom", () => {
    expect(THREE.map((column) => [column.left, column.right, column.boxes.map((b) => b.id)])).toEqual([
      [0, 200, ["a"]],
      [300, 500, ["m1", "m2"]],
      [600, 800, ["b"]],
    ]);
  });

  test("a column's free gaps: above its tables, between two with room, and below", () => {
    expect(freeGaps(THREE[1])).toEqual([
      [-OUTSIDE, -OUTSIDE],
      [120 + CLEARANCE, 200 - CLEARANCE],
      [320 + OUTSIDE, 320 + OUTSIDE],
    ]);
  });

  test("two tables too close for a line between them leave no gap there", () => {
    const tight = columnsOf([box("p", 0, 0, 100), box("q", 0, 110, 100)])[0];
    expect(freeGaps(tight)).toEqual([
      [-OUTSIDE, -OUTSIDE],
      [210 + OUTSIDE, 210 + OUTSIDE],
    ]);
  });
});

describe("a line across two columns", () => {
  const points = route({ box: A, y: 150 }, { box: B, y: 200 }, THREE);

  test("leaves its table on the side facing where it goes, and arrives the same way", () => {
    expect(points[0]).toEqual({ x: 200, y: 150 });
    expect(points[points.length - 1]).toEqual({ x: 600, y: 200 });
  });

  test("crosses the column between level, through the gap nearest its straight course", () => {
    expect(points.slice(1, 3)).toEqual([
      { x: 300, y: 175 },
      { x: 500, y: 175 },
    ]);
  });

  test("lies on no table but its own two", () => {
    expect(tablesUnder(points, [A, M1, M2, B], ["a", "b"])).toEqual([]);
  });

  test("a course that would hit a table goes to the nearest free height", () => {
    // Course 30 in the middle column, inside m1 (0-120): above m1, 48 away,
    // is nearer than the gap below it, 98 away.
    const a = box("a", 0, 0);
    const b = box("b", 600, 0);
    expect(route({ box: a, y: 40 }, { box: b, y: 20 }, columnsOf([a, M1, M2, b]))[1].y).toBe(-OUTSIDE);
    // Course 260, inside m2 (200-320): the gap above m2 is nearer than below.
    const c = box("c", 0, 200);
    const d = box("d", 600, 200);
    expect(route({ box: c, y: 260 }, { box: d, y: 260 }, columnsOf([c, M1, M2, d]))[1].y).toBe(200 - CLEARANCE);
  });

  test("going right to left, it leaves from the left side", () => {
    const back = route({ box: B, y: 200 }, { box: A, y: 150 }, THREE);
    expect(back[0]).toEqual({ x: 600, y: 200 });
    expect(back[1].x).toBe(500);
    expect(back[back.length - 1]).toEqual({ x: 200, y: 150 });
    expect(tablesUnder(back, [A, M1, M2, B], ["a", "b"])).toEqual([]);
  });
});

describe("a column whose only free gaps are above or below its tables", () => {
  const TALL = box("tall", 300, -50, 400);
  const columns = columnsOf([A, TALL, B]);

  test("is crossed above or below, whichever is nearer the straight course", () => {
    expect(freeGaps(columns[1])).toEqual([
      [-50 - OUTSIDE, -50 - OUTSIDE],
      [350 + OUTSIDE, 350 + OUTSIDE],
    ]);
    const near_top = route({ box: A, y: 110 }, { box: B, y: 160 }, columns);
    expect(near_top[1].y).toBe(-50 - OUTSIDE);
    const near_bottom = route({ box: box("a2", 0, 300), y: 390 }, { box: box("b2", 600, 300), y: 390 }, columnsOf([box("a2", 0, 300), TALL, box("b2", 600, 300)]));
    expect(near_bottom[1].y).toBe(350 + OUTSIDE);
  });

  test("and never across the tall table", () => {
    expect(tablesUnder(route({ box: A, y: 150 }, { box: B, y: 200 }, columns), [A, TALL, B], ["a", "b"])).toEqual([]);
  });

  test("of two gaps as near, the upper", () => {
    const one = columnsOf([box("o", 0, 0, 100)])[0];
    expect(crossingHeight(one, 50)).toBe(-OUTSIDE);
  });
});

describe("a line between two tables of one column", () => {
  const top = box("top", 300, 0);
  const middle = box("middle", 300, 150);
  const bottom = box("bottom", 300, 300);
  const columns = columnsOf([A, top, middle, bottom, B]);

  test("is a bracket in the channel to the right of the column", () => {
    const points = route({ box: top, y: 50 }, { box: bottom, y: 350 }, columns);
    expect(points).toEqual([
      { x: 500, y: 50 },
      { x: 500 + BRACKET, y: 50 },
      { x: 500 + BRACKET, y: 350 },
      { x: 500, y: 350 },
    ]);
    expect(tablesUnder(points, [A, top, middle, bottom, B], ["top", "bottom"])).toEqual([]);
  });

  test("brackets beside one column stand apart", () => {
    const second = route({ box: top, y: 50 }, { box: middle, y: 200 }, columns, { index: 1, count: 2 });
    expect(second[1].x).toBe(500 + BRACKET + BRACKET_STEP);
  });

  test("many brackets share the channel and none reaches the next column", () => {
    // d5 of the development questions: nine brackets beside one column, in
    // a channel 84 wide. At 10 apart the ninth stood 96 out, on a table.
    const offsets = Array.from({ length: 9 }, (_, index) => bracketOffset(index, 9, 84));
    expect(offsets[0]).toBe(BRACKET);
    expect(offsets[8]).toBe(84 - BRACKET_EDGE);
    expect(offsets.every((offset, index) => index === 0 || offset > offsets[index - 1])).toBe(true);
    expect(bracketOffset(1, 2, 84)).toBe(BRACKET + BRACKET_STEP);
    expect(bracketOffset(0, 1, 84)).toBe(BRACKET);
    // A channel too narrow for the first bracket: its middle.
    expect(bracketOffset(3, 5, 20)).toBe(10);
    // Past the last column there is room for all.
    expect(bracketOffset(30, 31, Infinity)).toBe(BRACKET + 30 * BRACKET_STEP);
  });

  test("nine brackets beside a column with a table beyond its channel stay off that table", () => {
    const stack = Array.from({ length: 10 }, (_, index) => box(`t${index}`, 0, index * 130, 100));
    const beyond = box("beyond", 284, 0, 1300);
    const tables = [...stack, beyond];
    const stacked = columnsOf(tables);
    for (let index = 0; index < 9; index += 1) {
      const points = route({ box: stack[0], y: 50 }, { box: stack[index + 1], y: (index + 1) * 130 + 50 }, stacked, { index, count: 9 });
      expect(tablesUnder(points, tables, ["t0", `t${index + 1}`])).toEqual([]);
    }
  });

  test("in the last column, to its left", () => {
    const last = columnsOf([A, box("u", 600, 0), box("v", 600, 200)]);
    const points = route({ box: last[1].boxes[0], y: 50 }, { box: last[1].boxes[1], y: 250 }, last);
    expect(points[0]).toEqual({ x: 600, y: 50 });
    expect(points[1].x).toBe(600 - BRACKET);
  });

  test("in a lone column, to its right", () => {
    const alone = columnsOf([box("u", 0, 0), box("v", 0, 200)]);
    expect(route({ box: alone[0].boxes[0], y: 50 }, { box: alone[0].boxes[1], y: 250 }, alone)[1].x).toBe(200 + BRACKET);
  });
});

describe("the path drawn", () => {
  test("straight where level or upright, an S-curve with level ends otherwise", () => {
    expect(pathOf([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 20 }, { x: 30, y: 40 }])).toBe(
      "M 0 0 L 10 0 L 10 20 C 20 20 20 40 30 40",
    );
  });

  test("the samples follow it, and an S-curve stays within its stretch's width", () => {
    const points = sample([{ x: 0, y: 0 }, { x: 100, y: 50 }], 20);
    expect(points[0]).toEqual({ x: 0, y: 0 });
    expect(points[points.length - 1]).toEqual({ x: 100, y: 50 });
    expect(points.every((p) => p.x >= 0 && p.x <= 100 && p.y >= 0 && p.y <= 50)).toBe(true);
  });

  test("a table the line does cross is reported", () => {
    expect(tablesUnder([{ x: 0, y: 50 }, { x: 400, y: 50 }], [box("in-the-way", 100, 0)], [])).toEqual(["in-the-way"]);
  });

  test("an unknown table is refused, not drawn across", () => {
    expect(() => route({ box: box("x", 0, 0), y: 0 }, { box: B, y: 0 }, THREE)).toThrow();
  });
});
