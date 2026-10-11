/**
 * The layout (DD-18, NFR-18): which columns a table lists, how big it is,
 * and where it goes. Pure, so tested with no browser.
 */
import { describe, expect, test } from "vitest";

import storedV2 from "../../fixtures/trace-v2-gender-diverged.json";
import type { TraceDocument } from "../trace/types";
import { fromTrace } from "./fromTrace";
import {
  FOOTER_HEIGHT,
  HEADER_HEIGHT,
  NODE_WIDTH,
  ROW_HEIGHT,
  drawnEdges,
  layout,
  listedColumns,
  nodeHeight,
  type View,
} from "./layout";
import type { GraphEdge, GraphModel, GraphNode } from "./model";

const GENDER = fromTrace(storedV2 as unknown as TraceDocument);
const CLOSED: View = { expanded: new Set(), showOther: false };

function node(id: string, columns: string[], matched: string[] = []): GraphNode {
  return {
    id,
    readable: id,
    anchor: true,
    inTree: true,
    matched,
    columns: columns.map((name) => ({ name, readable: name, dataType: "bigint", primaryKey: false, foreignKey: false })),
  };
}

function edge(kind: GraphEdge["kind"], from: string, to: string): GraphEdge {
  const [fromTable, fromColumn] = from.split(".");
  const [toTable, toColumn] = to.split(".");
  return {
    id: `${from}>${to}`,
    kind,
    fromTable,
    fromColumns: [fromColumn],
    toTable,
    toColumns: [toColumn],
    description: null,
    status: null,
    source: "catalog",
    loud: false,
  };
}

const MODEL: GraphModel = {
  nodes: [
    node("sale", ["id", "bill", "ship", "item", "amount", "note"], ["amount"]),
    node("address", ["id", "state", "city"]),
    node("item", ["id", "maker", "category"], ["category"]),
  ],
  edges: [
    edge("used", "sale.bill", "address.id"),
    edge("alternative", "sale.ship", "address.id"),
    edge("planned", "sale.item", "item.id"),
    edge("other", "item.maker", "address.id"),
  ],
  undrawn: [],
};

describe("the columns a table lists", () => {
  test("closed: the columns of the joins drawn and those the question matched, in the table's own order", () => {
    const drawn = drawnEdges(MODEL, CLOSED);
    expect(listedColumns(MODEL.nodes[0], drawn, false).map((column) => column.name)).toEqual([
      "bill",
      "ship",
      "item",
      "amount",
    ]);
    expect(listedColumns(MODEL.nodes[1], drawn, false).map((column) => column.name)).toEqual(["id"]);
    expect(listedColumns(MODEL.nodes[2], drawn, false).map((column) => column.name)).toEqual(["id", "category"]);
  });

  test("open: every column", () => {
    expect(listedColumns(MODEL.nodes[0], drawnEdges(MODEL, CLOSED), true)).toHaveLength(6);
  });

  test("a key that is not drawn does not list its column, and does once it is drawn", () => {
    const closed = layout(MODEL, CLOSED).nodes.find((placed) => placed.node.id === "item");
    expect(closed?.columns.map((column) => column.name)).toEqual(["id", "category"]);
    const open = layout(MODEL, { expanded: new Set(), showOther: true }).nodes.find((placed) => placed.node.id === "item");
    expect(open?.columns.map((column) => column.name)).toEqual(["id", "maker", "category"]);
  });

  test("every end of every edge drawn is a column listed, so no line ends in nothing", () => {
    for (const view of [CLOSED, { expanded: new Set<string>(), showOther: true }]) {
      for (const model of [MODEL, GENDER]) {
        const placed = layout(model, view);
        const listed = new Map(placed.nodes.map((at) => [at.node.id, new Set(at.columns.map((column) => column.name))]));
        for (const drawn of placed.edges) {
          expect(listed.get(drawn.fromTable)?.has(drawn.fromColumns[0])).toBe(true);
          expect(listed.get(drawn.toTable)?.has(drawn.toColumns[0])).toBe(true);
        }
      }
    }
  });
});

describe("what is hidden is counted", () => {
  test("a table says how many of its columns it is not listing", () => {
    const placed = layout(MODEL, CLOSED);
    expect(placed.nodes.map((at) => [at.node.id, at.columns.length, at.hidden])).toEqual([
      ["sale", 4, 2],
      ["address", 1, 2],
      ["item", 2, 1],
    ]);
    const open = layout(MODEL, { expanded: new Set(["sale"]), showOther: false });
    expect(open.nodes[0].hidden).toBe(0);
    expect(open.nodes[1].hidden).toBe(2);
  });

  test("the keys on no route are not drawn until asked for, and are counted", () => {
    const closed = layout(MODEL, CLOSED);
    expect(closed.edges.map((drawn) => drawn.kind)).toEqual(["used", "alternative", "planned"]);
    expect(closed.otherKeys).toBe(1);
    const open = layout(MODEL, { expanded: new Set(), showOther: true });
    expect(open.edges).toHaveLength(4);
    expect(open.otherKeys).toBe(0);
  });

  test("on the stored trace: web sales lists a few of its 34 columns", () => {
    const sales = layout(GENDER, CLOSED).nodes.find((at) => at.node.id === "web_sales");
    expect(sales?.node.columns).toHaveLength(34);
    expect(sales!.columns.length).toBeLessThan(15);
    expect(sales!.columns.length + sales!.hidden).toBe(34);
    expect(sales!.columns.map((column) => column.name)).toContain("ws_bill_cdemo_sk");
    expect(sales!.columns.map((column) => column.name)).toContain("ws_ship_cdemo_sk");
  });
});

describe("sizes and places", () => {
  test("a table's height is its header, its rows and its footer", () => {
    expect(nodeHeight(0)).toBe(HEADER_HEIGHT + FOOTER_HEIGHT);
    expect(nodeHeight(3)).toBe(HEADER_HEIGHT + 3 * ROW_HEIGHT + FOOTER_HEIGHT);
    for (const at of layout(MODEL, CLOSED).nodes) {
      expect(at.width).toBe(NODE_WIDTH);
      expect(at.height).toBe(nodeHeight(at.columns.length));
    }
  });

  test("opening a table makes it taller and nothing else bigger", () => {
    const closed = layout(MODEL, CLOSED);
    const open = layout(MODEL, { expanded: new Set(["sale"]), showOther: false });
    expect(open.nodes[0].height - closed.nodes[0].height).toBe(2 * ROW_HEIGHT);
    expect(open.nodes[1].height).toBe(closed.nodes[1].height);
  });

  test("the table that holds a key is to the left of the table it points at", () => {
    for (const model of [MODEL, GENDER]) {
      const placed = layout(model, CLOSED);
      const at = new Map(placed.nodes.map((node) => [node.node.id, node]));
      for (const drawn of placed.edges) {
        expect(at.get(drawn.fromTable)!.x + NODE_WIDTH).toBeLessThanOrEqual(at.get(drawn.toTable)!.x);
      }
    }
  });

  test("no two tables overlap, and all are inside the picture", () => {
    for (const view of [CLOSED, { expanded: new Set(["web_sales", "date_dim"]), showOther: true }]) {
      const placed = layout(GENDER, view);
      for (const a of placed.nodes) {
        expect(a.x).toBeGreaterThanOrEqual(0);
        expect(a.y).toBeGreaterThanOrEqual(0);
        expect(a.x + a.width).toBeLessThanOrEqual(placed.width);
        expect(a.y + a.height).toBeLessThanOrEqual(placed.height);
        for (const b of placed.nodes) {
          if (a === b) continue;
          const apart = a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y;
          expect(apart).toBe(true);
        }
      }
    }
  });

  test("the same model and view give the same picture", () => {
    expect(layout(GENDER, CLOSED)).toEqual(layout(GENDER, CLOSED));
  });
});
