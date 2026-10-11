/** The stage indicator (IR-12): an estimate from the known order, then the trace's own times. */
import { describe, expect, test } from "vitest";

import storedV2 from "../../fixtures/trace-v2-gender-diverged.json";
import type { TraceDocument } from "../trace/types";
import { STAGES, stageAt, took } from "./stages";

describe("the stages", () => {
  test("are the pipeline's, in its order, and cover every timing a trace records", () => {
    expect(STAGES.map((stage) => stage.keys)).toEqual([["locate"], ["prompt"], ["generate"], ["validate", "conform"], ["execute"]]);
    const recorded = Object.keys((storedV2 as unknown as TraceDocument).timings).sort();
    expect(STAGES.flatMap((stage) => stage.keys).sort()).toEqual(recorded);
  });

  test("the last has no end: the clock cannot say the query has finished", () => {
    expect(STAGES[STAGES.length - 1].typicalMs).toBe(Infinity);
    expect(STAGES.slice(0, -1).every((stage) => Number.isFinite(stage.typicalMs) && stage.typicalMs > 0)).toBe(true);
  });
});

describe("the stage the clock says is running", () => {
  test("moves along as each typical time is used up", () => {
    expect(stageAt(0)).toBe(0);
    expect(stageAt(499)).toBe(0);
    expect(stageAt(500)).toBe(1);
    expect(stageAt(599)).toBe(1);
    expect(stageAt(600)).toBe(2);
    expect(stageAt(3099)).toBe(2);
    expect(stageAt(3100)).toBe(3);
    expect(stageAt(3299)).toBe(3);
    expect(stageAt(3300)).toBe(4);
  });

  test("and holds on the last, however long the answer takes", () => {
    expect(stageAt(90_000)).toBe(4);
    expect(stageAt(Number.MAX_SAFE_INTEGER)).toBe(4);
  });

  test("with stages of its own", () => {
    const two = [
      { keys: ["a"], label: "first", typicalMs: 10 },
      { keys: ["b"], label: "second", typicalMs: Infinity },
    ];
    expect([stageAt(9, two), stageAt(10, two), stageAt(1e9, two)]).toEqual([0, 1, 1]);
    expect(stageAt(5, [two[1]])).toBe(0);
  });
});

describe("what each stage took", () => {
  test("is read from the trace, the two checks together", () => {
    expect(took({ locate: 286, prompt: 0, generate: 1825, validate: 6, conform: 2, execute: 2433 }).map((s) => s.ms)).toEqual([
      286, 0, 1825, 8, 2433,
    ]);
  });

  test("a stage the question never reached has no time, not a zero", () => {
    expect(took({ locate: 300, prompt: 1, generate: 900 }).map((stage) => stage.ms)).toEqual([300, 1, 900, null, null]);
    expect(took({ locate: 300, prompt: 1, generate: 900, validate: 4 })[3].ms).toBe(4);
    expect(took({}).every((stage) => stage.ms === null)).toBe(true);
  });

  test("the labels are the stages' own", () => {
    expect(took({}).map((stage) => stage.label)).toEqual(STAGES.map((stage) => stage.label));
  });
});
