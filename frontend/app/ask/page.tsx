"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { GraphView } from "@/components/graph/GraphView";
import { api, ApiError } from "@/lib/api";
import { fromTrace } from "@/lib/graph/fromTrace";
import { READABLE_VERSIONS, type QueryOut } from "@/lib/trace/types";

/**
 * /ask. AT STOP 2 OF STEP 9 this page does one thing: `?id=<query id>`
 * reads a stored query through the API and draws its graph. The question
 * box, the stage indicator, the results and the trace panel arrive next.
 */
function Stored() {
  const id = useSearchParams().get("id");
  const [found, setFound] = useState<QueryOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let current = true;
    api
      .query(id)
      .then((body) => current && setFound(body))
      .catch((cause: unknown) => {
        if (!current) return;
        setError(cause instanceof ApiError ? `${cause.status} ${cause.code}: ${cause.message}` : String(cause));
      });
    return () => {
      current = false;
    };
  }, [id]);

  if (!id) {
    return <p className="mt-6 text-sm text-gray-600">Add ?id= and the id of a stored query to draw its graph.</p>;
  }
  if (error) {
    return <p className="mt-6 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>;
  }
  if (!found) return <p className="mt-6 text-sm text-gray-500">reading the stored query&hellip;</p>;

  const trace = found.trace;
  if (!READABLE_VERSIONS.includes(trace.trace_version)) {
    return (
      <p className="mt-6 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
        This trace is version {trace.trace_version}, and this page draws versions {READABLE_VERSIONS.join(" and ")}.
      </p>
    );
  }
  return (
    <>
      <p className="mt-6 text-lg">{trace.question}</p>
      <p className="mt-1 text-xs text-gray-500">
        stored {trace.created_at} &middot; {found.status} &middot; trace version {trace.trace_version}
      </p>
      <div className="mt-6">
        {trace.subgraph ? (
          <GraphView model={fromTrace(trace)} />
        ) : (
          <p className="text-sm text-gray-600">This trace holds no subgraph, so there is nothing to draw.</p>
        )}
      </div>
    </>
  );
}

export default function Ask() {
  return (
    <main className="mx-auto max-w-[1240px] p-8 font-sans">
      <h1 className="text-xl font-semibold">Ask a question</h1>
      {/* useSearchParams needs a boundary for the production build. */}
      <Suspense fallback={<p className="mt-6 text-sm text-gray-500">&hellip;</p>}>
        <Stored />
      </Suspense>
    </main>
  );
}
