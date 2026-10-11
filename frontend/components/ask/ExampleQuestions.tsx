"use client";

import { EXAMPLES } from "@/lib/ask/examples";

/** FR-44: questions to try, each with what it shows. */
export function ExampleQuestions({ onPick, busy }: { onPick: (question: string) => void; busy: boolean }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
      <span className="text-gray-600">Try:</span>
      {EXAMPLES.map((example) => (
        <button
          key={example.question}
          type="button"
          disabled={busy}
          onClick={() => onPick(example.question)}
          className="rounded-full border border-gray-300 bg-white px-3 py-1 text-left text-gray-800 hover:border-blue-600 hover:text-blue-800 disabled:cursor-not-allowed disabled:opacity-50"
          title={`Shows ${example.shows}`}
        >
          {example.question} <span className="text-xs text-gray-500">({example.shows})</span>
        </button>
      ))}
    </div>
  );
}
