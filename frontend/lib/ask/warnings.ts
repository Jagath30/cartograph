/**
 * What the amber block holds (DD-19, DD-21, FR-45). Pure.
 *
 * The block is the warnings whose stored `loud` is true, and no others.
 * Nothing is worked out again here from a warning's state or code: which
 * warnings interrupt the reader was decided once, by the backend, against
 * the joins the executed SQL made, and stored (DD-06).
 *
 * A loud warning is told in its plain sentence (trace_version 2). A trace
 * written at version 1 has none, and falls back to the Explainer's text.
 */
import type { TraceDocument, Warning } from "../trace/types";

export type Alarm = {
  sentence: string;
  /** Whether the sentence is the plain one or the Explainer's own. */
  from: "plain" | "text";
  code: string;
};

export function alarms(trace: TraceDocument): Alarm[] {
  return (trace.paths?.warnings ?? []).filter((warning) => warning.loud).map(alarm);
}

function alarm(warning: Warning): Alarm {
  const plain = typeof warning.plain === "string" && warning.plain.trim() !== "" ? warning.plain : null;
  return { sentence: plain ?? warning.text, from: plain ? "plain" : "text", code: warning.code };
}

/** The warnings that do not interrupt: for the detail, never for the block. */
export function quietWarnings(trace: TraceDocument): Warning[] {
  return (trace.paths?.warnings ?? []).filter((warning) => !warning.loud);
}
