/**
 * The words of the history list (step 11, FR-34). Pure.
 */
import type { Outcome } from "./trace/types";

/** How a question ended, short enough for a list. */
export const OUTCOME_WORDS: Record<Outcome, string> = {
  answered: "Answered",
  not_answerable: "Declined",
  validation_failed: "Refused before it ran",
  model_failed: "No usable reply from the model",
  execution_failed: "Did not complete",
};

/** Where an answer rests on a choice the system had no basis for (DD-21,
 *  FR-45): the amber block's warning, as a list can carry it. */
export const MARK = "A choice had no basis";
