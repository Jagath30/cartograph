/**
 * Where a line goes (the owner's ruling on the graph at step 9). Pure and
 * deterministic: the same tables in the same places give the same lines.
 *
 * TRUTHFULNESS FIRST: a line must never seem to join tables it does not
 * join, so no part of a line lies on a table but its own two ends.
 *
 * The tables stand in columns, and between the columns are channels where
 * no table is. A line is a run of points:
 *
 *   - across a channel it is an S-curve with level ends, which stays within
 *     the channel's width, so it cannot touch a table;
 *   - through a column between its two ends it runs level, through the free
 *     gap nearest its straight course: between two tables, or above the
 *     highest or below the lowest. A column whose tables leave no gap
 *     between them is crossed above or below;
 *   - between two tables of one column it is a bracket in the channel
 *     beside that column.
 *
 * A line leaves its table from the side that faces where it is going.
 */

export type Point = { x: number; y: number };

export type Box = { id: string; x: number; y: number; width: number; height: number };

export type Column = { left: number; right: number; boxes: Box[] };

/** How close a level run may come to a table above or below it. */
export const CLEARANCE = 8;
/** How far beyond the highest or lowest table a run goes round a column. */
export const OUTSIDE = 18;
/** How far into the channel the first bracket beside a column stands, and
 *  how far apart the next ones are at most. */
export const BRACKET = 16;
export const BRACKET_STEP = 10;
/** The room a bracket leaves before the next column's tables. */
export const BRACKET_EDGE = 8;

/** Where one of `count` brackets stands, measured out from its column, in
 *  a channel `width` wide. They share the channel, closer together when
 *  there are many, and none reaches the next column. */
export function bracketOffset(index: number, count: number, width: number): number {
  const room = width - BRACKET - BRACKET_EDGE;
  if (room < 0) return width / 2;
  const step = count > 1 ? Math.min(BRACKET_STEP, room / (count - 1)) : 0;
  return BRACKET + index * step;
}

/** The columns, left to right, each with its tables top to bottom. */
export function columnsOf(boxes: Box[]): Column[] {
  const byLeft = new Map<number, Box[]>();
  for (const box of boxes) {
    const left = Math.round(box.x);
    byLeft.set(left, [...(byLeft.get(left) ?? []), box]);
  }
  return [...byLeft.entries()]
    .sort(([a], [b]) => a - b)
    .map(([, members]) => ({
      left: Math.min(...members.map((box) => box.x)),
      right: Math.max(...members.map((box) => box.x + box.width)),
      boxes: [...members].sort((a, b) => a.y - b.y || a.id.localeCompare(b.id)),
    }));
}

/** The heights at which a level run may cross a column: [low, high] for
 *  each free gap, the one above its tables and the one below included. */
export function freeGaps(column: Column): [number, number][] {
  const gaps: [number, number][] = [];
  const first = column.boxes[0];
  const last = column.boxes[column.boxes.length - 1];
  gaps.push([first.y - OUTSIDE, first.y - OUTSIDE]);
  for (let index = 1; index < column.boxes.length; index += 1) {
    const above = column.boxes[index - 1];
    const below = column.boxes[index];
    const low = above.y + above.height + CLEARANCE;
    const high = below.y - CLEARANCE;
    if (low <= high) gaps.push([low, high]);
  }
  gaps.push([last.y + last.height + OUTSIDE, last.y + last.height + OUTSIDE]);
  return gaps;
}

/** The height nearest `y` at which a run may cross: nearest gap first, and
 *  of two as near, the upper. */
export function crossingHeight(column: Column, y: number): number {
  let best = NaN;
  let distance = Infinity;
  for (const [low, high] of freeGaps(column)) {
    const at = Math.min(Math.max(y, low), high);
    if (Math.abs(at - y) < distance) {
      best = at;
      distance = Math.abs(at - y);
    }
  }
  return best;
}

export type End = { box: Box; y: number };

/**
 * The points of one line, from the row it leaves to the row it reaches.
 * `bracket` is the line's place among the brackets beside its column, and
 * how many there are; it is used only when both ends are in one column.
 */
export function route(from: End, to: End, columns: Column[], bracket = { index: 0, count: 1 }): Point[] {
  const at = (box: Box) => columns.findIndex((column) => column.boxes.some((member) => member.id === box.id));
  const start = at(from.box);
  const end = at(to.box);
  if (start < 0 || end < 0) throw new Error("a line's table is not among the columns");

  if (start === end) {
    // A bracket beside the column: in the channel to its right if there is
    // one, otherwise to its left, otherwise to the right all the same.
    const column = columns[start];
    const right = start < columns.length - 1 || start === 0;
    // The channel beside it; past the last column, as wide as it needs.
    const width =
      right && start < columns.length - 1
        ? columns[start + 1].left - column.right
        : !right
          ? column.left - columns[start - 1].right
          : Infinity;
    const offset = bracketOffset(bracket.index, bracket.count, width);
    const side = right ? column.right : column.left;
    const away = right ? side + offset : side - offset;
    return [
      { x: right ? from.box.x + from.box.width : from.box.x, y: from.y },
      { x: away, y: from.y },
      { x: away, y: to.y },
      { x: right ? to.box.x + to.box.width : to.box.x, y: to.y },
    ];
  }

  const rightward = end > start;
  const leave: Point = { x: rightward ? from.box.x + from.box.width : from.box.x, y: from.y };
  const arrive: Point = { x: rightward ? to.box.x : to.box.x + to.box.width, y: to.y };
  const points = [leave];
  const step = rightward ? 1 : -1;
  for (let index = start + step; index !== end; index += step) {
    const column = columns[index];
    const middle = (column.left + column.right) / 2;
    // The straight course's height in the middle of this column.
    const share = (middle - leave.x) / (arrive.x - leave.x);
    const y = crossingHeight(column, leave.y + share * (arrive.y - leave.y));
    const [enter, exit] = rightward ? [column.left, column.right] : [column.right, column.left];
    points.push({ x: enter, y }, { x: exit, y });
  }
  points.push(arrive);
  return points;
}

/** SVG path data: level or upright stretches straight, every other one an
 *  S-curve with level ends, whose controls stand halfway across. */
export function pathOf(points: Point[]): string {
  let d = `M ${points[0].x} ${points[0].y}`;
  for (let index = 1; index < points.length; index += 1) {
    const a = points[index - 1];
    const b = points[index];
    if (a.x === b.x || a.y === b.y) {
      d += ` L ${b.x} ${b.y}`;
    } else {
      const middle = (a.x + b.x) / 2;
      d += ` C ${middle} ${a.y} ${middle} ${b.y} ${b.x} ${b.y}`;
    }
  }
  return d;
}

/** Points along the drawn line, for measuring where it goes. */
export function sample(points: Point[], perStretch = 40): Point[] {
  const out: Point[] = [points[0]];
  for (let index = 1; index < points.length; index += 1) {
    const a = points[index - 1];
    const b = points[index];
    const curved = a.x !== b.x && a.y !== b.y;
    const middle = (a.x + b.x) / 2;
    for (let step = 1; step <= perStretch; step += 1) {
      const t = step / perStretch;
      if (!curved) {
        out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
      } else {
        const u = 1 - t;
        out.push({
          x: u * u * u * a.x + 3 * u * u * t * middle + 3 * u * t * t * middle + t * t * t * b.x,
          y: u * u * u * a.y + 3 * u * u * t * a.y + 3 * u * t * t * b.y + t * t * t * b.y,
        });
      }
    }
  }
  return out;
}

/** The tables, of those given, that some point of the line lies on, its
 *  own two ends' tables apart. */
export function tablesUnder(points: Point[], boxes: Box[], ends: string[]): string[] {
  const along = sample(points);
  return boxes
    .filter((box) => !ends.includes(box.id))
    .filter((box) => along.some((p) => p.x > box.x && p.x < box.x + box.width && p.y > box.y && p.y < box.y + box.height))
    .map((box) => box.id);
}
