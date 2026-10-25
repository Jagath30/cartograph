"use client";

import { useEffect, useState } from "react";

import { Ending } from "@/components/ask/Ending";
import { WIDTH } from "@/components/frame/Frame";
import { useRequiredSession } from "@/components/frame/Session";
import { HistoryList } from "@/components/history/HistoryList";
import { api, ApiError, type Listed } from "@/lib/api";
import { endingOfError } from "@/lib/ask/outcome";

type State = { kind: "reading" } | { kind: "read"; listed: Listed[] } | { kind: "failed"; error: ApiError };

/** The signed-in user's own questions, newest first, the newest 100
 *  (IR-08; no paging, a known limit). */
export default function History() {
  const { show, lost } = useRequiredSession();
  const [state, setState] = useState<State>({ kind: "reading" });

  useEffect(() => {
    if (!show) return;
    let current = true;
    api
      .history()
      .then((history) => current && setState({ kind: "read", listed: history.queries }))
      .catch((cause: unknown) => {
        if (!current) return;
        const error = cause instanceof ApiError ? cause : new ApiError(0, "unknown", String(cause));
        if (error.status === 401) lost();
        setState({ kind: "failed", error });
      });
    return () => {
      current = false;
    };
  }, [show, lost]);

  return (
    <div className={`${WIDTH} py-6 font-sans`}>
      <h1 className="mb-4 text-xl font-semibold">Your questions</h1>
      {(!show || state.kind === "reading") && <p className="text-sm text-gray-500">Reading your history&hellip;</p>}
      {show && state.kind === "failed" && <Ending ending={endingOfError(state.error)} />}
      {show && state.kind === "read" && <HistoryList listed={state.listed} />}
    </div>
  );
}
