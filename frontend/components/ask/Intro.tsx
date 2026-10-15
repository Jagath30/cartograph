"use client";

import { EXAMPLES } from "@/lib/ask/examples";
import { DATA, LEAD, STEPS } from "@/lib/ask/intro";

/**
 * The page before any question is asked (polish B): what Cartograph does,
 * how, and four questions to start from. Once an answer is shown the page
 * is as it was, and the examples are the chips beneath the question box.
 */
export function Intro({ onPick, busy }: { onPick: (question: string) => void; busy: boolean }) {
  return (
    <div className="mt-6 space-y-6" data-ask="intro">
      <p className="max-w-3xl text-base leading-relaxed text-gray-800">{LEAD}</p>

      <ol className="grid gap-4 lg:grid-cols-3" aria-label="How it works">
        {STEPS.map((step, index) => (
          <li key={step} className="flex gap-3 rounded-md border border-gray-200 bg-white p-4 text-[15px] text-gray-800">
            <span
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-gray-300 text-sm font-semibold text-gray-700"
              aria-hidden="true"
            >
              {index + 1}
            </span>
            <span>{step}</span>
          </li>
        ))}
      </ol>

      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-600">Questions to start from</h2>
        <div className="mt-3 grid gap-3 lg:grid-cols-2" data-ask="examples">
          {EXAMPLES.map((example) => (
            <button
              key={example.question}
              type="button"
              aria-label={example.question}
              disabled={busy}
              onClick={() => onPick(example.question)}
              className="rounded-md border border-gray-200 bg-white p-4 text-left hover:border-gray-400 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span className="block text-[15px] text-gray-900">{example.question}</span>
              <span className="mt-1 block text-xs text-gray-600" aria-hidden="true">
                Shows: {example.shows}
              </span>
            </button>
          ))}
        </div>
      </div>

      <p className="text-sm text-gray-600">{DATA}</p>
    </div>
  );
}
