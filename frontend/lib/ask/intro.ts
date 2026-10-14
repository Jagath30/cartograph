/**
 * What the empty page says (polish B). Every sentence true of Cartograph
 * as built; one the owner wrote is left out because it is not:
 *
 *   "Joins them by the warehouse's own keys, and says which routes it set
 *   aside, and why."
 *
 * Five of the 107 relationships the graph joins by are not the
 * warehouse's own keys: they are declared by hand in the overlay
 * (CHECKPOINTS.md, items 16 and 41), and plans use them (a return joined
 * to its sale). The steps are numbered as shown.
 */
export const LEAD =
  "Cartograph answers questions about a retail data warehouse in plain English. It shows which tables it used, how it joined them, and any choice it made with no basis.";

export const STEPS = [
  "Finds the tables your question is about, out of the warehouse's 24.",
  "Writes the SQL, checks it against its plan, and runs it on a read-only connection.",
];

export const DATA =
  "The data is a synthetic TPC-DS retail warehouse: sales and returns through stores, catalogs and the web.";
