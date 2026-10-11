/**
 * What GraphView draws (DD-18): tables as nodes with their columns listed
 * inside, keys as edges. Plain data, made from a trace by `fromTrace` and
 * from nothing else: never from the live schema (DD-05, NFR-18).
 */

/**
 * used         selected by the system, and made by the SQL that answered
 * planned      selected, and not made by the SQL (or no SQL was checked)
 * alternative  a route the choice was between, and not the one chosen
 * actual       a join the SQL made that the plan did not select (DD-13)
 * other        any other key between the tables shown; hidden by default
 */
export type EdgeKind = "used" | "planned" | "alternative" | "actual" | "other";

export type GraphColumn = {
  name: string;
  readable: string;
  dataType: string;
  primaryKey: boolean;
  foreignKey: boolean;
};

export type GraphNode = {
  /** The table's identifier. */
  id: string;
  readable: string;
  anchor: boolean;
  /** False for a table shown only because a tied route passes through it. */
  inTree: boolean;
  columns: GraphColumn[];
  /** Columns the question matched; listed even when the node is collapsed. */
  matched: string[];
};

export type GraphEdge = {
  id: string;
  kind: EdgeKind;
  /** The table that holds the key, and the table it points at. */
  fromTable: string;
  fromColumns: string[];
  toTable: string;
  toColumns: string[];
  /** The stored plain-English sentence. Null where the trace holds none. */
  description: string | null;
  /** For an alternative: tied with the route chosen, or set aside by a
   *  declared preference. */
  status: "tied" | "withdrawn" | null;
  source: "catalog" | "overlay" | null;
  /** A loud warning is about this join. */
  loud: boolean;
};

export type GraphModel = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Joins the SQL made that cannot be drawn, because a table of theirs
   *  is not in the trace's subgraph. Listed in words, never given a node. */
  undrawn: string[];
};

/** `table.column = table.column`, and so on for a key of several columns. */
export function identifiers(edge: GraphEdge): string {
  return edge.fromColumns
    .map((column, index) => `${edge.fromTable}.${column} = ${edge.toTable}.${edge.toColumns[index]}`)
    .join(" and ");
}
