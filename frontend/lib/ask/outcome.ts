/**
 * Every outcome, legible (FR-32, IR-05). Pure.
 *
 * A question ends in one of two ways. The pipeline answered with a 200
 * and a trace, whatever became of the question: answered, declined,
 * refused, or failed. Or no trace came back at all: the request was
 * malformed, the system was not ready, a fault, or no reply.
 *
 * The TITLE is this page's own fixed label for the kind of ending. Every
 * other line is the API's: its message, the trace's narrative, the
 * validator's findings. Nothing here explains what the backend did not.
 */
import type { ApiError } from "../api";
import type { QueryOut } from "../trace/types";

export type Tone = "answered" | "declined" | "refused" | "failed";

export type Ending = {
  tone: Tone;
  title: string;
  lines: string[];
};

const said = (lines: (string | null | undefined)[]): string[] =>
  [...new Set(lines.filter((line): line is string => typeof line === "string" && line.trim() !== ""))];

export function endingOf(result: QueryOut): Ending {
  const trace = result.trace;
  switch (result.status) {
    case "answered":
      return { tone: "answered", title: "Answered", lines: said([trace.narrative.result]) };
    case "not_answerable":
      // The narrative's result opens "No answer was given."; the title
      // does not say it again.
      return { tone: "declined", title: "Declined", lines: said([result.message, trace.narrative.result]) };
    case "validation_failed":
      return {
        tone: "refused",
        title: "The query was refused before it ran",
        lines: said([result.message, trace.narrative.sql, ...(trace.validation?.findings ?? [])]),
      };
    case "model_failed":
      return {
        tone: "failed",
        title: "The model gave no usable reply",
        lines: said([result.message, trace.narrative.sql]),
      };
    case "execution_failed":
      return {
        tone: "failed",
        title: "The query was written and checked, and did not complete",
        lines: said([result.message, trace.narrative.result]),
      };
    default:
      return { tone: "failed", title: `An outcome this page does not know: ${result.status}`, lines: said([result.message]) };
  }
}

export function endingOfError(error: ApiError): Ending {
  const detail = (error.detail ?? []).map((entry) => [entry.where, entry.why].filter(Boolean).join(": "));
  if (error.code === "timeout") {
    return {
      tone: "failed",
      title: "No answer came back in time",
      lines: said([error.message, "The question may still have run, and its trace may have been stored."]),
    };
  }
  if (error.status === 0) {
    return { tone: "failed", title: "The backend could not be reached", lines: said([error.message]) };
  }
  if (error.status === 422) {
    return { tone: "refused", title: "The question could not be sent as written", lines: said([error.message, ...detail]) };
  }
  if (error.status === 404) {
    return { tone: "declined", title: "No such stored query", lines: said([error.message]) };
  }
  // The deployed demo's allowance (NFR-25, step 12): the API's message
  // says when asking opens again.
  if (error.code === "budget_spent") {
    return { tone: "refused", title: "The demo's budget for this month is spent", lines: said([error.message]) };
  }
  if (error.code === "daily_limit") {
    return { tone: "refused", title: "Today's questions are used up", lines: said([error.message]) };
  }
  if (error.status === 503) {
    return { tone: "failed", title: "The system is not ready to answer", lines: said([error.message, ...detail]) };
  }
  if (error.code === "trace_not_persisted") {
    return {
      tone: "failed",
      title: "The answer could not be kept, so it is not shown",
      lines: said([error.message, error.queryId ? `Query ${error.queryId}.` : null]),
    };
  }
  return {
    tone: "failed",
    title: error.status >= 500 ? "A fault on the server" : `The request failed (${error.status})`,
    lines: said([error.message, ...detail]),
  };
}
