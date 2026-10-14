/**
 * Cartograph's glyph: three points joined by two lines, a join path. One
 * drawing, read by the header's inline SVG and checked against the
 * favicon (app/icon.svg) by a test, so that the two cannot drift apart.
 *
 * In a 32 by 32 box: wide strokes and large points, legible at 16 pixels.
 */
export const VIEW = 32;
export const POINTS: readonly (readonly [number, number])[] = [
  [6, 23],
  [16, 9],
  [26, 23],
];
export const POINT_RADIUS = 4;
export const LINE_WIDTH = 3;
