"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { Answer } from "@/components/ask/Answer";
import { Ending } from "@/components/ask/Ending";
import { ExampleQuestions } from "@/components/ask/ExampleQuestions";
import { Intro } from "@/components/ask/Intro";
import { QuestionInput } from "@/components/ask/QuestionInput";
import { StageIndicator } from "@/components/ask/StageIndicator";
import { WIDTH } from "@/components/frame/Frame";
import { useRequiredSession } from "@/components/frame/Session";
import { api, ApiError } from "@/lib/api";
import { endingOfError } from "@/lib/ask/outcome";
import { type QueryOut } from "@/lib/trace/types";

type State =
  | { kind: "idle" }
  | { kind: "asking"; question: string; startedAt: number }
  | { kind: "done"; result: QueryOut; stored: boolean }
  // `reading` names the stored query that could not be read, if it was one.
  | { kind: "failed"; error: ApiError; reading: string | null };

const asError = (cause: unknown): ApiError =>
  cause instanceof ApiError ? cause : new ApiError(0, "unknown", String(cause));

function Asking() {
  const id = useSearchParams().get("id");
  // Asking needs a session (step 11); without one, to /login and back.
  const { show, lost } = useRequiredSession();
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  // A 401 on the way: the session ended, and the visitor signs in again.
  const failed = useCallback(
    (cause: unknown, reading: string | null): State => {
      const error = asError(cause);
      if (error.status === 401) lost();
      return { kind: "failed", error, reading };
    },
    [lost],
  );

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
      .catch((cause: unknown) => setState(failed(cause, null)));
  }, [failed]);

  // `?id=` reads a stored query (IR-08's shape, ahead of the history
  // page). Whether one is being read is worked out, not kept: the address
  // names a query that is neither shown nor known to be unreadable.
  const busy = state.kind === "asking";
  const reading =
    id !== null &&
    show &&
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
      .catch((cause: unknown) => current && setState(failed(cause, id)));
    return () => {
      current = false;
    };
  }, [id, reading, failed]);

  return (
    <>
      <QuestionInput value={question} onChange={setQuestion} onAsk={ask} busy={busy} />
      {/* Before any question: what Cartograph does, and the examples as cards. */}
      {state.kind === "idle" && !reading ? <Intro onPick={ask} busy={busy} /> : <ExampleQuestions onPick={ask} busy={busy} />}

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
