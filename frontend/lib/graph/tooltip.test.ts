/** Where the tooltip beside the pointer goes (stop 3, fix 2). */
import { describe, expect, test } from "vitest";

import { TIP_GAP, TIP_HEIGHT, TIP_WIDTH, tooltipAt } from "./tooltip";

const CANVAS = { width: 1200, height: 600 };

describe("the tooltip beside the pointer", () => {
  test("goes to the lower right when it fits", () => {
    expect(tooltipAt({ x: 100, y: 100 }, CANVAS)).toEqual({ left: 100 + TIP_GAP, top: 100 + TIP_GAP });
  });

  test("turns to the left near the right edge, and up near the bottom", () => {
    expect(tooltipAt({ x: 1000, y: 100 }, CANVAS)).toEqual({ left: 1000 - TIP_GAP - TIP_WIDTH, top: 116 });
    expect(tooltipAt({ x: 100, y: 550 }, CANVAS)).toEqual({ left: 116, top: 550 - TIP_GAP - TIP_HEIGHT });
    expect(tooltipAt({ x: 1000, y: 550 }, CANVAS)).toEqual({
      left: 1000 - TIP_GAP - TIP_WIDTH,
      top: 550 - TIP_GAP - TIP_HEIGHT,
    });
  });

  test("the edges exactly: it fits when it just fits", () => {
    const x = CANVAS.width - TIP_GAP - TIP_WIDTH;
    expect(tooltipAt({ x, y: 0 }, CANVAS).left).toBe(x + TIP_GAP);
    expect(tooltipAt({ x: x + 1, y: 0 }, CANVAS).left).toBe(x + 1 - TIP_GAP - TIP_WIDTH);
    const y = CANVAS.height - TIP_GAP - TIP_HEIGHT;
    expect(tooltipAt({ x: 0, y }, CANVAS).top).toBe(y + TIP_GAP);
    expect(tooltipAt({ x: 0, y: y + 1 }, CANVAS).top).toBe(y + 1 - TIP_GAP - TIP_HEIGHT);
  });

  test("never leaves the canvas at its top or left, on a canvas too small for it", () => {
    expect(tooltipAt({ x: 50, y: 40 }, { width: 200, height: 100 })).toEqual({ left: 0, top: 0 });
  });
});
