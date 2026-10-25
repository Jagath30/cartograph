"use client";

import { CopyButton, ResultActions } from "@/components/ask/Actions";
import { Ending } from "@/components/ask/Ending";
import { ResultTable } from "@/components/ask/ResultTable";
import { Rerun } from "@/components/ask/Rerun";
import { AmberBlock, Disclosures, Narrative } from "@/components/ask/TracePanel";
import { GraphView } from "@/components/graph/GraphView";
import { formatWhen } from "@/lib/ask/format";
import { endingOf } from "@/lib/ask/outcome";
import { fromTrace } from "@/lib/graph/fromTrace";
import { canRerun } from "@/lib/rerun";
import { READABLE_VERSIONS, type QueryOut } from "@/lib/trace/types";

/**
 * One answer, in the order of DD-19: the amber block when a choice had no
 * basis; the plain-English account with the results beside it (FR-30);
 * the graph; and everything technical behind two disclosures.
 */
export function Answer({ result, stored }: { result: QueryOut; stored: boolean }) {
  const trace = result.trace;
  if (!READABLE_VERSIONS.includes(trace.trace_version)) {
    return (
      <Ending
        ending={{
          tone: "failed",
          title: `This trace is version ${trace.trace_version}`,
          lines: [`This page reads versions ${READABLE_VERSIONS.slice(0, -1).join(", ")} and ${READABLE_VERSIONS.at(-1)}.`],
        }}
      />
    );
  }
  const answered = result.status === "answered";
  return (
    <div className="mt-6 space-y-6" data-ask="answer">
      <div>
        <p className="text-xl">{trace.question}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-gray-500">
          <span>
            {stored ? "Read back from the store; asked" : "Asked"} {formatWhen(trace.created_at)}
          </span>
          <CopyButton
            label="Copy link"
            name="Copy link to this answer"
            text={() => `${window.location.origin}/ask?id=${result.query_id}`}
          />
        </p>
      </div>

      <AmberBlock trace={trace} />

      {/* Below 1024 px, one column, in reading order: the account, then the results. */}
      <div className="grid items-start gap-8 lg:grid-cols-2">
        <Narrative trace={trace} />
        <section data-ask="results">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-600">
              {answered ? "Results" : "What happened"}
            </h2>
            {answered && <ResultActions result={result} />}
          </div>
          <div className="mt-3 space-y-3">
            {!answered && <Ending ending={endingOf(result)} />}
            {/* A stored answer keeps no rows (DR-15): it offers to run its SQL again (item 88). */}
            {answered && !(stored && canRerun(result)) && <ResultTable result={result} />}
            {answered && <p className="text-[15px] text-gray-900">{trace.narrative.result}</p>}
            {stored && canRerun(result) && <Rerun stored={result} />}
          </div>
        </section>
      </div>

      {trace.subgraph && trace.subgraph.nodes.length > 0 && (
        <section data-ask="graph">
          <GraphView model={fromTrace(trace)} heading="The tables, and the joins between them" />
        </section>
      )}

      <Disclosures trace={trace} />
    </div>
  );
}
