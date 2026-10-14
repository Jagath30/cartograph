/**
 * Where everything goes. Pure: a model and what the reader has opened in,
 * positions out. A node's size is computed from fixed row heights and not
 * measured, so the layout needs no browser and is tested without one.
 */
import dagre from "@dagrejs/dagre";

import type { EdgeKind, GraphColumn, GraphEdge, GraphModel, GraphNode } from "./model";
import { columnsOf, routeLines, type Box, type Line, type Point } from "./router";

export const NODE_WIDTH = 272;
export const HEADER_HEIGHT = 52;
export const ROW_HEIGHT = 24;
export const FOOTER_HEIGHT = 30;

export type View = {
  /** Tables whose every column is listed. */
  expanded: ReadonlySet<string>;
  /** Whether the keys that are on no route are drawn. */
  showOther: boolean;
};

export type PlacedNode = {
  node: GraphNode;
  x: number;
  y: number;
  width: number;
  height: number;
  /** The columns listed, in the table's own order. */
  columns: GraphColumn[];
  /** How many of the table's columns are not listed. */
  hidden: number;
};

export type Placed = {
  nodes: PlacedNode[];
  edges: GraphEdge[];
  /** Where each line goes, by edge id (lib/graph/router.ts). */
  routes: Map<string, Point[]>;
  /** Keys between these tables that are not drawn in this view. */
  otherKeys: number;
  /** Everything drawn, tables and lines: a line may pass above the
   *  highest table or below the lowest. */
  bounds: { x: number; y: number; width: number; height: number };
  width: number;
  height: number;
};

/** The joins that place the tables: the plan's own (the owner's ruling on
 *  the graph at step 9). Every other line is drawn, and moves no table. */
export const SHAPING: ReadonlySet<EdgeKind> = new Set<EdgeKind>(["used", "planned"]);

/** Where a row's line meets its table, measured from the table's top. */
export function rowCentre(index: number): number {
  return HEADER_HEIGHT + index * ROW_HEIGHT + ROW_HEIGHT / 2;
}

export function drawnEdges(model: GraphModel, view: View): GraphEdge[] {
  return model.edges.filter((edge) => view.showOther || edge.kind !== "other");
}

/**
 * The columns a node lists: every column when it is expanded; otherwise
 * the columns of the joins drawn, and those the question matched.
 */
export function listedColumns(node: GraphNode, drawn: GraphEdge[], expanded: boolean): GraphColumn[] {
  if (expanded) return node.columns;
  const keep = new Set(node.matched);
  for (const edge of drawn) {
    if (edge.fromTable === node.id) edge.fromColumns.forEach((name) => keep.add(name));
    if (edge.toTable === node.id) edge.toColumns.forEach((name) => keep.add(name));
  }
  return node.columns.filter((column) => keep.has(column.name));
}

export function nodeHeight(rows: number): number {
  return HEADER_HEIGHT + rows * ROW_HEIGHT + FOOTER_HEIGHT;
}

export function layout(model: GraphModel, view: View): Placed {
  const drawn = drawnEdges(model, view);
  const graph = new dagre.graphlib.Graph({ multigraph: true });
  // Left to right: the table that holds a key sits left of the one it
  // points at, so a fact table leads and its dimensions follow.
  graph.setGraph({ rankdir: "LR", nodesep: 22, ranksep: 84, marginx: 10, marginy: 10 });
  graph.setDefaultEdgeLabel(() => ({}));

  const listed = new Map<string, GraphColumn[]>();
  for (const node of model.nodes) {
    const columns = listedColumns(node, drawn, view.expanded.has(node.id));
    listed.set(node.id, columns);
    graph.setNode(node.id, { width: NODE_WIDTH, height: nodeHeight(columns.length) });
  }
  for (const edge of drawn.filter((edge) => SHAPING.has(edge.kind))) {
    graph.setEdge(edge.fromTable, edge.toTable, {}, edge.id);
  }
  dagre.layout(graph);

  const nodes = model.nodes.map((node) => {
    const at = graph.node(node.id);
    const columns = listed.get(node.id) ?? [];
    return {
      node,
      // dagre gives a node's centre.
      x: at.x - at.width / 2,
      y: at.y - at.height / 2,
      width: at.width,
      height: at.height,
      columns,
      hidden: node.columns.length - columns.length,
    };
  });
  const routes = routeAll(nodes, drawn);
  const bounds = boundsOf(nodes, routes);
  return {
    nodes,
    edges: drawn,
    routes,
    otherKeys: model.edges.length - drawn.length,
    bounds,
    width: bounds.width,
    height: bounds.height,
  };
}

/** Every drawn line's route. Brackets beside one column are numbered in
 *  the order of their edges' ids, so that they stand apart, the same way
 *  every time. */
export function routeAll(nodes: PlacedNode[], edges: GraphEdge[]): Map<string, Point[]> {
  const boxes = new Map<string, Box>(
    nodes.map((at) => [at.node.id, { id: at.node.id, x: at.x, y: at.y, width: at.width, height: at.height }]),
  );
  const columns = columnsOf([...boxes.values()]);
  const columnOf = (id: string) => columns.findIndex((column) => column.boxes.some((box) => box.id === id));
  const rowOf = (id: string, name: string) => {
    const at = nodes.find((placed) => placed.node.id === id)!;
    return at.y + rowCentre(at.columns.findIndex((column) => column.name === name));
  };
  const ordered = [...edges].sort((a, b) => a.id.localeCompare(b.id));
  const bracketsBeside = new Map<number, number>();
  for (const edge of ordered) {
    const column = columnOf(edge.fromTable);
    if (column === columnOf(edge.toTable)) bracketsBeside.set(column, (bracketsBeside.get(column) ?? 0) + 1);
  }
  const placed = new Map<number, number>();
  const lines: Line[] = ordered.map((edge) => {
    const column = columnOf(edge.fromTable);
    const sameColumn = column === columnOf(edge.toTable);
    const index = sameColumn ? (placed.get(column) ?? 0) : 0;
    if (sameColumn) placed.set(column, index + 1);
    return {
      id: edge.id,
      from: { box: boxes.get(edge.fromTable)!, y: rowOf(edge.fromTable, edge.fromColumns[0]) },
      to: { box: boxes.get(edge.toTable)!, y: rowOf(edge.toTable, edge.toColumns[0]) },
      bracket: sameColumn ? { index, count: bracketsBeside.get(column) ?? 1 } : undefined,
    };
  });
  const routes = routeLines(lines, columns);
  return routes;
}

function boundsOf(nodes: PlacedNode[], routes: Map<string, Point[]>) {
  const xs = nodes.flatMap((at) => [at.x, at.x + at.width]);
  const ys = nodes.flatMap((at) => [at.y, at.y + at.height]);
  for (const points of routes.values()) {
    xs.push(...points.map((point) => point.x));
    ys.push(...points.map((point) => point.y));
  }
  // The margin dagre leaves, kept all round.
  const margin = 10;
  const x = Math.min(...xs) - margin;
  const y = Math.min(...ys) - margin;
  return { x, y, width: Math.max(...xs) + margin - x, height: Math.max(...ys) + margin - y };
}
