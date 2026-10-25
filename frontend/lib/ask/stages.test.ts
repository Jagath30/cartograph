/** The stage indicator (IR-12): an estimate from the known order, then the trace's own times. */
import { describe, expect, test } from "vitest";

import storedV2 from "../../fixtures/trace-v2-gender-diverged.json";
import storedV3 from "../../fixtures/trace-v3-catalog-customers.json";
import type { TraceDocument } from "../trace/types";
import { STAGES, STAGES_BEFORE_LINKING, stageAt, stagesOf, took } from "./stages";

describe("the stages", () => {
  test("are the pipeline's, in its order, and cover every timing a trace of version 3 records", () => {
    expect(STAGES.map((stage) => stage.keys)).toEqual([
      ["link"], ["locate"], ["choose"], ["prompt"], ["generate"], ["validate", "conform"], ["execute"],
    ]);
    const recorded = Object.keys((storedV3 as unknown as TraceDocument).timings).sort();
    expect(STAGES.flatMap((stage) => stage.keys).sort()).toEqual(recorded);
  });

  test("their words and typical times, as approved at stop F1", () => {
    expect(STAGES.map((stage) => [stage.label, stage.typicalMs])).toEqual([
      ["A language model is naming the tables", 1700],
      ["Finding the joins between them", 250],
      ["Asking the model about any choice the rules left open", 400],
      ["Writing out what the model will be shown", 100],
      ["The model is writing the SQL", 2300],
      ["Checking the SQL before it runs", 200],
      ["Running it on the warehouse, read-only", Infinity],
    ]);
  });

  test("a trace before version 3 is shown with the stages it ran, as it was", () => {
    expect(STAGES_BEFORE_LINKING.map((stage) => stage.keys)).toEqual([["locate"], ["prompt"], ["generate"], ["validate", "conform"], ["execute"]]);
    expect(STAGES_BEFORE_LINKING[0].label).toBe("Finding the tables and the joins between them");
    const recorded = Object.keys((storedV2 as unknown as TraceDocument).timings).sort();
    expect(STAGES_BEFORE_LINKING.flatMap((stage) => stage.keys).sort()).toEqual(recorded);
    expect([stagesOf(1), stagesOf(2), stagesOf(3)]).toEqual([STAGES_BEFORE_LINKING, STAGES_BEFORE_LINKING, STAGES]);
  });

  test("choosing, when it did not run, is not reached", () => {
    expect(took({ link: 1700, locate: 250, prompt: 1, generate: 2000 }, STAGES)[2].ms).toBeNull();
  });

  test("the last has no end: the clock cannot say the query has finished", () => {
    expect(STAGES[STAGES.length - 1].typicalMs).toBe(Infinity);
    expect(STAGES.slice(0, -1).every((stage) => Number.isFinite(stage.typicalMs) && stage.typicalMs > 0)).toBe(true);
  });
});

describe("the stage the clock says is running", () => {
  test("moves along as each typical time is used up", () => {
    expect(stageAt(0)).toBe(0);
    expect(stageAt(1699)).toBe(0);
    expect(stageAt(1700)).toBe(1);
    expect(stageAt(1949)).toBe(1);
    expect(stageAt(1950)).toBe(2);
    expect(stageAt(2349)).toBe(2);
    expect(stageAt(2350)).toBe(3);
    expect(stageAt(2450)).toBe(4);
    expect(stageAt(4749)).toBe(4);
    expect(stageAt(4750)).toBe(5);
    expect(stageAt(4950)).toBe(6);
  });

  test("and holds on the last, however long the answer takes", () => {
    expect(stageAt(90_000)).toBe(6);
    expect(stageAt(Number.MAX_SAFE_INTEGER)).toBe(6);
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
    const before = STAGES_BEFORE_LINKING;
    expect(took({ locate: 286, prompt: 0, generate: 1825, validate: 6, conform: 2, execute: 2433 }, before).map((s) => s.ms)).toEqual([
      286, 0, 1825, 8, 2433,
    ]);
    expect(took({ link: 1600, locate: 20, choose: 1900, prompt: 0, generate: 1825, validate: 6, conform: 2, execute: 40 }).map((s) => s.ms)).toEqual([
      1600, 20, 1900, 0, 1825, 8, 40,
    ]);
  });

  test("a stage the question never reached has no time, not a zero", () => {
    const before = STAGES_BEFORE_LINKING;
    expect(took({ locate: 300, prompt: 1, generate: 900 }, before).map((stage) => stage.ms)).toEqual([300, 1, 900, null, null]);
    expect(took({ locate: 300, prompt: 1, generate: 900, validate: 4 }, before)[3].ms).toBe(4);
    expect(took({}).every((stage) => stage.ms === null)).toBe(true);
    // The decline at linking: nothing after it ran.
    expect(took({ link: 1500 }).map((stage) => stage.ms)).toEqual([1500, null, null, null, null, null, null]);
  });

  test("the labels are the stages' own", () => {
    expect(took({}).map((stage) => stage.label)).toEqual(STAGES.map((stage) => stage.label));
  });
});
