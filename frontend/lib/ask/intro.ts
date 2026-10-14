/**
 * What the empty page says (polish B, its words as the owner corrected
 * them). Every sentence true of Cartograph as built: the joins include
 * five relationships declared by hand, so the step says the joins are
 * picked, not that they are the warehouse's own keys; and a language
 * model writes the SQL, which Cartograph then checks and runs.
 */
export const LEAD =
  "Cartograph answers plain-English questions about a retail data warehouse. It shows which tables it used, how it joined them, and any choice it made with no basis.";

export const STEPS = [
  "Finds the tables your question is about, out of the warehouse's 24.",
  "Picks the joins between them, and says which other routes it set aside, and why.",
  "Has a language model write the SQL, then checks it against the plan and runs it on a read-only connection.",
];

export const DATA =
  "The data is a synthetic TPC-DS retail warehouse: sales and returns through stores, catalogs and the web.";
