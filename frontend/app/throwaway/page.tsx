"use client";

import { GraphView } from "@/components/graph/GraphView";
import { HANDMADE } from "@/lib/graph/handmade";

/**
 * THROWAWAY (R-05): a graph written by hand, to prove the graph library,
 * the automatic layout and the styling before the real panel is built.
 * Deleted once GraphView draws a stored trace.
 */
export default function Throwaway() {
  return (
    <main className="mx-auto max-w-[1240px] p-8 font-sans">
      <h1 className="text-xl font-semibold">Throwaway: a graph written by hand</h1>
      <p className="mt-1 text-sm text-gray-600">
        Five tables. The route chosen is solid; the route it was tied with is dashed. Nothing here comes from the
        API.
      </p>
      <div className="mt-6">
        <GraphView model={HANDMADE} />
      </div>
    </main>
  );
}
