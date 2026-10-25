"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Answer } from "@/components/ask/Answer";
import { Ending } from "@/components/ask/Ending";
import { WIDTH } from "@/components/frame/Frame";
import { useRequiredSession } from "@/components/frame/Session";
import { api, ApiError } from "@/lib/api";
import { endingOfError } from "@/lib/ask/outcome";
import type { QueryOut } from "@/lib/trace/types";

type State =
  | { kind: "reading" }
  | { kind: "read"; result: QueryOut }
  | { kind: "failed"; error: ApiError };

/** One stored answer, reopened: its explanation, graph and SQL, and the
 *  offer to run its SQL again (item 88). Anyone else's is not found. */
export default function StoredAnswer() {
  const { id } = useParams<{ id: string }>();
  const { show, lost } = useRequiredSession();
  const [state, setState] = useState<State>({ kind: "reading" });

  useEffect(() => {
    if (!show) return;
    let current = true;
    api
      .query(id)
      .then((result) => current && setState({ kind: "read", result }))
      .catch((cause: unknown) => {
        if (!current) return;
        const error = cause instanceof ApiError ? cause : new ApiError(0, "unknown", String(cause));
        if (error.status === 401) lost();
        setState({ kind: "failed", error });
      });
    return () => {
      current = false;
    };
  }, [id, show, lost]);

  return (
    <div className={`${WIDTH} py-6 font-sans`}>
      <p className="text-sm">
        <Link href="/history" className="rounded-sm text-gray-700 underline-offset-4 hover:underline">
          &larr; Your questions
        </Link>
      </p>
      <h1 className="mt-2 text-xl font-semibold">From your history</h1>
      {(!show || state.kind === "reading") && <p className="mt-6 text-sm text-gray-500">Reading the stored query&hellip;</p>}
      {show && state.kind === "failed" && (
        <div className="mt-6">
          <Ending ending={endingOfError(state.error)} />
        </div>
      )}
      {show && state.kind === "read" && <Answer result={state.result} stored />}
    </div>
  );
}
