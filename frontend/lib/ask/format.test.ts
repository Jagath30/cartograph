/**
 * How a value is shown (stop 1, point d and ruling 3): separators only,
 * every digit kept, identifiers never grouped, the raw value beside it.
 */
import { describe, expect, test } from "vitest";

import { count, formatCell, formatWhen, grouped, seconds } from "./format";

describe("separators", () => {
  test.each([
    ["0", "0"],
    ["999", "999"],
    ["1000", "1,000"],
    ["12345", "12,345"],
    ["123456", "123,456"],
    ["1234567", "1,234,567"],
    ["-1234567", "-1,234,567"],
    ["-999", "-999"],
  ])("%s -> %s", (digits, shown) => {
    expect(grouped(digits)).toBe(shown);
  });
});

describe("a decimal that arrived as text", () => {
  test("gets separators in its whole part and keeps every digit of its fraction", () => {
    expect(formatCell("183224441.98", "total_spent").text).toBe("183,224,441.98");
    expect(formatCell("-1234567.5", "balance").text).toBe("-1,234,567.5");
    expect(formatCell("12.3456789012345678", "average").text).toBe("12.3456789012345678");
    expect(formatCell("1234.5000", "price").text).toBe("1,234.5000");
    expect(formatCell("0.07", "tax").text).toBe("0.07");
  });

  test("nothing is rounded: taking the separators out gives back what came", () => {
    for (const value of ["183224441.98", "0.10", "999999.999999", "-42.00", "1000000.0"]) {
      const cell = formatCell(value, "x");
      expect(cell.text.replaceAll(",", "")).toBe(value);
      expect(cell.raw).toBe(value);
      expect(cell.numeric).toBe(true);
    }
  });
});

describe("an integer", () => {
  test("of 10,000 or more is grouped; a smaller one is not, so a year stays a year", () => {
    expect(formatCell(72271, "orders").text).toBe("72,271");
    expect(formatCell(10000, "orders").text).toBe("10,000");
    expect(formatCell(9999, "orders").text).toBe("9999");
    expect(formatCell(2001, "d_year").text).toBe("2001");
    expect(formatCell(-25000, "change").text).toBe("-25,000");
    expect(formatCell(0, "orders").text).toBe("0");
  });

  test("a number that is not whole is shown as it came", () => {
    expect(formatCell(12345.678, "ratio").text).toBe("12345.678");
    expect(formatCell(0.5, "ratio").numeric).toBe(true);
  });
});

describe("identifiers are never grouped (ruling 3)", () => {
  test.each(["ss_item_sk", "customer_id", "cs_order_number", "D_DATE_SK", "c_customer_sk"])("%s", (column) => {
    expect(formatCell(2451545, column).text).toBe("2451545");
    expect(formatCell("2451545.00", column).text).toBe("2451545.00");
  });

  test("the name must END so: a column that merely holds the letters is not one", () => {
    expect(formatCell(2451545, "sku_count").text).toBe("2,451,545");
    expect(formatCell(2451545, "number_of_orders").text).toBe("2,451,545");
    expect(formatCell(2451545, "valid_ids").text).toBe("2,451,545");
  });
});

describe("everything else is shown as it came", () => {
  test("text that looks like a whole number is text: a zip code stays one", () => {
    expect(formatCell("37010", "ca_zip")).toEqual({ text: "37010", raw: "37010", numeric: false, empty: false });
    expect(formatCell("1234567", "code").text).toBe("1234567");
  });

  test("words, dates, and text that only resembles a number", () => {
    expect(formatCell("Tennessee", "state").text).toBe("Tennessee");
    expect(formatCell("2002-01-02", "d_date").text).toBe("2002-01-02");
    expect(formatCell("1.2.3", "version").text).toBe("1.2.3");
    expect(formatCell("1e5", "x").text).toBe("1e5");
    expect(formatCell(" 12.5", "x").text).toBe(" 12.5");
    expect(formatCell("", "x")).toEqual({ text: "", raw: "", numeric: false, empty: false });
  });

  test("a missing value is marked as missing, and is not an empty string or a zero", () => {
    expect(formatCell(null, "x")).toEqual({ text: "—", raw: "null", numeric: false, empty: true });
  });

  test("true and false", () => {
    expect(formatCell(true, "x").text).toBe("true");
    expect(formatCell(false, "x").text).toBe("false");
  });
});

describe("the page's own numbers and times", () => {
  test("a count", () => {
    expect(count(1000)).toBe("1,000");
    expect(count(7)).toBe("7");
  });

  test("a stored moment reads as a person would write it (stop 2, ruling 6)", () => {
    expect(formatWhen("2026-10-11T12:26:13.993187Z", "UTC")).toBe("11 October 2026, 12:26");
    expect(formatWhen("2026-10-10T10:49:07.495564Z", "Asia/Kolkata")).toBe("10 October 2026, 16:19");
    expect(formatWhen("2026-01-05T00:03:00Z", "UTC")).toBe("5 January 2026, 00:03");
  });

  test("what is not a moment is shown as it came, not as an invalid date", () => {
    expect(formatWhen("yesterday", "UTC")).toBe("yesterday");
  });

  test("a duration", () => {
    expect(seconds(286)).toBe("0.3 s");
    expect(seconds(2433)).toBe("2.4 s");
    expect(seconds(0)).toBe("0.0 s");
  });
});
