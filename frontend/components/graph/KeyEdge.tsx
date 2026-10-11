"use client";

import { BaseEdge, getBezierPath, type Edge, type EdgeProps } from "@xyflow/react";

import type { GraphEdge } from "@/lib/graph/model";

import { AMBER, lineOf } from "./styles";

export type KeyEdgeData = {
  edge: GraphEdge;
  hovered: boolean;
};

export type KeyEdgeType = Edge<KeyEdgeData, "key">;

/**
 * One join. Its kind decides the line (styles.ts); a join a loud warning
 * is about carries an amber band beneath it, the colour of the block at
 * the head of the panel.
 */
export function KeyEdge(props: EdgeProps<KeyEdgeType>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data } = props;
  if (!data) return null;
  const [path] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const line = lineOf(data.edge);
  const width = data.hovered ? line.width + 1.5 : line.width;

  return (
    <>
      {data.edge.loud && (
        <path d={path} fill="none" stroke={AMBER} strokeWidth={width + 7} strokeOpacity={0.35} strokeLinecap="round" />
      )}
      <BaseEdge
        path={path}
        interactionWidth={18}
        style={{
          stroke: line.stroke,
          strokeWidth: width,
          strokeDasharray: line.dash,
          opacity: data.hovered ? 1 : line.opacity,
        }}
      />
    </>
  );
}
