"use client";

import { useEffect, useRef, useState } from "react";

import { csvFileName, downloadLabel, toCsv, toTsv } from "@/lib/ask/export";
import type { QueryOut } from "@/lib/trace/types";

export const ACTION =
  "rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50";

type Said = "idle" | "copied" | "failed";

/**
 * Puts text on the clipboard and says so for two seconds, through a polite
 * live region. If the clipboard refuses, it says "Copy failed": never a
 * "Copied" that did not happen.
 */
export function CopyButton({ label, text, name }: { label: string; text: () => string; name?: string }) {
  const [said, setSaid] = useState<Said>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  const copy = async () => {
    let next: Said = "failed";
    try {
      await navigator.clipboard.writeText(text());
      next = "copied";
    } catch {
      next = "failed";
    }
    setSaid(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setSaid("idle"), 2000);
  };
  const shown = said === "copied" ? "Copied" : said === "failed" ? "Copy failed" : label;
  return (
    <>
      <button type="button" className={ACTION} onClick={copy} aria-label={said === "idle" ? name : undefined} data-action={label}>
        {shown}
      </button>
      <span className="sr-only" aria-live="polite">
        {said === "copied" ? `${name ?? label}: copied` : said === "failed" ? `${name ?? label}: copy failed` : ""}
      </span>
    </>
  );
}

/** Copy table and Download CSV, for an answer whose rows are here. */
export function ResultActions({ result }: { result: QueryOut }) {
  const rows = result.rows;
  if (!rows) return null;
  const download = () => {
    const blob = new Blob([toCsv(result.columns, rows)], { type: "text/csv;charset=utf-8" });
    const address = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = address;
    link.download = csvFileName(result.query_id);
    link.click();
    URL.revokeObjectURL(address);
  };
  return (
    <div className="flex items-center gap-2" data-ask="result-actions">
      <CopyButton label="Copy table" text={() => toTsv(result.columns, rows)} />
      <button type="button" className={ACTION} onClick={download} data-action="Download CSV">
        {downloadLabel(result.truncated, result.trace.execution?.row_cap)}
      </button>
    </div>
  );
}
