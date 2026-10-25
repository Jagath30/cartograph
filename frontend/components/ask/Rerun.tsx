"use client";

import { useCallback, useState } from "react";

import { Ending } from "@/components/ask/Ending";
import { ResultTable } from "@/components/ask/ResultTable";
import { useSession } from "@/components/frame/Session";
import { api, ApiError, type RerunOut } from "@/lib/api";
import { count } from "@/lib/ask/format";
import { endingOfError, type Ending as EndingView } from "@/lib/ask/outcome";
import { asOfNow, RERUN_LABEL } from "@/lib/rerun";
import type { QueryOut } from "@/lib/trace/types";

export type RerunState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done"; result: RerunOut }
  | { kind: "failed"; error: ApiError };

const BUTTON =
  "rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-900 hover:bg-gray-50 disabled:cursor-wait disabled:text-gray-500";

/** How a rerun the server did not answer ended. The lines are the API's. */
function endingOfRerun(result: RerunOut): EndingView {
  const lines = result.message ? [result.message] : [];
  if (result.status === "validation_failed") return { tone: "refused", title: "The SQL was refused when it was checked again", lines };
  return { tone: "failed", title: "The SQL was run again and did not complete", lines };
}

/**
 * Item 88: a stored answer keeps no rows (DR-15). This runs its SQL again,
 * validated again by the backend and run read-only, and shows the rows
 * with the moment they are of. Nothing is stored.
 */
export function RerunView({ stored, state, onRun }: { stored: QueryOut; state: RerunState; onRun: () => void }) {
  const running = state.kind === "running";
  return (
    <div className="space-y-3" data-ask="rerun">
      {state.kind !== "done" || state.result.status !== "answered" ? (
        <div className="rounded-md border border-gray-200 bg-white p-4 text-sm text-gray-700">
          <p className="font-medium text-gray-900">The rows are not stored.</p>
          <p className="mt-1">
            {stored.row_count !== null &&
              `${count(stored.row_count)} ${stored.row_count === 1 ? "row" : "rows"} came back when it was asked. `}
            {RERUN_LABEL} runs the stored SQL against the data as it is now, checked again before it runs; nothing is
            stored.
          </p>
        </div>
      ) : (
        <>
          <ResultTable
            result={{
              ...stored,
              status: "answered",
              columns: state.result.columns,
              rows: state.result.rows,
              row_count: state.result.row_count,
              truncated: state.result.truncated,
            }}
          />
          <p className="text-[15px] text-gray-900">{asOfNow(state.result.ran_at)}</p>
        </>
      )}
      {state.kind === "done" && state.result.status !== "answered" && <Ending ending={endingOfRerun(state.result)} />}
      {state.kind === "failed" && <Ending ending={endingOfError(state.error)} />}
      <button type="button" className={BUTTON} onClick={onRun} disabled={running}>
        {running ? "Running…" : RERUN_LABEL}
      </button>
    </div>
  );
}

export function Rerun({ stored }: { stored: QueryOut }) {
  const { lost } = useSession();
  const [state, setState] = useState<RerunState>({ kind: "idle" });
  const run = useCallback(() => {
    setState({ kind: "running" });
    api
      .rerun(stored.query_id)
      .then((result) => setState({ kind: "done", result }))
      .catch((cause: unknown) => {
        const error = cause instanceof ApiError ? cause : new ApiError(0, "unknown", String(cause));
        if (error.status === 401) lost();
        setState({ kind: "failed", error });
      });
  }, [stored.query_id, lost]);
  return <RerunView stored={stored} state={state} onRun={run} />;
}
