/**
 * The answer's rows, out of the page (polish C). Pure.
 *
 * The values are the ones that came back, not the ones shown: no
 * separators, nothing rounded. A missing value is an empty cell.
 *
 * A text value that begins with =, +, -, @, a tab or a carriage return,
 * and is not a number, is written with a leading single quote, so that a
 * spreadsheet cannot run it as a formula (OWASP, CSV injection). Numbers,
 * negative ones included, are written as they came back. The rule holds
 * for the copied text as well as for the file: pasted into a spreadsheet,
 * a cell is read the same way.
 */
import { count } from "./format";
import type { Value } from "../trace/types";

const NUMBER = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;
const RISKY = /^[=+\-@\t\r]/;

/** A value as text, made safe to open in a spreadsheet. */
export function safeText(value: Value): string {
  if (value === null) return "";
  const text = String(value);
  if (typeof value === "string" && RISKY.test(text) && !NUMBER.test(text)) return `'${text}`;
  return text;
}

/** One RFC 4180 field: quoted when it holds a comma, a quote or a line
 *  break, its quotes doubled. */
export function csvField(value: Value): string {
  const text = safeText(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** RFC 4180: the header row first, records ended by CRLF. */
export function toCsv(columns: string[], rows: Value[][]): string {
  return [columns, ...rows].map((row) => row.map(csvField).join(",") + "\r\n").join("");
}

/** One cell of tab-separated text: quoted, as a spreadsheet expects, when
 *  it holds a tab, a line break or a quote. */
export function tsvField(value: Value): string {
  const text = safeText(value);
  return /["\t\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** Tab-separated text for the clipboard, the header row first, so that it
 *  pastes into a spreadsheet as a table. */
export function toTsv(columns: string[], rows: Value[][]): string {
  return [columns, ...rows].map((row) => row.map(tsvField).join("\t")).join("\n") + "\n";
}

export function csvFileName(queryId: string): string {
  return `cartograph-${queryId.slice(0, 8)}.csv`;
}

/** The download button's words; when the rows were capped, it says so. */
export function downloadLabel(truncated: boolean | null, rowCap: number | null | undefined): string {
  return truncated && rowCap ? `Download CSV (first ${count(rowCap)} rows)` : "Download CSV";
}
