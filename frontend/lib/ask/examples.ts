/**
 * The example questions (FR-44). Written for the demonstration; none is
 * from the evaluation set or the held-out set.
 *
 * The first two raise an arbitrary choice where the two routes give
 * different answers, proved with hand-written read-only SQL on the scale
 * factor 1 warehouse (CHECKPOINTS.md, step 9):
 *
 *   gender   by the billed customer's demographics women spent more on
 *            the web in 2002 (183.2M against 178.6M); by the shipped-to
 *            customer's, men (183.2M against 178.7M).
 *   states   by billing or by shipping address the same five states lead,
 *            and every amount differs, Virginia's by 7%.
 *
 * The last two are development questions of step 7: one that needs no
 * join and raises nothing, and one the warehouse cannot answer.
 */
export type Example = { question: string; shows: string };

export const EXAMPLES: Example[] = [
  { question: "Which gender spent more on the web in the year 2002?", shows: "a choice made with no basis" },
  { question: "Which five states spent the most on the web?", shows: "a choice made with no basis" },
  { question: "How many customers were born in each country?", shows: "a quiet answer" },
  { question: "Which of our suppliers deliver late most often?", shows: "an honest decline" },
];

/** The longest question the API takes (app/api_models.py). */
export const MAX_QUESTION = 500;
