"use client";

import { count, formatCell } from "@/lib/ask/format";
import type { QueryOut } from "@/lib/trace/types";

/**
 * FR-29: the result, as it came back. Columns and rows are the API's, in
 * its order; a value is shown with separators and never changed, and the
 * raw value is on the cell for anyone who points at it.
 */
export function ResultTable({ result }: { result: QueryOut }) {
  const execution = result.trace.execution;
  if (result.status !== "answered") return null;

  // A stored query read back: the rows were never stored (DR-15).
  if (result.rows === null) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-4 text-sm text-gray-700">
        <p className="font-medium text-gray-900">The rows are not stored.</p>
        <p className="mt-1">
          {result.row_count === null
            ? "This query was read back from the store."
            : `${count(result.row_count)} ${result.row_count === 1 ? "row" : "rows"} came back when it was asked`}
          {result.columns.length > 0 && result.row_count !== null && (
            <>
              , with {result.columns.length === 1 ? "the column" : "the columns"}{" "}
              <span className="font-mono text-xs">{result.columns.join(", ")}</span>
            </>
          )}
          {result.row_count !== null && ". Ask the question again to see them."}
        </p>
      </div>
    );
  }

  return (
    <div>
      {/* It takes focus, so that a large result can be scrolled by keyboard (polish D). */}
      <div
        className="max-h-[28rem] overflow-auto rounded-md border border-gray-200 bg-white"
        role="region"
        aria-label="The results"
        tabIndex={0}
      >
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 bg-slate-100 text-left">
            <tr>
              {result.columns.map((column) => (
                <th key={column} className="border-b border-gray-300 px-3 py-2 font-mono text-xs font-semibold">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, index) => (
              <tr key={index} className="border-b border-gray-100 last:border-0">
                {row.map((value, position) => {
                  const cell = formatCell(value, result.columns[position] ?? "");
                  return (
                    <td
                      key={position}
                      title={cell.raw}
                      className={`px-3 py-1.5 ${cell.numeric ? "text-right tabular-nums" : ""} ${
                        cell.empty ? "text-gray-400" : ""
                      }`}
                    >
                      {cell.text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* How many rows came back is said once, by the narrative beneath. */}
      <p className="mt-2 text-xs text-gray-600">
        {result.truncated && (
          <span className="mr-1 font-medium text-amber-800">
            Showing the first {count(execution?.row_cap ?? result.rows.length)} rows: more matched than the limit allows.
          </span>
        )}
        Numbers are shown with separators and are otherwise as they came back; point at one for the raw value.
      </p>
    </div>
  );
}
