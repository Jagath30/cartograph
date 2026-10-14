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
 * EACH LINE ITS OWN LANE (the owner's ruling after b8b505d). Lines that
 * cross a column through the same gap do not share a level run: each has
 * its own height, LANE apart, in the order of their straight courses and
 * then of the heights they start from, so that they cross one another as
 * little as may be. In a narrow gap the lanes come closer, never under
 * LANE_MIN apart, and keep CLEARANCE from the tables; lines that do not
 * fit go to the next nearest gap. Above or below a column the lanes stack
 * outward, LANE apart.
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
/** How far apart two level runs through one gap are, and at the closest. */
export const LANE = 8;
export const LANE_MIN = 5;
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

export type Line = { id: string; from: End; to: End; bracket?: { index: number; count: number } };

type Wanted = { id: string; course: number; start: number };

const indexOf = (columns: Column[], box: Box) =>
  columns.findIndex((column) => column.boxes.some((member) => member.id === box.id));

/** Where each line's level run through one column goes: its own lane. */
export function lanes(column: Column, wanted: Wanted[]): Map<string, number> {
  const gaps = freeGaps(column);
  const above = 0;
  const below = gaps.length - 1;
  const distance = (y: number, [low, high]: [number, number]) => Math.abs(Math.min(Math.max(y, low), high) - y);
  const barred = new Map<string, Set<number>>(wanted.map((line) => [line.id, new Set()]));
  const nearest = (line: Wanted) => {
    let best = -1;
    gaps.forEach((gap, index) => {
      if (barred.get(line.id)!.has(index)) return;
      if (best < 0 || distance(line.course, gap) < distance(line.course, gaps[best])) best = index;
    });
    return best;
  };
  const gapOf = new Map(wanted.map((line) => [line.id, nearest(line)]));

  // A gap between two tables holds as many lanes as fit LANE_MIN apart; the
  // lines farthest from it go on to their next nearest gap. Above and below
  // there is always room.
  for (let changed = true; changed; ) {
    changed = false;
    for (let index = above + 1; index < below; index += 1) {
      const [low, high] = gaps[index];
      const room = Math.floor((high - low) / LANE_MIN) + 1;
      const members = wanted
        .filter((line) => gapOf.get(line.id) === index)
        .sort((a, b) => distance(a.course, gaps[index]) - distance(b.course, gaps[index]) || a.id.localeCompare(b.id));
      for (const line of members.slice(room)) {
        barred.get(line.id)!.add(index);
        gapOf.set(line.id, nearest(line));
        changed = true;
      }
    }
  }

  const heights = new Map<string, number>();
  gaps.forEach(([low, high], index) => {
    const members = wanted
      .filter((line) => gapOf.get(line.id) === index)
      .sort((a, b) => a.course - b.course || a.start - b.start || a.id.localeCompare(b.id));
    const count = members.length;
    if (count === 0) return;
    members.forEach((line, place) => {
      if (index === above) heights.set(line.id, high - (count - 1 - place) * LANE);
      else if (index === below) heights.set(line.id, low + place * LANE);
      else {
        const step = count > 1 ? Math.min(LANE, (high - low) / (count - 1)) : 0;
        const block = (count - 1) * step;
        const mean = members.reduce((sum, member) => sum + member.course, 0) / count;
        const centre = Math.min(Math.max(mean, low + block / 2), high - block / 2);
        heights.set(line.id, centre - block / 2 + place * step);
      }
    });
  });
  return heights;
}

/** Every line at once, each in its own lane through every column it
 *  crosses. A line between two tables of one column is its bracket. */
export function routeLines(lines: Line[], columns: Column[]): Map<string, Point[]> {
  type Crossing = { column: number; course: number };
  const shape = new Map<string, { leave: Point; arrive: Point; crossings: Crossing[] }>();
  const wanted = new Map<number, Wanted[]>();
  for (const line of lines) {
    const start = indexOf(columns, line.from.box);
    const end = indexOf(columns, line.to.box);
    if (start < 0 || end < 0) throw new Error("a line's table is not among the columns");
    if (start === end) continue;
    const rightward = end > start;
    const leave = { x: rightward ? line.from.box.x + line.from.box.width : line.from.box.x, y: line.from.y };
    const arrive = { x: rightward ? line.to.box.x : line.to.box.x + line.to.box.width, y: line.to.y };
    const crossings: Crossing[] = [];
    for (let index = start + (rightward ? 1 : -1); index !== end; index += rightward ? 1 : -1) {
      const middle = (columns[index].left + columns[index].right) / 2;
      const course = leave.y + ((middle - leave.x) / (arrive.x - leave.x)) * (arrive.y - leave.y);
      crossings.push({ column: index, course });
      // Where it starts from, on the left: the height it comes in at.
      const left = rightward ? leave.y : arrive.y;
      wanted.set(index, [...(wanted.get(index) ?? []), { id: line.id, course, start: left }]);
    }
    shape.set(line.id, { leave, arrive, crossings });
  }
  const heights = new Map<number, Map<string, number>>();
  for (const [index, members] of wanted) heights.set(index, lanes(columns[index], members));

  const routes = new Map<string, Point[]>();
  for (const line of lines) {
    const drawn = shape.get(line.id);
    if (!drawn) {
      routes.set(line.id, route(line.from, line.to, columns, line.bracket));
      continue;
    }
    const points = [drawn.leave];
    for (const crossing of drawn.crossings) {
      const column = columns[crossing.column];
      const y = heights.get(crossing.column)!.get(line.id)!;
      const [enter, exit] = drawn.arrive.x > drawn.leave.x ? [column.left, column.right] : [column.right, column.left];
      points.push({ x: enter, y }, { x: exit, y });
    }
    points.push(drawn.arrive);
    routes.set(line.id, points);
  }
  return routes;
}

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

const samePoint = (p: Point, q: Point) => Math.abs(p.x - q.x) < 0.01 && Math.abs(p.y - q.y) < 0.01;

function segmentDistance(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length = dx * dx + dy * dy;
  const t = length === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function polylineDistance(p: Point, line: Point[]): number {
  let nearest = Infinity;
  for (let index = 1; index < line.length; index += 1) nearest = Math.min(nearest, segmentDistance(p, line[index - 1], line[index]));
  return nearest;
}

/** Whether two stretches cross or touch; lying along one another is not
 *  crossing. */
function properlyCross(p1: Point, p2: Point, q1: Point, q2: Point): boolean {
  const side = (a: Point, b: Point, c: Point) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  const d1 = side(q1, q2, p1);
  const d2 = side(q1, q2, p2);
  const d3 = side(p1, p2, q1);
  const d4 = side(p1, p2, q2);
  if (d1 === 0 && d2 === 0) return false;
  return d1 * d2 <= 0 && d3 * d4 <= 0;
}

/**
 * Whether two lines run alongside each other: within `within` pixels over
 * more than `over` pixels of one of them, without crossing anywhere. Where
 * they meet the same row is left out: there they must come together. Two
 * lines that cross come close where they cross, and that is a crossing,
 * not a line drawn on another (the owner's ruling after b8b505d).
 */
export function runAlongside(a: Point[], b: Point[], within = 2, over = 20): boolean {
  const per = 40;
  const trim = (line: Point[], other: Point[]) => {
    let along = sample(line, per);
    if (samePoint(line[line.length - 1], other[other.length - 1]) || samePoint(line[line.length - 1], other[0])) along = along.slice(0, -per);
    if (samePoint(line[0], other[0]) || samePoint(line[0], other[other.length - 1])) along = along.slice(per);
    return along;
  };
  const one = trim(a, b);
  const two = trim(b, a);
  if (one.length < 2 || two.length < 2) return false;
  for (let i = 1; i < one.length; i += 1) for (let j = 1; j < two.length; j += 1) if (properlyCross(one[i - 1], one[i], two[j - 1], two[j])) return false;
  const longest = (line: Point[], other: Point[]) => {
    let run = 0;
    let most = 0;
    for (let index = 1; index < line.length; index += 1) {
      if (polylineDistance(line[index], other) < within && polylineDistance(line[index - 1], other) < within) {
        run += Math.hypot(line[index].x - line[index - 1].x, line[index].y - line[index - 1].y);
        most = Math.max(most, run);
      } else run = 0;
    }
    return most;
  };
  return Math.max(longest(one, two), longest(two, one)) > over;
}
