"use client";

import { useEffect, useState } from "react";

import { seconds } from "@/lib/ask/format";
import { STAGES, stageAt, stagesOf, took } from "@/lib/ask/stages";

/**
 * IR-12. While a question is in flight the stage shown is an estimate
 * from the clock, and says so: the server reports nothing until it
 * answers (D-05).
 */
export function StageIndicator({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const tick = () => setElapsed(Date.now() - startedAt);
    tick();
    const timer = setInterval(tick, 100);
    return () => clearInterval(timer);
  }, [startedAt]);

  const running = stageAt(elapsed);
  return (
    <div className="mt-6 rounded-md border border-gray-200 bg-white p-4" role="status" aria-live="polite">
      <ol className="space-y-1.5">
        {STAGES.map((stage, index) => (
          <li
            key={stage.label}
            className={`flex items-center gap-3 text-sm ${
              index === running ? "font-medium text-gray-900" : index < running ? "text-gray-500" : "text-gray-400"
            }`}
          >
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${
                index === running ? "animate-pulse bg-blue-600" : index < running ? "bg-gray-400" : "border border-gray-300"
              }`}
              aria-hidden
            />
            {stage.label}
            {index === running && <span className="text-gray-500">&hellip;</span>}
          </li>
        ))}
      </ol>
      <p className="mt-3 text-xs text-gray-500">
        {seconds(elapsed)} so far. The stage shown is an estimate from how long each usually takes: the server reports
        nothing until it answers.
      </p>
    </div>
  );
}

/** What each stage took, from the trace: no longer an estimate. */
export function StageTimes({ timings, traceVersion }: { timings: Record<string, number>; traceVersion: number }) {
  return (
    <ul className="space-y-1 text-sm">
      {took(timings, stagesOf(traceVersion)).map((stage) => (
        <li key={stage.label} className="flex justify-between gap-6">
          <span>{stage.label}</span>
          <span className="tabular-nums text-gray-600">{stage.ms === null ? "not reached" : `${stage.ms} ms`}</span>
        </li>
      ))}
    </ul>
  );
}
