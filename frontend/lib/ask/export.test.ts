/** Copy and download (polish C): the CSV and the tab text. */
import { describe, expect, test } from "vitest";

import { csvField, csvFileName, downloadLabel, safeText, toCsv, toTsv, tsvField } from "./export";

describe("CSV, RFC 4180", () => {
  test("the header row first, records ended by CRLF, values as they came back", () => {
    expect(toCsv(["cd_gender", "total_spent"], [["F", "183224441.98"], ["M", "178618746.26"]])).toBe(
      "cd_gender,total_spent\r\nF,183224441.98\r\nM,178618746.26\r\n",
    );
  });

  test("commas, quotes and line breaks are quoted, quotes doubled", () => {
    expect(csvField("a,b")).toBe('"a,b"');
    expect(csvField('say "hi"')).toBe('"say ""hi"""');
    expect(csvField("two\nlines")).toBe('"two\nlines"');
    expect(csvField("two\r\nlines")).toBe('"two\r\nlines"');
    expect(csvField("plain")).toBe("plain");
  });

  test("an empty value is an empty cell; an empty string stays empty", () => {
    expect(toCsv(["a", "b", "c"], [[null, "", 0]])).toBe("a,b,c\r\n,,0\r\n");
  });

  test("numbers and booleans as they came back, not as shown", () => {
    expect(toCsv(["n"], [[72271], [-25000], [0.5], [true]])).toBe("n\r\n72271\r\n-25000\r\n0.5\r\ntrue\r\n");
  });
});

describe("a value a spreadsheet could run as a formula", () => {
  test.each(["=SUM(A1:A9)", "+1+1 then", "-cmd", "@SUM(A1)", "\tTAB", "\rCR", "=1+1"])("%j is written with a leading quote", (text) => {
    expect(safeText(text)).toBe(`'${text}`);
  });

  test("numbers that begin with a sign are numbers, and are left alone", () => {
    for (const text of ["-25000", "-1.5", "+3", "-0.07", "-1e5", "+.5"]) expect(safeText(text)).toBe(text);
    expect(safeText(-42)).toBe("-42");
  });

  test("the quoted value is still quoted where CSV needs it", () => {
    expect(csvField("=HYPERLINK(\"x\",\"y\")")).toBe('"\'=HYPERLINK(""x"",""y"")"');
    expect(csvField("\tTAB")).toBe("'\tTAB");
  });

  test("text that merely contains the characters is untouched", () => {
    expect(safeText("a=b")).toBe("a=b");
    expect(safeText("Mid-sized")).toBe("Mid-sized");
  });
});

describe("tab text for the clipboard", () => {
  test("the header row first, tabs between cells, a line per row", () => {
    expect(toTsv(["state", "total"], [["TX", "134919264.10"], ["GA", null]])).toBe("state\ttotal\nTX\t134919264.10\nGA\t\n");
  });

  test("a cell holding a tab, a line break or a quote is quoted", () => {
    expect(tsvField("a\tb")).toBe('"a\tb"');
    expect(tsvField("two\nlines")).toBe('"two\nlines"');
    expect(tsvField('say "hi"')).toBe('"say ""hi"""');
    expect(tsvField("a,b")).toBe("a,b");
  });

  test("the formula rule holds when pasted too", () => {
    expect(tsvField("=1+1")).toBe("'=1+1");
    expect(tsvField("-25000")).toBe("-25000");
  });
});

describe("names and labels", () => {
  test("the file is named by the first 8 characters of the query id", () => {
    expect(csvFileName("be421a4e-094f-4350-a6aa-29615a6488c7")).toBe("cartograph-be421a4e.csv");
  });

  test("the download says when the rows were capped, with the real cap", () => {
    expect(downloadLabel(false, 1000)).toBe("Download CSV");
    expect(downloadLabel(null, null)).toBe("Download CSV");
    expect(downloadLabel(true, 1000)).toBe("Download CSV (first 1,000 rows)");
    expect(downloadLabel(true, 250)).toBe("Download CSV (first 250 rows)");
  });
});
