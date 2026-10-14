"use client";

import { BaseEdge, type Edge, type EdgeProps } from "@xyflow/react";

import type { GraphEdge } from "@/lib/graph/model";

import { AMBER, lineOf } from "./styles";

export type KeyEdgeData = {
  edge: GraphEdge;
  /** The line's path, routed round the tables (lib/graph/router.ts). */
  d: string;
  /** Pointed at, or pinned. */
  lit: boolean;
  /** Another line is lit. */
  dimmed: boolean;
};

export type KeyEdgeType = Edge<KeyEdgeData, "key">;

/**
 * One join. Its kind decides the line (styles.ts); a join a loud warning
 * is about carries an amber band beneath it, the colour of the block at
 * the head of the panel. Its path is the router's, not React Flow's: a
 * line goes round the tables it does not join. It takes no pointer itself:
 * GraphView works out which line the pointer is nearest
 * (lib/graph/nearest.ts).
 */
export function KeyEdge(props: EdgeProps<KeyEdgeType>) {
  const { data } = props;
  if (!data) return null;
  const path = data.d;
  const line = lineOf(data.edge);
  const width = data.lit ? line.width + 2 : line.width;
  const opacity = data.lit ? 1 : data.dimmed ? line.opacity * 0.3 : line.opacity;

  return (
    <>
      {data.edge.loud && (
        <path
          d={path}
          fill="none"
          stroke={AMBER}
          strokeWidth={width + 7}
          strokeOpacity={data.dimmed ? 0.12 : 0.35}
          strokeLinecap="round"
        />
      )}
      <BaseEdge
        path={path}
        interactionWidth={0}
        style={{
          stroke: line.stroke,
          strokeWidth: width,
          // A lit line is drawn whole, so that a dotted one can be followed.
          strokeDasharray: data.lit ? undefined : line.dash,
          opacity,
        }}
      />
    </>
  );
}
