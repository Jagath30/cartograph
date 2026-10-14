/**
 * The layout (DD-18, NFR-18): which columns a table lists, how big it is,
 * and where it goes. Pure, so tested with no browser.
 */
import { describe, expect, test } from "vitest";

import storedV1 from "../../fixtures/trace-v1-days-of-the-week.json";
import storedV2 from "../../fixtures/trace-v2-gender-diverged.json";
import type { TraceDocument } from "../trace/types";
import { fromTrace } from "./fromTrace";
import { runAlongside, tablesUnder } from "./router";
import {
  SHAPING,
  shapingEdges,
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
const DAYS = fromTrace(storedV1 as unknown as TraceDocument);
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

  test("a plan's join puts the table that holds the key one column left of the table it points at", () => {
    for (const model of [MODEL, GENDER, DAYS]) {
      const placed = layout(model, CLOSED);
      const columns = [...new Set(placed.nodes.map((at) => Math.round(at.x)))].sort((a, b) => a - b);
      const column = new Map(placed.nodes.map((at) => [at.node.id, columns.indexOf(Math.round(at.x))]));
      for (const drawn of placed.edges.filter((edge) => SHAPING.has(edge.kind))) {
        expect(column.get(drawn.toTable)! - column.get(drawn.fromTable)!, drawn.id).toBe(1);
      }
    }
  });

  test("only the plan's joins place the tables: the other lines move none", () => {
    const placed = layout(MODEL, CLOSED);
    const without = layout({ ...MODEL, edges: MODEL.edges.filter((edge) => SHAPING.has(edge.kind)) }, CLOSED);
    expect(placed.nodes.map((at) => Math.round(at.x))).toEqual(without.nodes.map((at) => Math.round(at.x)));
  });

  test("no drawn line lies on a table it does not join in any view, nor runs alongside another in the closed view", () => {
    for (const model of [MODEL, GENDER, DAYS]) {
      const views: View[] = [
        CLOSED,
        { expanded: new Set(), showOther: true },
        { expanded: new Set(model.nodes.map((node) => node.id)), showOther: true },
      ];
      for (const view of views) {
        const placed = layout(model, view);
        const boxes = placed.nodes.map((at) => ({ id: at.node.id, x: at.x, y: at.y, width: at.width, height: at.height }));
        for (const edge of placed.edges) {
          expect(tablesUnder(placed.routes.get(edge.id)!, boxes, [edge.fromTable, edge.toTable]), edge.id).toEqual([]);
        }
        // No two lines run alongside each other without crossing, in the
        // closed view, the one a reader sees first (the owner's rule after
        // the lanes). Opened views are measured and recorded, not held.
        if (view !== CLOSED) continue;
        for (let i = 0; i < placed.edges.length; i += 1) {
          for (let j = i + 1; j < placed.edges.length; j += 1) {
            const pair = `${placed.edges[i].id} | ${placed.edges[j].id}`;
            expect(runAlongside(placed.routes.get(placed.edges[i].id)!, placed.routes.get(placed.edges[j].id)!), pair).toBe(false);
          }
        }
      }
    }
  });

  test("the bounds hold every table and every line", () => {
    const placed = layout(GENDER, CLOSED);
    const { x, y, width, height } = placed.bounds;
    for (const points of placed.routes.values()) {
      for (const p of points) expect(p.x >= x && p.x <= x + width && p.y >= y && p.y <= y + height).toBe(true);
    }
    for (const at of placed.nodes) {
      expect(at.x >= x && at.x + at.width <= x + width && at.y >= y && at.y + at.height <= y + height).toBe(true);
    }
  });

  test("no two tables overlap, and all are inside the picture", () => {
    for (const view of [CLOSED, { expanded: new Set(["web_sales", "date_dim"]), showOther: true }]) {
      const placed = layout(GENDER, view);
      for (const a of placed.nodes) {
        expect(a.x).toBeGreaterThanOrEqual(placed.bounds.x);
        expect(a.y).toBeGreaterThanOrEqual(placed.bounds.y);
        expect(a.x + a.width).toBeLessThanOrEqual(placed.bounds.x + placed.width);
        expect(a.y + a.height).toBeLessThanOrEqual(placed.bounds.y + placed.height);
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

describe("a table outside the plan stands beside a table it joins", () => {
  // The plan joins sale to address. "maker" is outside the plan, joined to
  // address by an equally valid line and to sale by a set-aside one;
  // "region" is joined only to maker.
  const OUTSIDE: GraphModel = {
    nodes: [
      node("sale", ["id", "address", "maker"]),
      node("address", ["id", "maker", "region"]),
      node("maker", ["id", "region"]),
      node("region", ["id"]),
    ],
    edges: [
      edge("planned", "sale.address", "address.id"),
      { ...edge("alternative", "sale.maker", "maker.id"), status: "withdrawn" },
      { ...edge("alternative", "address.maker", "maker.id"), status: "tied" },
      { ...edge("alternative", "maker.region", "region.id"), status: "withdrawn" },
    ],
    undrawn: [],
  };
  const columnsOf = (placed: ReturnType<typeof layout>) => {
    const xs = [...new Set(placed.nodes.map((at) => Math.round(at.x)))].sort((a, b) => a - b);
    return Object.fromEntries(placed.nodes.map((at) => [at.node.id, xs.indexOf(Math.round(at.x)) + 1]));
  };

  test("joined to a plan table: the first line in the legend's order places it, one column beyond", () => {
    const shaping = shapingEdges(OUTSIDE.nodes, OUTSIDE.edges);
    expect(shaping.map(({ edge, from, to }) => [edge.id, from, to])).toEqual([
      ["sale.address>address.id", "sale", "address"],
      ["address.maker>maker.id", "address", "maker"],
      ["maker.region>region.id", "maker", "region"],
    ]);
    expect(columnsOf(layout(OUTSIDE, CLOSED))).toEqual({ sale: 1, address: 2, maker: 3, region: 4 });
  });

  test("joined only through another table outside the plan: placed after it, beyond it", () => {
    const placed = layout(OUTSIDE, CLOSED);
    expect(placed.shaping.map((edge) => edge.id)).toContain("maker.region>region.id");
    expect(columnsOf(placed).region - columnsOf(placed).maker).toBe(1);
  });

  test("the plan's tables stay where the plan alone puts them", () => {
    const planOnly: GraphModel = { ...OUTSIDE, nodes: OUTSIDE.nodes.slice(0, 2), edges: OUTSIDE.edges.slice(0, 1) };
    const alone = columnsOf(layout(planOnly, CLOSED));
    const all = columnsOf(layout(OUTSIDE, CLOSED));
    expect([all.sale, all.address]).toEqual([alone.sale, alone.address]);
  });

  test("a table with no drawn line is placed by no line; with no plan, nothing is", () => {
    const lonely: GraphModel = { ...OUTSIDE, nodes: [...OUTSIDE.nodes, node("lonely", ["id"])] };
    expect(shapingEdges(lonely.nodes, lonely.edges).map(({ edge }) => edge.id)).not.toContain("lonely");
    expect(shapingEdges(OUTSIDE.nodes, OUTSIDE.edges.slice(1))).toEqual([]);
  });

  test("the shaping is a tree: one line per table placed, and every shaping join one column apart", () => {
    for (const model of [OUTSIDE, GENDER, DAYS]) {
      const placed = layout(model, CLOSED);
      const shaped = new Set(placed.shaping.flatMap((edge) => [edge.fromTable, edge.toTable]));
      expect(placed.shaping.length).toBe(shaped.size - 1);
      const columns = columnsOf(placed);
      for (const edge of placed.shaping) expect(Math.abs(columns[edge.toTable] - columns[edge.fromTable]), edge.id).toBe(1);
    }
  });
});
