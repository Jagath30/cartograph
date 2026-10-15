/**
 * Every outcome, legible (FR-32, IR-05; stop 1, point e): the five the
 * pipeline ends in, and the ways no trace comes back at all.
 */
import { describe, expect, test } from "vitest";

import { ApiError } from "../api";
import type { Outcome, QueryOut } from "../trace/types";
import { endingOf, endingOfError } from "./outcome";

function result(status: Outcome, extra: object = {}, trace: object = {}): QueryOut {
  return {
    query_id: "q",
    status,
    code: status === "answered" ? null : `code_of_${status}`,
    message: status === "answered" ? null : `The API's message for ${status}.`,
    columns: [],
    rows: null,
    row_count: null,
    truncated: null,
    trace: {
      narrative: { found: "Found.", route: "Route.", not_taken: "Not taken.", sql: "What the query did.", result: "What came back." },
      validation: null,
      execution: null,
      ...trace,
    },
    ...extra,
  } as unknown as QueryOut;
}

describe("the five endings of the pipeline", () => {
  test("answered", () => {
    expect(endingOf(result("answered"))).toEqual({ tone: "answered", title: "Answered", lines: ["What came back."] });
  });

  test("not answerable: the API's message and what the trace says of the tables shown", () => {
    expect(endingOf(result("not_answerable"))).toEqual({
      tone: "declined",
      title: "Declined",
      lines: ["The API's message for not_answerable.", "What came back."],
    });
  });

  test("a decline's title does not repeat the narrative's opening", () => {
    expect(endingOf(result("not_answerable")).title).not.toBe("No answer was given");
  });

  test("validation failed: every finding of the validator is shown (FR-32)", () => {
    const refused = result("validation_failed", {}, {
      validation: { findings: ["the statement is not a SELECT: DROP", "unknown column store.nothing"] },
    });
    expect(endingOf(refused)).toEqual({
      tone: "refused",
      title: "The query was refused before it ran",
      lines: [
        "The API's message for validation_failed.",
        "What the query did.",
        "the statement is not a SELECT: DROP",
        "unknown column store.nothing",
      ],
    });
  });

  test("model failed", () => {
    expect(endingOf(result("model_failed"))).toEqual({
      tone: "failed",
      title: "The model gave no usable reply",
      lines: ["The API's message for model_failed.", "What the query did."],
    });
  });

  test("execution failed", () => {
    expect(endingOf(result("execution_failed"))).toEqual({
      tone: "failed",
      title: "The query was written and checked, and did not complete",
      lines: ["The API's message for execution_failed.", "What came back."],
    });
  });

  test("each has a title and a tone of its own kind, and something to read", () => {
    const all = (["answered", "not_answerable", "validation_failed", "model_failed", "execution_failed"] as const).map(
      (status) => endingOf(result(status)),
    );
    expect(new Set(all.map((ending) => ending.title)).size).toBe(5);
    expect(all.map((ending) => ending.tone)).toEqual(["answered", "declined", "refused", "failed", "failed"]);
    expect(all.every((ending) => ending.lines.length > 0)).toBe(true);
  });

  test("a line is said once, and an empty one not at all", () => {
    const same = result("not_answerable", { message: "What came back." });
    expect(endingOf(same).lines).toEqual(["What came back."]);
    expect(endingOf(result("model_failed", { message: null })).lines).toEqual(["What the query did."]);
    expect(endingOf(result("model_failed", { message: "  " })).lines).toEqual(["What the query did."]);
  });

  test("an outcome it does not know is said to be unknown, not passed off as an answer", () => {
    const strange = endingOf(result("something_else" as Outcome));
    expect(strange.tone).toBe("failed");
    expect(strange.title).toBe("An outcome this page does not know: something_else");
  });
});

describe("when no trace comes back", () => {
  test("422: what was wrong with the request, part by part", () => {
    const error = new ApiError(422, "invalid_request", "The request was not in the form this endpoint takes.", [
      { where: "body.question", why: "String should have at least 1 character" },
    ]);
    expect(endingOfError(error)).toEqual({
      tone: "refused",
      title: "The question could not be sent as written",
      lines: [
        "The request was not in the form this endpoint takes.",
        "body.question: String should have at least 1 character",
      ],
    });
  });

  test("503 not_ready", () => {
    expect(endingOfError(new ApiError(503, "not_ready", "No schema has been ingested yet."))).toEqual({
      tone: "failed",
      title: "The system is not ready to answer",
      lines: ["No schema has been ingested yet."],
    });
  });

  test("500 trace_not_persisted: the answer is not shown, and the query is named", () => {
    const error = new ApiError(500, "trace_not_persisted", "The trace could not be stored.", null, "abc-123");
    expect(endingOfError(error)).toEqual({
      tone: "failed",
      title: "The answer could not be kept, so it is not shown",
      lines: ["The trace could not be stored.", "Query abc-123."],
    });
  });

  test("500, any other fault", () => {
    expect(endingOfError(new ApiError(500, "internal_error", "A fault."))).toEqual({
      tone: "failed",
      title: "A fault on the server",
      lines: ["A fault."],
    });
  });

  test("404: a stored query that is not there", () => {
    expect(endingOfError(new ApiError(404, "not_found", "no query x is stored")).title).toBe("No such stored query");
  });

  test("no reply in time: the question may still have run (D-05)", () => {
    expect(endingOfError(new ApiError(0, "timeout", "No answer came back within 90 seconds."))).toEqual({
      tone: "failed",
      title: "No answer came back in time",
      lines: ["No answer came back within 90 seconds.", "The question may still have run, and its trace may have been stored."],
    });
  });

  test("the backend not reached at all", () => {
    expect(endingOfError(new ApiError(0, "unreachable", "The backend could not be reached."))).toEqual({
      tone: "failed",
      title: "The backend could not be reached",
      lines: ["The backend could not be reached."],
    });
  });

  test("any other status says which", () => {
    expect(endingOfError(new ApiError(418, "unknown", "I'm a teapot")).title).toBe("The request failed (418)");
    expect(endingOfError(new ApiError(502, "unknown", "Bad Gateway")).title).toBe("A fault on the server");
  });

  test("no ending is the tone of an answer", () => {
    for (const status of [0, 404, 418, 422, 500, 503]) {
      expect(endingOfError(new ApiError(status, "x", "m")).tone).not.toBe("answered");
    }
  });
});
