/**
 * Which line the pointer is on. Pure.
 *
 * Every line takes the pointer from HIT_RADIUS away on either side, so a
 * thin dotted line is as easy to point at as a heavy one. Bands that wide
 * overlap wherever two keys leave neighbouring rows, and there the line
 * NEAREST the pointer is meant, not whichever happens to be drawn on top.
 */
export type Point = { x: number; y: number };

/** A line as points along it, close enough together to stand for the curve. */
export type Sampled = { id: string; points: Point[] };

/** How far from a line the pointer still counts as on it, in screen pixels. */
export const HIT_RADIUS = 14;

export function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length = dx * dx + dy * dy;
  // How far along the segment the nearest point is, held to its two ends.
  const along = length === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length));
  return Math.hypot(p.x - (a.x + along * dx), p.y - (a.y + along * dy));
}

export function distanceToLine(p: Point, points: Point[]): number {
  if (points.length === 0) return Infinity;
  if (points.length === 1) return Math.hypot(p.x - points[0].x, p.y - points[0].y);
  let nearest = Infinity;
  for (let index = 1; index < points.length; index += 1) {
    nearest = Math.min(nearest, distanceToSegment(p, points[index - 1], points[index]));
  }
  return nearest;
}

/**
 * The line nearest the pointer, if one is within `within` of it. Of two
 * exactly as near, the later: it is the one drawn on top.
 */
export function nearestLine(p: Point, lines: Sampled[], within: number = HIT_RADIUS): string | null {
  let found: string | null = null;
  let nearest = Infinity;
  for (const line of lines) {
    const distance = distanceToLine(p, line.points);
    if (distance <= within && distance <= nearest) {
      found = line.id;
      nearest = distance;
    }
  }
  return found;
}
