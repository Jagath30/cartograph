"use client";

import "@xyflow/react/dist/style.css";

import { useCallback, useMemo, useState } from "react";
import { Controls, ReactFlow } from "@xyflow/react";

import { layout } from "@/lib/graph/layout";
import { identifiers, type GraphEdge, type GraphModel } from "@/lib/graph/model";

import { KeyEdge, type KeyEdgeType } from "./KeyEdge";
import { AMBER, LINES, ORDER, lineOf, type LineStyle } from "./styles";
import { TableNode, type TableNodeType } from "./TableNode";

const nodeTypes = { table: TableNode };
const edgeTypes = { key: KeyEdge };

function Sample({ line, loud = false }: { line: LineStyle; loud?: boolean }) {
  return (
    <svg width="44" height="14" className="shrink-0" aria-hidden>
      {loud && <line x1="2" y1="7" x2="42" y2="7" stroke={AMBER} strokeWidth={line.width + 7} strokeOpacity={0.35} />}
      <line
        x1="2"
        y1="7"
        x2="42"
        y2="7"
        stroke={line.stroke}
        strokeWidth={line.width}
        strokeDasharray={line.dash}
        opacity={line.opacity}
      />
    </svg>
  );
}

/**
 * The graph of one trace (DD-18, FR-31, FR-45). It draws the model it is
 * given and composes nothing: every sentence shown is one the trace stored.
 */
export function GraphView({ model }: { model: GraphModel }) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [showOther, setShowOther] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);

  const toggle = useCallback((table: string) => {
    setExpanded((before) => {
      const after = new Set(before);
      if (!after.delete(table)) after.add(table);
      return after;
    });
  }, []);

  const placed = useMemo(() => layout(model, { expanded, showOther }), [model, expanded, showOther]);
  const others = model.edges.filter((edge) => edge.kind === "other").length;

  const nodes: TableNodeType[] = useMemo(
    () =>
      placed.nodes.map((at) => ({
        id: at.node.id,
        type: "table",
        position: { x: at.x, y: at.y },
        data: { placed: at, expanded: expanded.has(at.node.id), onToggle: toggle },
        draggable: false,
        selectable: false,
      })),
    [placed, expanded, toggle],
  );

  const edges: KeyEdgeType[] = useMemo(
    () =>
      placed.edges.map((edge) => ({
        id: edge.id,
        type: "key",
        source: edge.fromTable,
        target: edge.toTable,
        sourceHandle: `out:${edge.fromColumns[0]}`,
        targetHandle: `in:${edge.toColumns[0]}`,
        data: { edge, hovered: hovered === edge.id },
        zIndex: ORDER[lineOf(edge).key] + (hovered === edge.id ? 10 : 0),
        selectable: false,
      })),
    [placed, hovered],
  );

  const routes = placed.edges.filter((edge) => edge.kind !== "other");
  const shown = hovered ? placed.edges.find((edge) => edge.id === hovered) : undefined;
  const legend = [...new Set(placed.edges.map((edge) => lineOf(edge).key))].sort((a, b) => ORDER[b] - ORDER[a]);
  const anyLoud = placed.edges.some((edge) => edge.loud);

  const describe = (edge: GraphEdge) => edge.description ?? identifiers(edge);

  return (
    <div>
      <div
        className="rounded-md border border-gray-200 bg-gray-50"
        style={{ height: Math.min(Math.max(placed.height + 40, 320), 680) }}
      >
        <ReactFlow
          // A new layout is a new picture: fit it afresh.
          key={`${expanded.size}:${showOther}:${[...expanded].join(",")}`}
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.08, maxZoom: 1 }}
          minZoom={0.2}
          maxZoom={1.5}
          nodesConnectable={false}
          nodesDraggable={false}
          elementsSelectable={false}
          zoomOnScroll={false}
          preventScrolling={false}
          onEdgeMouseEnter={(_, edge) => setHovered(edge.id)}
          onEdgeMouseLeave={() => setHovered(null)}
        >
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <p className="mt-2 min-h-[2.5rem] rounded bg-white px-3 py-2 text-sm text-gray-800" aria-live="polite">
        {shown ? (
          <>
            <span className="font-medium">{lineOf(shown).label}.</span> {describe(shown)}
            {shown.description && <span className="ml-2 font-mono text-xs text-gray-500">{identifiers(shown)}</span>}
          </>
        ) : (
          <span className="text-gray-500">Point at a line to read what that join means.</span>
        )}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-gray-700">
        {legend.map((key) => (
          <span key={key} className="flex items-center gap-2">
            <Sample line={LINES[key]} />
            {LINES[key].label}
          </span>
        ))}
        {anyLoud && (
          <span className="flex items-center gap-2">
            <Sample line={LINES.used} loud />A choice made with no basis
          </span>
        )}
        {others > 0 && (
          <button
            type="button"
            className="ml-auto rounded border border-gray-300 bg-white px-2 py-1 text-xs text-blue-700 hover:bg-gray-50"
            onClick={() => setShowOther((before) => !before)}
          >
            {showOther ? "Hide" : "Show"} {others} other {others === 1 ? "key" : "keys"} between these tables
          </button>
        )}
      </div>

      {model.undrawn.length > 0 && (
        <p className="mt-2 text-xs text-gray-700">
          Joined by the query and not drawn, because a table is not among those shown: {model.undrawn.join("; ")}.
        </p>
      )}

      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-gray-700">
          The {routes.length} {routes.length === 1 ? "join" : "joins"} drawn, in words
        </summary>
        <ul className="mt-2 space-y-2">
          {routes.map((edge) => (
            <li
              key={edge.id}
              className="flex items-start gap-3"
              onMouseEnter={() => setHovered(edge.id)}
              onMouseLeave={() => setHovered(null)}
            >
              <span className="mt-1">
                <Sample line={lineOf(edge)} loud={edge.loud} />
              </span>
              <span>
                <span className="text-gray-500">{lineOf(edge).label}.</span> {describe(edge)}
                {edge.description && (
                  <span className="block font-mono text-xs text-gray-500">{identifiers(edge)}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
