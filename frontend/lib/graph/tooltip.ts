/**
 * Where the tooltip beside the pointer goes (stop 3 of step 9, fix 2).
 * Pure.
 *
 * To the lower right of the pointer, and turned to the other side when it
 * would leave the canvas, so that it is always whole and never hides the
 * point being read.
 */
import type { Point } from "./nearest";

/** The gap between the pointer and the tooltip. */
export const TIP_GAP = 16;
/** The widest the tooltip is drawn. */
export const TIP_WIDTH = 360;
/** Room kept for its height when deciding whether it fits below. */
export const TIP_HEIGHT = 96;

export type Placement = { left: number; top: number };

/** `pointer` is within the canvas, measured from its top left. */
export function tooltipAt(pointer: Point, canvas: { width: number; height: number }): Placement {
  const right = pointer.x + TIP_GAP + TIP_WIDTH <= canvas.width;
  const below = pointer.y + TIP_GAP + TIP_HEIGHT <= canvas.height;
  const left = right ? pointer.x + TIP_GAP : Math.max(0, pointer.x - TIP_GAP - TIP_WIDTH);
  const top = below ? pointer.y + TIP_GAP : Math.max(0, pointer.y - TIP_GAP - TIP_HEIGHT);
  return { left, top };
}
