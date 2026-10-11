/**
 * Which warnings show in the amber block (DD-19, DD-21, FR-45): the ones
 * the trace stored as loud, each in its plain sentence when it has one.
 */
import { describe, expect, test } from "vitest";

import storedV1 from "../../fixtures/trace-v1-days-of-the-week.json";
import storedV2 from "../../fixtures/trace-v2-gender-diverged.json";
import type { TraceDocument, Warning } from "../trace/types";
import { alarms, quietWarnings } from "./warnings";

const GENDER = storedV2 as unknown as TraceDocument;
const DAYS = storedV1 as unknown as TraceDocument;

function warning(extra: Partial<Warning>): Warning {
  return {
    code: "arbitrary_choice",
    text: "I had no basis for this choice.",
    about: [],
    joins: [],
    other_joins: [],
    state: "followed",
    loud: true,
    plain: "This answer uses the billing address.",
    ...extra,
  };
}

function withWarnings(warnings: Warning[]): TraceDocument {
  return { paths: { warnings } } as unknown as TraceDocument;
}

describe("on stored traces", () => {
  test("version 2: the loud warnings, in their plain sentences, in the trace's order", () => {
    const shown = alarms(GENDER);
    expect(shown.map((alarm) => alarm.from)).toEqual(["plain", "plain"]);
    expect(shown[0].sentence).toMatch(/^This answer uses the billed customer's demographics\. /);
    expect(shown[1].sentence).toMatch(/^This answer uses the web sale's date of sale, not /);
    expect(GENDER.paths!.warnings).toHaveLength(3);
    expect(quietWarnings(GENDER)).toHaveLength(1);
  });

  test("version 1 has no plain sentence, and falls back to the text it stored", () => {
    const shown = alarms(DAYS);
    expect(shown).toHaveLength(2);
    expect(shown.map((alarm) => alarm.from)).toEqual(["text", "text"]);
    expect(shown[0].sentence).toBe(DAYS.paths!.warnings[0].text);
    expect(shown[0].sentence).toMatch(/^I had no basis for this choice\./);
    expect(quietWarnings(DAYS)).toHaveLength(2);
  });
});

describe("which show", () => {
  test("only those stored as loud", () => {
    const trace = withWarnings([warning({ loud: false, plain: null }), warning({ plain: "Loud one." })]);
    expect(alarms(trace).map((alarm) => alarm.sentence)).toEqual(["Loud one."]);
    expect(quietWarnings(trace)).toHaveLength(1);
  });

  test("none: the block is absent and the panel is quiet", () => {
    expect(alarms(withWarnings([]))).toEqual([]);
    expect(alarms(withWarnings([warning({ loud: false }), warning({ loud: false })]))).toEqual([]);
    expect(alarms({ paths: null } as unknown as TraceDocument)).toEqual([]);
    expect(quietWarnings({ paths: null } as unknown as TraceDocument)).toEqual([]);
  });

  test("`loud` is read as stored: it is not worked out again from the state or the code", () => {
    for (const state of ["followed", "other_route_taken", "unknown", "not_used", "not_applicable"] as const) {
      expect(alarms(withWarnings([warning({ state, loud: true })]))).toHaveLength(1);
      expect(alarms(withWarnings([warning({ state, loud: false })]))).toHaveLength(0);
    }
    expect(alarms(withWarnings([warning({ code: "something_new", loud: true })]))).toHaveLength(1);
  });
});

describe("the sentence", () => {
  test("the plain one when there is one", () => {
    expect(alarms(withWarnings([warning({})]))[0]).toEqual({
      sentence: "This answer uses the billing address.",
      from: "plain",
      code: "arbitrary_choice",
    });
  });

  test("the stored text when plain is null, absent or empty", () => {
    const { plain: _dropped, ...withoutPlain } = warning({});
    void _dropped;
    for (const one of [warning({ plain: null }), withoutPlain as Warning, warning({ plain: "" }), warning({ plain: "   " })]) {
      expect(alarms(withWarnings([one]))[0]).toMatchObject({ sentence: "I had no basis for this choice.", from: "text" });
    }
  });
});
