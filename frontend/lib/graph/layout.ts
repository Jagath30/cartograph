/**
 * Where everything goes. Pure: a model and what the reader has opened in,
 * positions out. A node's size is computed from fixed row heights and not
 * measured, so the layout needs no browser and is tested without one.
 */
import dagre from "@dagrejs/dagre";

import type { GraphColumn, GraphEdge, GraphModel, GraphNode } from "./model";

export const NODE_WIDTH = 280;
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
  /** Keys between these tables that are not drawn in this view. */
  otherKeys: number;
  width: number;
  height: number;
};

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
  graph.setGraph({ rankdir: "LR", nodesep: 36, ranksep: 140, marginx: 24, marginy: 24 });
  graph.setDefaultEdgeLabel(() => ({}));

  const listed = new Map<string, GraphColumn[]>();
  for (const node of model.nodes) {
    const columns = listedColumns(node, drawn, view.expanded.has(node.id));
    listed.set(node.id, columns);
    graph.setNode(node.id, { width: NODE_WIDTH, height: nodeHeight(columns.length) });
  }
  for (const edge of drawn) {
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
  const size = graph.graph();
  return {
    nodes,
    edges: drawn,
    otherKeys: model.edges.length - drawn.length,
    width: size.width ?? 0,
    height: size.height ?? 0,
  };
}
