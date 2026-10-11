"use client";

import type { Ending as EndingView, Tone } from "@/lib/ask/outcome";

const TONES: Record<Tone, string> = {
  answered: "border-gray-200 bg-white text-gray-800",
  declined: "border-slate-300 bg-slate-50 text-slate-900",
  refused: "border-red-300 bg-red-50 text-red-900",
  failed: "border-red-300 bg-red-50 text-red-900",
};

/** FR-32: how the question ended, whichever way it ended. */
export function Ending({ ending }: { ending: EndingView }) {
  return (
    <div className={`rounded-md border p-4 ${TONES[ending.tone]}`} role={ending.tone === "answered" ? undefined : "alert"}>
      <p className="font-semibold">{ending.title}</p>
      {ending.lines.map((line) => (
        <p key={line} className="mt-1 text-sm">
          {line}
        </p>
      ))}
    </div>
  );
}
