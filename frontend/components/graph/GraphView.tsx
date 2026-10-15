"use client";

import "@xyflow/react/dist/style.css";

import { useCallback, useMemo, useRef, useState, useSyncExternalStore, type MouseEvent } from "react";
import { ReactFlow, ReactFlowProvider, useReactFlow } from "@xyflow/react";

import { layout } from "@/lib/graph/layout";
import { identifiers, type GraphEdge, type GraphModel } from "@/lib/graph/model";
import { nearestLine, type Sampled } from "@/lib/graph/nearest";
import { pathOf } from "@/lib/graph/router";
import { TIP_WIDTH, tooltipAt, type Placement } from "@/lib/graph/tooltip";

import { KeyEdge, type KeyEdgeType } from "./KeyEdge";
import { AMBER, LINES, ORDER, lineOf, type LineStyle } from "./styles";
import { TableNode, type TableNodeType } from "./TableNode";

/**
 * Nothing to see: it covers everything drawn, so that fitting the picture
 * to its canvas keeps a line that goes above or below the tables. React
 * Flow fits to its nodes alone.
 */
function Bounds({ data }: { data: { width: number; height: number } }) {
  return <div style={{ width: data.width, height: data.height }} aria-hidden="true" />;
}

const nodeTypes = { table: TableNode, bounds: Bounds };
const edgeTypes = { key: KeyEdge };

/** The largest the picture is drawn when it is fitted to its canvas. */
const FIT_MAX_ZOOM = 1.15;

/** Below this width the page is one column, and the graph is dragged. */
const WIDE = "(min-width: 1024px)";
/** On a narrow screen the graph is never fitted smaller than this, so that
 *  its titles stay at 12 px and its column text at 10 px; it is dragged. */
const NARROW_MIN_ZOOM = 0.8;
const NARROW_HEIGHT = 460;

function useWide(): boolean {
  return useSyncExternalStore(
    (changed) => {
      const query = window.matchMedia(WIDE);
      query.addEventListener("change", changed);
      return () => query.removeEventListener("change", changed);
    },
    () => window.matchMedia(WIDE).matches,
    () => true,
  );
}

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

const TOOL =
  "flex h-8 w-8 items-center justify-center rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50";

/**
 * Zoom, beside the heading and never on the picture: the same three
 * buttons React Flow's own controls give, with the same names.
 */
function Zoom() {
  const flow = useReactFlow();
  return (
    <div role="toolbar" aria-label="Control Panel" className="flex items-center gap-1" data-graph="zoom">
      <button type="button" className={TOOL} aria-label="Zoom In" title="Zoom In" onClick={() => flow.zoomIn()}>
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M6 1v10M1 6h10" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      </button>
      <button type="button" className={TOOL} aria-label="Zoom Out" title="Zoom Out" onClick={() => flow.zoomOut()}>
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M1 6h10" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      </button>
      <button
        type="button"
        className={TOOL}
        aria-label="Fit View"
        title="Fit View"
        onClick={() => flow.fitView({ padding: 0, maxZoom: FIT_MAX_ZOOM })}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" fill="none">
          <path d="M1 4V1h3M8 1h3v3M11 8v3H8M4 11H1V8" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      </button>
    </div>
  );
}

/** The graph, with its heading and its zoom buttons above it. */
export function GraphView({ model, heading }: { model: GraphModel; heading: string }) {
  return (
    <ReactFlowProvider>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-600">{heading}</h2>
        <Zoom />
      </div>
      <Graph model={model} />
    </ReactFlowProvider>
  );
}

/**
 * The graph of one trace (DD-18, FR-31, FR-45). It draws the model it is
 * given and composes nothing: every sentence shown is one the trace stored.
 *
 * Pointing at a line shows what that join means; a click pins it until the
 * next click. The wheel scrolls the page, not the graph: zoom is Ctrl and
 * the wheel, a pinch, or the buttons above it.
 */
function Graph({ model }: { model: GraphModel }) {
  const wide = useWide();
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [showOther, setShowOther] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  // Where the tooltip beside the pointer goes, worked out as the pointer moves.
  const [tip, setTip] = useState<Placement | null>(null);
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
    if (!canvas.current || over.closest(".react-flow__node, .react-flow__panel")) return null;
    return nearestLine({ x: event.clientX, y: event.clientY }, sampled(canvas.current));
  }, []);
  const onMove = useCallback(
    (event: MouseEvent) => {
      setHovered(lineAt(event));
      const box = canvas.current?.getBoundingClientRect();
      setTip(
        box ? tooltipAt({ x: event.clientX - box.left, y: event.clientY - box.top }, { width: box.width, height: box.height }) : null,
      );
    },
    [lineAt],
  );
  const onClick = useCallback(
    (event: MouseEvent) => {
      const over = event.target as Element;
      if (over.closest(".react-flow__node, .react-flow__panel")) return;
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

  const nodes = useMemo(
    () => [
      {
        id: "__bounds",
        type: "bounds",
        position: { x: placed.bounds.x, y: placed.bounds.y },
        data: { width: placed.bounds.width, height: placed.bounds.height },
        draggable: false,
        selectable: false,
        focusable: false,
        style: { pointerEvents: "none" as const },
        zIndex: -1,
      },
      ...placed.nodes.map((at): TableNodeType => ({
        id: at.node.id,
        type: "table",
        position: { x: at.x, y: at.y },
        data: { placed: at, expanded: expanded.has(at.node.id), onToggle: toggle },
        draggable: false,
        selectable: false,
        // Above every line.
        zIndex: 100,
      })),
    ],
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
          data: { edge, d: pathOf(placed.routes.get(edge.id) ?? []), lit, dimmed: told !== null && !lit },
          zIndex: ORDER[lineOf(edge).key] + (lit ? 10 : 0),
          selectable: false,
        };
      }),
    [placed, hovered, pinned, told],
  );

  const routes = placed.edges.filter((edge) => edge.kind !== "other");
  const shown = told ? placed.edges.find((edge) => edge.id === told) : undefined;
  // The tooltip tells the line pointed at, beside the pointer; the strip
  // above tells it too, and keeps a pinned one.
  const pointed = hovered ? placed.edges.find((edge) => edge.id === hovered) : undefined;
  const legend = [...new Set(placed.edges.map((edge) => lineOf(edge).key))].sort((a, b) => ORDER[b] - ORDER[a]);
  const anyLoud = placed.edges.some((edge) => edge.loud);

  const describe = (edge: GraphEdge) => edge.description ?? identifiers(edge);

  return (
    <div data-graph="view">
      {!wide && (
        <p className="mb-2 text-xs text-gray-600" data-graph="narrow-note">
          Drag to move around the graph; it reads best on a wider screen.
        </p>
      )}
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
        className="relative w-full rounded-md border border-gray-200 bg-gray-50"
        // The picture's own shape, so that fitting it leaves no empty band;
        // on a narrow screen a fixed height, and the picture is dragged.
        style={{
          ...(wide ? { aspectRatio: `${placed.width} / ${placed.height}` } : { height: NARROW_HEIGHT }),
          // Never taller than the picture at its largest: one table alone
          // is not given a screen of empty canvas.
          maxHeight: Math.min(900, Math.ceil(placed.height * FIT_MAX_ZOOM)),
          cursor: hovered ? "pointer" : undefined,
        }}
        onMouseMove={onMove}
        onMouseLeave={() => {
          setHovered(null);
          setTip(null);
        }}
        onMouseDown={(event) => (pressed.current = { x: event.clientX, y: event.clientY })}
        onClick={onClick}
      >
        <ReactFlow
          // A new layout is a new picture: fit it afresh.
          key={`${wide}:${showOther}:${[...expanded].sort().join(",")}`}
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          // Wide: the whole picture, fitted. Narrow: the picture's top left,
          // where the plan starts, at a zoom no smaller than NARROW_MIN_ZOOM.
          fitView={wide}
          fitViewOptions={{ padding: 0, maxZoom: FIT_MAX_ZOOM }}
          onInit={(flow) => {
            if (wide || !canvas.current) return;
            const box = canvas.current.getBoundingClientRect();
            const { x, y, width, height } = placed.bounds;
            const zoom = Math.min(FIT_MAX_ZOOM, Math.max(NARROW_MIN_ZOOM, Math.min(box.width / width, box.height / height)));
            flow.setViewport({ x: -x * zoom, y: -y * zoom, zoom });
          }}
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
        />
        {pointed && tip && (
          <div
            role="tooltip"
            data-graph="tooltip"
            className="pointer-events-none absolute z-50 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-lg"
            style={{ left: tip.left, top: tip.top, maxWidth: TIP_WIDTH }}
          >
            <span className="font-medium">{lineOf(pointed).label}.</span> {describe(pointed)}
          </div>
        )}
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

      {routes.length > 0 && (
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
      )}
    </div>
  );
}
