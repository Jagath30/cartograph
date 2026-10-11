/** Which line the pointer is on (stop 2 of step 9, ruling 1). */
import { describe, expect, test } from "vitest";

import { HIT_RADIUS, distanceToLine, distanceToSegment, nearestLine, type Sampled } from "./nearest";

const A = { x: 0, y: 0 };
const B = { x: 100, y: 0 };

describe("the distance from a point to a segment", () => {
  test("beside it: straight across", () => {
    expect(distanceToSegment({ x: 50, y: 7 }, A, B)).toBeCloseTo(7);
    expect(distanceToSegment({ x: 50, y: -7 }, A, B)).toBeCloseTo(7);
    expect(distanceToSegment({ x: 50, y: 0 }, A, B)).toBe(0);
  });

  test("past an end: to that end, not to the line carried on", () => {
    expect(distanceToSegment({ x: 103, y: 4 }, A, B)).toBeCloseTo(5);
    expect(distanceToSegment({ x: -3, y: 4 }, A, B)).toBeCloseTo(5);
  });

  test("a segment of no length is a point", () => {
    expect(distanceToSegment({ x: 3, y: 4 }, A, A)).toBeCloseTo(5);
  });

  test("a slanted one", () => {
    expect(distanceToSegment({ x: 0, y: 10 }, A, { x: 10, y: 10 })).toBeCloseTo(Math.SQRT2 * 5);
  });
});

describe("the distance to a line of several points", () => {
  const bent = [A, B, { x: 100, y: 100 }];
  test("is to its nearest stretch", () => {
    expect(distanceToLine({ x: 50, y: 6 }, bent)).toBeCloseTo(6);
    expect(distanceToLine({ x: 94, y: 50 }, bent)).toBeCloseTo(6);
  });
  test("one point, and none", () => {
    expect(distanceToLine({ x: 3, y: 4 }, [A])).toBeCloseTo(5);
    expect(distanceToLine({ x: 3, y: 4 }, [])).toBe(Infinity);
  });
});

describe("the line the pointer is on", () => {
  // Two keys leaving neighbouring rows, 22 pixels apart, towards one target.
  const upper: Sampled = { id: "upper", points: [{ x: 0, y: 0 }, { x: 200, y: 0 }] };
  const lower: Sampled = { id: "lower", points: [{ x: 0, y: 22 }, { x: 200, y: 22 }] };

  test("a line takes the pointer from 14 pixels away, and no further", () => {
    expect(HIT_RADIUS).toBe(14);
    expect(nearestLine({ x: 100, y: -14 }, [upper])).toBe("upper");
    expect(nearestLine({ x: 100, y: -14.5 }, [upper])).toBeNull();
    expect(nearestLine({ x: 100, y: 9 }, [upper], 8)).toBeNull();
  });

  test("where two bands overlap the nearer line is meant, whichever is drawn on top", () => {
    expect(nearestLine({ x: 100, y: 21 }, [upper, lower])).toBe("lower");
    expect(nearestLine({ x: 100, y: 21 }, [lower, upper])).toBe("lower");
    expect(nearestLine({ x: 100, y: 2 }, [upper, lower])).toBe("upper");
    expect(nearestLine({ x: 100, y: 2 }, [lower, upper])).toBe("upper");
    expect(nearestLine({ x: 100, y: 12 }, [upper, lower])).toBe("lower");
    expect(nearestLine({ x: 100, y: 10 }, [upper, lower])).toBe("upper");
  });

  test("exactly between two, the later: it is the one on top", () => {
    expect(nearestLine({ x: 100, y: 11 }, [upper, lower])).toBe("lower");
    expect(nearestLine({ x: 100, y: 11 }, [lower, upper])).toBe("upper");
  });

  test("nothing near, or nothing drawn", () => {
    expect(nearestLine({ x: 100, y: 80 }, [upper, lower])).toBeNull();
    expect(nearestLine({ x: 100, y: 0 }, [])).toBeNull();
  });
});
