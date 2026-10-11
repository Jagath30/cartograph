/**
 * A trace into what GraphView draws (DD-18, FR-31, FR-45). Pure.
 *
 * Everything comes from the trace's own copy of the subgraph and its own
 * record of the routes (DD-05): no table, column, key or sentence is taken
 * from anywhere else, and none is composed here (DD-06).
 *
 * Each key between the tables shown becomes one edge, of the strongest
 * kind that is true of it:
 *
 *   actual       the SQL made it and the plan had not selected it (DD-13)
 *   used         the plan selected it and the SQL made it, whole or in part
 *   planned      the plan selected it; the SQL did not make it, or no SQL
 *                was checked
 *   alternative  it is on a route the choice was between: tied with the
 *                one chosen, or set aside by a declared preference
 *   other        any other key between these tables
 *
 * `loud` is read from the warnings as stored: an edge is loud when a
 * warning whose `loud` is true names it. Nothing is worked out again from
 * a warning's state.
 */
import type { ActualEdge, Join, SchemaEdge, TraceDocument } from "../trace/types";
import type { EdgeKind, GraphEdge, GraphModel, GraphNode } from "./model";

/** How many of the best-scored columns count as matched, beside those
 *  chosen for a term of the question. */
export const MATCHED_TOP = 5;

const STRENGTH: Record<EdgeKind, number> = { other: 0, alternative: 1, planned: 2, used: 3, actual: 4 };

type Key = Pick<SchemaEdge, "from_table" | "from_columns" | "to_table" | "to_columns">;

export function edgeId(key: Key): string {
  return `${key.from_table}.${key.from_columns.join("+")}>${key.to_table}.${key.to_columns.join("+")}`;
}

/** As a warning writes a join: `a.b = c.d`, and `and` between the pairs of a key. */
export function joinText(key: Key): string {
  return key.from_columns
    .map((column, index) => `${key.from_table}.${column} = ${key.to_table}.${key.to_columns[index]}`)
    .join(" and ");
}

/** The columns the question matched: the element chosen for each of its
 *  terms, and the best-scored few. By table. */
export function matchedColumns(trace: TraceDocument): Map<string, Set<string>> {
  const matched = new Map<string, Set<string>>();
  const add = (table: string, column: string) => {
    if (!matched.has(table)) matched.set(table, new Set());
    matched.get(table)!.add(column);
  };
  const retrieval = trace.retrieval;
  if (!retrieval) return matched;
  for (const term of retrieval.term_alternatives) {
    const [table, column] = term.chosen_element.split(".");
    if (column) add(table, column);
  }
  retrieval.candidates
    // A table among the candidates has no column.
    .filter((candidate) => candidate.column !== null)
    .sort((a, b) => a.rank - b.rank)
    .slice(0, MATCHED_TOP)
    .forEach((candidate) => add(candidate.table, candidate.column as string));
  return matched;
}

/** Whether a key is the join the SQL made, on any of its column pairs and
 *  whichever way round the SQL wrote it. */
function isJoin(key: Key, made: ActualEdge): boolean {
  return key.from_columns.some((column, index) => {
    const target = key.to_columns[index];
    const forward =
      made.left_table === key.from_table &&
      made.left_column === column &&
      made.right_table === key.to_table &&
      made.right_column === target;
    const backward =
      made.right_table === key.from_table &&
      made.right_column === column &&
      made.left_table === key.to_table &&
      made.left_column === target;
    return forward || backward;
  });
}

export function fromTrace(trace: TraceDocument): GraphModel {
  const subgraph = trace.subgraph;
  if (!subgraph) return { nodes: [], edges: [], undrawn: [] };
  const paths = trace.paths;
  const matched = matchedColumns(trace);

  const nodes: GraphNode[] = subgraph.nodes.map((node) => ({
    id: node.table,
    readable: node.readable,
    anchor: node.anchor,
    inTree: node.in_tree,
    columns: node.columns.map((column) => ({
      name: column.name,
      readable: column.readable,
      dataType: column.data_type,
      primaryKey: column.primary_key,
      foreignKey: column.foreign_key,
    })),
    matched: node.columns.filter((column) => matched.get(node.table)?.has(column.name)).map((column) => column.name),
  }));
  const shown = new Map(subgraph.nodes.map((node) => [node.table, new Set(node.columns.map((column) => column.name))]));

  const edges = new Map<string, GraphEdge>();
  for (const key of subgraph.edges) {
    edges.set(edgeId(key), {
      id: edgeId(key),
      kind: "other",
      fromTable: key.from_table,
      fromColumns: key.from_columns,
      toTable: key.to_table,
      toColumns: key.to_columns,
      description: null,
      status: null,
      source: key.source,
      loud: false,
    });
  }

  /** Raise an edge to a kind, never lower it; keep the first sentence given. */
  const mark = (key: Key, kind: EdgeKind, description: string | null, status: GraphEdge["status"] = null) => {
    const edge = edges.get(edgeId(key));
    // A key that is not in the trace's subgraph is not drawn (DD-05).
    if (!edge) return;
    if (STRENGTH[kind] > STRENGTH[edge.kind]) {
      edge.kind = kind;
      edge.status = status;
    } else if (kind === "alternative" && edge.kind === "alternative" && status === "tied") {
      // Tied on one route and set aside on another: it was a live choice.
      edge.status = "tied";
    }
    edge.description = edge.description ?? description;
  };

  /** A route of one join is told by its own sentence; a longer one, join by join. */
  const told = (joins: Join[], route: string, join: Join) => (joins.length === 1 ? route : join.description);

  const undrawn: string[] = [];
  if (paths) {
    const sentences = new Map<string, string>();
    for (const attachment of paths.attachments) {
      for (const join of attachment.selected?.joins ?? []) {
        sentences.set(edgeId(join), told(attachment.selected!.joins, attachment.selected!.description, join));
      }
    }
    for (const selected of paths.selected_edges) {
      const made = selected.use === "present" || selected.use === "partial";
      mark(selected, made ? "used" : "planned", sentences.get(edgeId(selected)) ?? null);
    }
    for (const attachment of paths.attachments) {
      for (const alternative of attachment.alternatives) {
        for (const join of alternative.joins) {
          mark(join, "alternative", told(alternative.joins, alternative.description, join), alternative.status);
        }
      }
    }
    for (const made of paths.actual_edges.filter((edge) => edge.foreign)) {
      const key = subgraph.edges.find((candidate) => isJoin(candidate, made));
      if (key) {
        mark(key, "actual", null);
        continue;
      }
      // Not a key of the schema shown: a join the SQL made on columns of
      // its own choosing. Drawn between them when both are there to draw.
      const text = `${made.left_table}.${made.left_column} = ${made.right_table}.${made.right_column}`;
      const drawable =
        shown.get(made.left_table)?.has(made.left_column) && shown.get(made.right_table)?.has(made.right_column);
      if (!drawable) {
        undrawn.push(text);
        continue;
      }
      const free: Key = {
        from_table: made.left_table,
        from_columns: [made.left_column],
        to_table: made.right_table,
        to_columns: [made.right_column],
      };
      edges.set(edgeId(free), {
        id: edgeId(free),
        kind: "actual",
        fromTable: free.from_table,
        fromColumns: free.from_columns,
        toTable: free.to_table,
        toColumns: free.to_columns,
        description: null,
        status: null,
        source: null,
        loud: false,
      });
    }

    const alarmed = new Set(
      paths.warnings.filter((warning) => warning.loud).flatMap((warning) => [...warning.joins, ...warning.other_joins]),
    );
    for (const edge of edges.values()) {
      edge.loud = alarmed.has(
        joinText({
          from_table: edge.fromTable,
          from_columns: edge.fromColumns,
          to_table: edge.toTable,
          to_columns: edge.toColumns,
        }),
      );
    }
  }

  return { nodes, edges: [...edges.values()], undrawn };
}
