/**
 * The stage indicator (IR-12). Pure.
 *
 * One request, one response (D-05): the server says nothing until it
 * answers, so while a question is in flight the stage shown is an
 * ESTIMATE, moved along by the clock from the known order of the stages
 * and what each typically takes. It is labelled as one. When the answer
 * arrives, the times the trace recorded replace it.
 */

export type Stage = {
  /** The keys of the trace's `timings` that this stage covers. */
  keys: string[];
  label: string;
  /** What it typically takes, in milliseconds. The last has no end. */
  typicalMs: number;
};

/** In the order the pipeline runs them. Typical times are the stored
 *  traces' of 10 and 11 October 2026, rounded. */
export const STAGES: Stage[] = [
  { keys: ["locate"], label: "Finding the tables and the joins between them", typicalMs: 500 },
  { keys: ["prompt"], label: "Writing out what the model will be shown", typicalMs: 100 },
  { keys: ["generate"], label: "The model is writing the SQL", typicalMs: 2500 },
  { keys: ["validate", "conform"], label: "Checking the SQL before it runs", typicalMs: 200 },
  { keys: ["execute"], label: "Running it on the warehouse, read-only", typicalMs: Infinity },
];

/** Which stage the clock says is running. Never past the last. */
export function stageAt(elapsedMs: number, stages: Stage[] = STAGES): number {
  let ends = 0;
  for (let index = 0; index < stages.length - 1; index += 1) {
    ends += stages[index].typicalMs;
    if (elapsedMs < ends) return index;
  }
  return stages.length - 1;
}

export type Took = { label: string; ms: number | null };

/** What each stage actually took, from the trace. A stage the question
 *  never reached has null, and is not given a time it did not take. */
export function took(timings: Record<string, number>, stages: Stage[] = STAGES): Took[] {
  return stages.map((stage) => {
    const present = stage.keys.filter((key) => key in timings);
    return { label: stage.label, ms: present.length ? present.reduce((sum, key) => sum + timings[key], 0) : null };
  });
}
