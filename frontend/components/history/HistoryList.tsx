"use client";

import Link from "next/link";

import type { Listed } from "@/lib/api";
import { formatWhen } from "@/lib/ask/format";
import { MARK, OUTCOME_WORDS } from "@/lib/history";

/**
 * The signed-in user's own questions, in the order the API gave them:
 * newest first (IR-08). Each opens its stored answer; one that rests on a
 * choice without basis is marked, in the amber kept for that warning.
 */
export function HistoryList({ listed }: { listed: Listed[] }) {
  if (listed.length === 0) {
    return (
      <p className="text-sm text-gray-700">
        No questions yet.{" "}
        <Link href="/ask" className="rounded-sm underline underline-offset-4">
          Ask one
        </Link>
        .
      </p>
    );
  }
  return (
    <ol className="divide-y divide-gray-200 rounded-md border border-gray-200 bg-white">
      {listed.map((row) => (
        <li key={row.query_id} className="px-4 py-3">
          <Link href={`/history/${row.query_id}`} className="rounded-sm text-[15px] text-gray-900 underline-offset-4 hover:underline">
            {row.question}
          </Link>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600">
            <span>{formatWhen(row.created_at)}</span>
            <span>{OUTCOME_WORDS[row.status] ?? row.status}</span>
            {row.had_ambiguity && (
              <span className="rounded border border-amber-400 bg-amber-50 px-1.5 py-0.5 font-medium text-amber-950">{MARK}</span>
            )}
          </p>
        </li>
      ))}
    </ol>
  );
}
