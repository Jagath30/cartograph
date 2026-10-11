/**
 * How a value is shown. Pure. The value itself is never changed: nothing
 * is rounded, and every digit that came back is on the screen. Only
 * separators are added, and the raw value stays available beside it.
 *
 * No types cross the API, so the rule rests on how JSON carried the value
 * (app/api_models.py: a numeric arrives as text, exact; an integer as a
 * number; a date in ISO form) and on the column's name:
 *
 *   - a column whose name ends in _sk, _id or _number is an identifier,
 *     and is never grouped (the owner's guard at stop 1);
 *   - a decimal written as text, with a fraction, gets separators in its
 *     whole part: "183224441.98" -> "183,224,441.98";
 *   - an integer of 10,000 or more gets them: 72271 -> "72,271". A year
 *     stays 2001;
 *   - anything else is shown as it came. A zip code is text and stays so.
 */
import type { Value } from "../trace/types";

export type Cell = {
  text: string;
  /** What came back, as text: for the hover, and for anyone who doubts. */
  raw: string;
  numeric: boolean;
  empty: boolean;
};

const IDENTIFIER = /(_sk|_id|_number)$/i;
const DECIMAL = /^-?\d+\.\d+$/;
const GROUP_FROM = 10_000;

/** Separators into a run of digits, with or without a sign. */
export function grouped(digits: string): string {
  // Between two digits, wherever a multiple of three digits follows. A
  // sign is not a digit, so nothing is put after it.
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function formatCell(value: Value, column: string): Cell {
  if (value === null) return { text: "—", raw: "null", numeric: false, empty: true };
  const raw = String(value);
  if (typeof value === "boolean") return { text: raw, raw, numeric: false, empty: false };
  const identifier = IDENTIFIER.test(column);
  if (typeof value === "number") {
    const group = !identifier && Number.isInteger(value) && Math.abs(value) >= GROUP_FROM;
    return { text: group ? grouped(raw) : raw, raw, numeric: true, empty: false };
  }
  if (DECIMAL.test(value)) {
    if (identifier) return { text: raw, raw, numeric: true, empty: false };
    const [whole, fraction] = value.split(".");
    return { text: `${grouped(whole)}.${fraction}`, raw, numeric: true, empty: false };
  }
  return { text: raw, raw, numeric: false, empty: false };
}

/** A count, for the page's own sentences: 1000 -> "1,000". */
export function count(number: number): string {
  return grouped(String(number));
}

/**
 * A stored moment as a person would write it: "10 October 2026, 16:19".
 * In the reader's own time zone unless one is given.
 */
export function formatWhen(iso: string, timeZone?: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return iso;
  const parts = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone,
  }).formatToParts(when);
  const part = (type: string) => parts.find((entry) => entry.type === type)?.value ?? "";
  return `${part("day")} ${part("month")} ${part("year")}, ${part("hour")}:${part("minute")}`;
}

/** Milliseconds for a reader: 286 -> "0.3 s", 2433 -> "2.4 s". */
export function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}
