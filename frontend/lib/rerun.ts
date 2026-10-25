/**
 * Running a stored answer's SQL again (item 88). Pure.
 */
import { formatWhen } from "./ask/format";
import type { QueryOut } from "./trace/types";

export const RERUN_LABEL = "Run this SQL again";

/** An answer holds SQL to run again if the model wrote any. */
export function canRerun(result: QueryOut): boolean {
  const trace = result.trace;
  return Boolean(trace.execution?.sql) || (trace.generation?.attempts ?? []).some((attempt) => Boolean(attempt.sql));
}

/** The rows came from the data as it is now, not as it was when asked. */
export function asOfNow(ranAt: string): string {
  return `This is the data as of now (${formatWhen(ranAt)}), not as of the question.`;
}
