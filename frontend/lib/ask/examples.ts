/**
 * The example questions (FR-44).
 *
 * Asked live in chosen mode at stop F3 of step 10b (25 October 2026), each
 * once; the replies are pinned in backend/tests/test_examples_chosen.py.
 *
 *   catalog  the original set's Q3. The model, asked which address the
 *            question means, named neither, so the choice was the
 *            alphabet's and is flagged. It replaced the gender question
 *            (ruling 4 at stop F1): asked at F3, the model named the
 *            billed customer's demographics, so that question no longer
 *            showed a choice made with no basis. The gender totals stay
 *            pinned by hand-written SQL (backend/tests/test_demo_questions.py).
 *   states   the model named neither address. By billing or by shipping
 *            address the same five states lead, and every amount
 *            differs, Virginia's by 7% (CHECKPOINTS.md, step 9).
 *
 * The last two are development questions of step 7: one that needs no
 * join and raises nothing, and one the warehouse cannot answer, now
 * declined at linking.
 */
export type Example = { question: string; shows: string };

export const EXAMPLES: Example[] = [
  { question: "Where are our catalog customers located?", shows: "a choice made with no basis" },
  { question: "Which five states spent the most on the web?", shows: "a choice made with no basis" },
  { question: "How many customers were born in each country?", shows: "a quiet answer" },
  { question: "Which of our suppliers deliver late most often?", shows: "an honest decline" },
];

/** The longest question the API takes (app/api_models.py). */
export const MAX_QUESTION = 500;
