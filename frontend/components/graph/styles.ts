/**
 * How each kind of join is drawn (DD-18). One table, read by the edges and
 * by the legend, so that the legend cannot disagree with the picture.
 */
import type { GraphEdge } from "@/lib/graph/model";

export type LineStyle = {
  key: string;
  label: string;
  stroke: string;
  width: number;
  dash?: string;
  opacity: number;
};

const INK = "#1d4ed8";
const GREY = "#6b7280";

export const AMBER = "#f59e0b";

export const LINES: Record<string, LineStyle> = {
  used: { key: "used", label: "Used by the query", stroke: INK, width: 3.5, opacity: 1 },
  planned: { key: "planned", label: "Planned, and not needed by the query", stroke: INK, width: 1.5, opacity: 0.6 },
  tied: { key: "tied", label: "Equally valid, not taken", stroke: GREY, width: 1.75, dash: "7 5", opacity: 0.85 },
  // The quietest line drawn: it was set aside for a reason, and it stays
  // in the picture all the same (DD-21).
  withdrawn: {
    key: "withdrawn",
    label: "Set aside by a declared preference",
    stroke: "#9ca3af",
    width: 1.25,
    dash: "1 5",
    opacity: 0.75,
  },
  actual: { key: "actual", label: "Joined by the query, outside the plan", stroke: "#c026d3", width: 3.5, opacity: 1 },
  other: { key: "other", label: "Another key between these tables", stroke: "#d1d5db", width: 1, dash: "1 3", opacity: 0.9 },
};

export function lineOf(edge: GraphEdge): LineStyle {
  if (edge.kind === "alternative") return edge.status === "withdrawn" ? LINES.withdrawn : LINES.tied;
  return LINES[edge.kind];
}

/** Which lines to draw above which. */
export const ORDER: Record<string, number> = { other: 0, withdrawn: 1, tied: 2, planned: 3, used: 4, actual: 5 };
