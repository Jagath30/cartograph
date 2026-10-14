"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { Ending } from "@/components/ask/Ending";
import { ExampleQuestions } from "@/components/ask/ExampleQuestions";
import { QuestionInput } from "@/components/ask/QuestionInput";
import { ResultTable } from "@/components/ask/ResultTable";
import { StageIndicator } from "@/components/ask/StageIndicator";
import { AmberBlock, Disclosures, Narrative } from "@/components/ask/TracePanel";
import { WIDTH } from "@/components/frame/Frame";
import { GraphView } from "@/components/graph/GraphView";
import { api, ApiError } from "@/lib/api";
import { formatWhen } from "@/lib/ask/format";
import { endingOf, endingOfError } from "@/lib/ask/outcome";
import { fromTrace } from "@/lib/graph/fromTrace";
import { READABLE_VERSIONS, type QueryOut } from "@/lib/trace/types";

type State =
  | { kind: "idle" }
  | { kind: "asking"; question: string; startedAt: number }
  | { kind: "done"; result: QueryOut; stored: boolean }
  // `reading` names the stored query that could not be read, if it was one.
  | { kind: "failed"; error: ApiError; reading: string | null };

const asError = (cause: unknown): ApiError =>
  cause instanceof ApiError ? cause : new ApiError(0, "unknown", String(cause));

/**
 * One answer, in the order of DD-19: the amber block when a choice had no
 * basis; the plain-English account with the results beside it (FR-30);
 * the graph; and everything technical behind two disclosures.
 */
function Answer({ result, stored }: { result: QueryOut; stored: boolean }) {
  const trace = result.trace;
  if (!READABLE_VERSIONS.includes(trace.trace_version)) {
    return (
      <Ending
        ending={{
          tone: "failed",
          title: `This trace is version ${trace.trace_version}`,
          lines: [`This page reads versions ${READABLE_VERSIONS.join(" and ")}.`],
        }}
      />
    );
  }
  const answered = result.status === "answered";
  return (
    <div className="mt-6 space-y-6" data-ask="answer">
      <div>
        <p className="text-xl">{trace.question}</p>
        <p className="mt-1 text-xs text-gray-500">
          {stored ? "Read back from the store; asked" : "Asked"} {formatWhen(trace.created_at)}
        </p>
      </div>

      <AmberBlock trace={trace} />

      <div className="grid grid-cols-2 items-start gap-8">
        <Narrative trace={trace} />
        <section data-ask="results">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-600">
            {answered ? "Results" : "What happened"}
          </h2>
          <div className="mt-3 space-y-3">
            {!answered && <Ending ending={endingOf(result)} />}
            {answered && <ResultTable result={result} />}
            {answered && <p className="text-[15px] text-gray-900">{trace.narrative.result}</p>}
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

function Asking() {
  const id = useSearchParams().get("id");
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  const ask = useCallback((asked: string) => {
    setQuestion(asked);
    // The address no longer names the answer on the screen.
    window.history.replaceState(null, "", "/ask");
    setState({ kind: "asking", question: asked, startedAt: Date.now() });
    api
      .ask(asked)
      .then((result) => {
        setState({ kind: "done", result, stored: false });
        // The address names the stored query, so that it can be reopened.
        window.history.replaceState(null, "", `/ask?id=${result.query_id}`);
      })
      .catch((cause: unknown) => setState({ kind: "failed", error: asError(cause), reading: null }));
  }, []);

  // `?id=` reads a stored query (IR-08's shape, ahead of the history
  // page). Whether one is being read is worked out, not kept: the address
  // names a query that is neither shown nor known to be unreadable.
  const busy = state.kind === "asking";
  const reading =
    id !== null &&
    !busy &&
    !(state.kind === "done" && state.result.query_id === id) &&
    !(state.kind === "failed" && state.reading === id);
  useEffect(() => {
    if (!reading || id === null) return;
    let current = true;
    api
      .query(id)
      .then((result) => {
        if (!current) return;
        setQuestion(result.trace.question);
        setState({ kind: "done", result, stored: true });
      })
      .catch((cause: unknown) => current && setState({ kind: "failed", error: asError(cause), reading: id }));
    return () => {
      current = false;
    };
  }, [id, reading]);

  return (
    <>
      <QuestionInput value={question} onChange={setQuestion} onAsk={ask} busy={busy} />
      <ExampleQuestions onPick={ask} busy={busy} />

      {state.kind === "asking" && <StageIndicator startedAt={state.startedAt} />}
      {reading && <p className="mt-6 text-sm text-gray-500">Reading the stored query&hellip;</p>}
      {!reading && state.kind === "failed" && (
        <div className="mt-6">
          <Ending ending={endingOfError(state.error)} />
        </div>
      )}
      {!reading && state.kind === "done" && <Answer result={state.result} stored={state.stored} />}
    </>
  );
}

export default function Ask() {
  return (
    <div className={`${WIDTH} py-6 font-sans`}>
      <h1 className="mb-4 text-xl font-semibold">Ask a question</h1>
      {/* useSearchParams needs a boundary for the production build. */}
      <Suspense fallback={<p className="text-sm text-gray-500">&hellip;</p>}>
        <Asking />
      </Suspense>
    </div>
  );
}
