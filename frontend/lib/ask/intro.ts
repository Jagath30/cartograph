/**
 * What the empty page says (polish B, its words as the owner corrected
 * them; step 10b's as approved at stop F1, v2.1 adopted). Every sentence
 * true of Cartograph as built: a language model names the tables; the
 * joins include five relationships declared by hand, so the step says the
 * joins are picked, not that they are the warehouse's own keys; where the
 * rules leave a choice open the model is asked; and the model writes the
 * SQL, which Cartograph then checks and runs.
 */
export const LEAD =
  "Cartograph answers plain-English questions about a retail data warehouse. It shows which tables it used, how it joined them, and how each choice of join was made: by a rule, by a language model reading your question, or with no basis.";

export const STEPS = [
  "Has a language model name the tables your question needs, out of the warehouse's 24.",
  "Picks the joins between them by its rules, asks the model where the rules leave a choice open, and says which other routes it set aside, and why.",
  "Has the model write the SQL, then checks it against the plan and runs it on a read-only connection.",
];

export const DATA =
  "The data is a synthetic TPC-DS retail warehouse: sales and returns through stores, catalogs and the web.";
