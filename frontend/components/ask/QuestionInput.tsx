"use client";

import { MAX_QUESTION } from "@/lib/ask/examples";

type Props = {
  value: string;
  onChange: (value: string) => void;
  onAsk: (question: string) => void;
  busy: boolean;
};

/** FR-28: a plain-English question, and a button. */
export function QuestionInput({ value, onChange, onAsk, busy }: Props) {
  const ready = value.trim() !== "" && !busy;
  return (
    <form
      className="flex items-start gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) onAsk(value.trim());
      }}
    >
      <label className="sr-only" htmlFor="question">
        Your question
      </label>
      <input
        id="question"
        type="text"
        className="min-w-0 flex-1 rounded-md border border-gray-400 px-4 py-2.5 text-base shadow-sm focus:border-blue-600 focus:outline-none"
        placeholder="Ask a question of the warehouse, in plain English"
        value={value}
        maxLength={MAX_QUESTION}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
      />
      <button
        type="submit"
        disabled={!ready}
        className="rounded-md bg-blue-700 px-6 py-2.5 text-base font-medium text-white shadow-sm hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-gray-300"
      >
        {busy ? "Asking…" : "Ask"}
      </button>
    </form>
  );
}
