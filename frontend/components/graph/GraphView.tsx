"use client";

import "@xyflow/react/dist/style.css";

import { useCallback, useMemo, useRef, useState, type MouseEvent } from "react";
import { Controls, ReactFlow } from "@xyflow/react";

import { layout } from "@/lib/graph/layout";
import { identifiers, type GraphEdge, type GraphModel } from "@/lib/graph/model";
import { nearestLine, type Sampled } from "@/lib/graph/nearest";

import { KeyEdge, type KeyEdgeType } from "./KeyEdge";
import { AMBER, LINES, ORDER, lineOf, type LineStyle } from "./styles";
import { TableNode, type TableNodeType } from "./TableNode";

const nodeTypes = { table: TableNode };
const edgeTypes = { key: KeyEdge };

/** How far apart, in the path's own units, the points are that stand for a curve. */
const SAMPLE_EVERY = 10;

/** Every line drawn, as points on the screen. */
function sampled(within: HTMLElement): Sampled[] {
  return [...within.querySelectorAll<SVGGElement>(".react-flow__edge")].flatMap((group) => {
    const path = group.querySelector<SVGPathElement>("path.react-flow__edge-path");
    const onScreen = path?.getScreenCTM();
    const id = group.dataset.id;
    if (!path || !onScreen || !id) return [];
    const length = path.getTotalLength();
    const steps = Math.max(2, Math.ceil(length / SAMPLE_EVERY));
    const points = Array.from({ length: steps + 1 }, (_, step) => {
      const at = path.getPointAtLength((length * step) / steps).matrixTransform(onScreen);
      return { x: at.x, y: at.y };
    });
    return [{ id, points }];
  });
}

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
 *
 * Pointing at a line shows what that join means; a click pins it until the
 * next click. The wheel scrolls the page, not the graph: zoom is Ctrl and
 * the wheel, a pinch, or the buttons.
 */
export function GraphView({ model }: { model: GraphModel }) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [showOther, setShowOther] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);

  const toggle = useCallback((table: string) => {
    setExpanded((before) => {
      const after = new Set(before);
      if (!after.delete(table)) after.add(table);
      return after;
    });
  }, []);
  const pin = useCallback((id: string) => setPinned((before) => (before === id ? null : id)), []);

  // Which line the pointer is on is worked out here and not left to each
  // line: their hit bands overlap, and the nearest is the one meant.
  const canvas = useRef<HTMLDivElement>(null);
  const pressed = useRef<{ x: number; y: number } | null>(null);
  const lineAt = useCallback((event: MouseEvent): string | null => {
    const over = event.target as Element;
    if (!canvas.current || over.closest(".react-flow__node, .react-flow__controls, .react-flow__panel")) return null;
    return nearestLine({ x: event.clientX, y: event.clientY }, sampled(canvas.current));
  }, []);
  const onMove = useCallback((event: MouseEvent) => setHovered(lineAt(event)), [lineAt]);
  const onClick = useCallback(
    (event: MouseEvent) => {
      const over = event.target as Element;
      if (over.closest(".react-flow__node, .react-flow__controls, .react-flow__panel")) return;
      // The end of a drag that moved the picture is not a click on it.
      const from = pressed.current;
      if (from && Math.hypot(event.clientX - from.x, event.clientY - from.y) > 4) return;
      const id = lineAt(event);
      setPinned((before) => (id === null || before === id ? null : id));
    },
    [lineAt],
  );

  const placed = useMemo(() => layout(model, { expanded, showOther }), [model, expanded, showOther]);
  const others = model.edges.filter((edge) => edge.kind === "other").length;
  // What the caption tells: the line pointed at, or else the one pinned.
  const told = hovered ?? pinned;

  const nodes: TableNodeType[] = useMemo(
    () =>
      placed.nodes.map((at) => ({
        id: at.node.id,
        type: "table",
        position: { x: at.x, y: at.y },
        data: { placed: at, expanded: expanded.has(at.node.id), onToggle: toggle },
        draggable: false,
        selectable: false,
        // Above every line: a join passes behind a table it does not touch.
        zIndex: 100,
      })),
    [placed, expanded, toggle],
  );

  const edges: KeyEdgeType[] = useMemo(
    () =>
      placed.edges.map((edge) => {
        const lit = edge.id === hovered || edge.id === pinned;
        return {
          id: edge.id,
          type: "key",
          source: edge.fromTable,
          target: edge.toTable,
          sourceHandle: `out:${edge.fromColumns[0]}`,
          targetHandle: `in:${edge.toColumns[0]}`,
          data: { edge, lit, dimmed: told !== null && !lit },
          zIndex: ORDER[lineOf(edge).key] + (lit ? 10 : 0),
          selectable: false,
        };
      }),
    [placed, hovered, pinned, told],
  );

  const routes = placed.edges.filter((edge) => edge.kind !== "other");
  const shown = told ? placed.edges.find((edge) => edge.id === told) : undefined;
  const legend = [...new Set(placed.edges.map((edge) => lineOf(edge).key))].sort((a, b) => ORDER[b] - ORDER[a]);
  const anyLoud = placed.edges.some((edge) => edge.loud);

  const describe = (edge: GraphEdge) => edge.description ?? identifiers(edge);

  return (
    <div data-graph="view">
      {/* Above the picture, so that it is in view while a line is pointed at. */}
      <p
        className="mb-2 min-h-[2.75rem] rounded border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800"
        aria-live="polite"
        data-graph="caption"
      >
        {shown ? (
          <>
            <span className="font-medium">{lineOf(shown).label}.</span> {describe(shown)}
            {shown.description && <span className="ml-2 font-mono text-xs text-gray-500">{identifiers(shown)}</span>}
            {pinned === shown.id && <span className="ml-2 text-xs text-gray-500">(pinned; click again to release)</span>}
          </>
        ) : (
          <span className="text-gray-500">Point at a line to read what that join means. Click to keep it shown.</span>
        )}
      </p>

      <div
        ref={canvas}
        className="w-full rounded-md border border-gray-200 bg-gray-50"
        // The picture's own shape, so that fitting it leaves no empty band.
        style={{
          aspectRatio: `${placed.width} / ${placed.height}`,
          minHeight: 280,
          maxHeight: 900,
          cursor: hovered ? "pointer" : undefined,
        }}
        onMouseMove={onMove}
        onMouseLeave={() => setHovered(null)}
        onMouseDown={(event) => (pressed.current = { x: event.clientX, y: event.clientY })}
        onClick={onClick}
      >
        <ReactFlow
          // A new layout is a new picture: fit it afresh.
          key={`${showOther}:${[...expanded].sort().join(",")}`}
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0, maxZoom: 1.15 }}
          minZoom={0.2}
          maxZoom={2}
          nodesConnectable={false}
          nodesDraggable={false}
          elementsSelectable={false}
          // The wheel belongs to the page. Zoom is Ctrl and the wheel, a
          // pinch (which a browser reports as the same), or the buttons.
          zoomOnScroll={false}
          panOnScroll={false}
          preventScrolling={false}
          zoomOnPinch
          zoomActivationKeyCode="Control"
          zoomOnDoubleClick={false}
        >
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

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
        <ul className="mt-2 space-y-1">
          {routes.map((edge) => (
            <li key={edge.id}>
              <button
                type="button"
                data-graph="route"
                aria-pressed={pinned === edge.id}
                className={`flex w-full items-start gap-3 rounded px-2 py-1 text-left hover:bg-gray-50 ${
                  pinned === edge.id ? "bg-blue-50 ring-1 ring-blue-200" : ""
                }`}
                onClick={() => pin(edge.id)}
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
              </button>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
