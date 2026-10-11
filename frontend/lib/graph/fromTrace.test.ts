/**
 * A trace into nodes and edges (DD-18, FR-31, FR-45; DD-05, NFR-18).
 *
 * Two fixtures are real: traces stored by the API and read back through
 * GET, one written at trace_version 1 and one at 2 whose SQL diverged
 * from the plan. The rest are written by hand, each for one rule.
 */
import { describe, expect, test } from "vitest";

import storedV1 from "../../fixtures/trace-v1-days-of-the-week.json";
import storedV2 from "../../fixtures/trace-v2-gender-diverged.json";
import type { ActualEdge, SchemaEdge, SelectedEdge, TraceDocument, Warning } from "../trace/types";
import { MATCHED_TOP, edgeId, fromTrace, joinText, matchedColumns } from "./fromTrace";
import type { GraphModel } from "./model";

const GENDER = storedV2 as unknown as TraceDocument;
const DAYS = storedV1 as unknown as TraceDocument;

function kinds(model: GraphModel): Record<string, string> {
  return Object.fromEntries(
    model.edges.map((edge) => [edge.id, edge.status ? `${edge.kind}:${edge.status}` : edge.kind]),
  );
}

function loud(model: GraphModel): string[] {
  return model.edges.filter((edge) => edge.loud).map((edge) => edge.id).sort();
}

// ---- a trace written by hand -------------------------------------------------

function key(from: string, to: string, extra: Partial<SchemaEdge> = {}): SchemaEdge {
  const [fromTable, fromColumn] = from.split(".");
  const [toTable, toColumn] = to.split(".");
  return {
    from_table: fromTable,
    from_columns: [fromColumn],
    to_table: toTable,
    to_columns: [toColumn],
    source: "catalog",
    constraint: null,
    note: null,
    ...extra,
  };
}

function table(name: string, columns: string[]) {
  return {
    table: name,
    readable: name.replace("_", " "),
    anchor: true,
    in_tree: true,
    columns: columns.map((column) => ({
      name: column,
      data_type: "bigint",
      readable: `${name} — ${column}`,
      primary_key: column.endsWith("_id"),
      foreign_key: column.endsWith("_sk"),
    })),
  };
}

const BILL = key("sale.bill_sk", "address.address_id");
const SHIP = key("sale.ship_sk", "address.address_id");
const ITEM = key("sale.item_sk", "item.item_id");
const SPARE = key("item.maker_sk", "address.address_id");

function warning(extra: Partial<Warning>): Warning {
  return {
    code: "arbitrary_choice",
    text: "I had no basis for this choice.",
    about: [],
    joins: [joinText(BILL)],
    other_joins: [joinText(SHIP)],
    state: "followed",
    loud: true,
    ...extra,
  };
}

type Changes = {
  selected?: SelectedEdge[];
  actual?: ActualEdge[];
  warnings?: Warning[];
  edges?: SchemaEdge[];
  /** Routes the item's attachment was weighed against. */
  alternatives?: object[];
};

/** A route as the trace stores one: a sentence for the whole, a clause for each join. */
function route(joins: SchemaEdge[], words: string) {
  return {
    id: joins.map(edgeId).join(" / "),
    tables: [joins[0].to_table, ...joins.map((join) => join.from_table)],
    length: joins.length,
    joins: joins.map((join) => ({ ...join, walked: "pk_to_fk", description: `clause: ${words} by ${join.from_columns[0]}` })),
    description: `Sentence: ${words}.`,
  };
}

/** A sale, its address by two keys, and its item. Billing is chosen. */
function tiny(changes: Changes = {}): TraceDocument {
  return {
    trace_version: 2,
    retrieval: null,
    subgraph: {
      nodes: [
        table("sale", ["sale_id", "bill_sk", "ship_sk", "item_sk", "amount", "note"]),
        table("address", ["address_id", "state", "city"]),
        table("item", ["item_id", "maker_sk", "category"]),
      ],
      edges: changes.edges ?? [BILL, SHIP, ITEM, SPARE],
    },
    paths: {
      attachments: [
        { anchor: "sale", selected: null, alternatives: [] },
        { anchor: "address", selected: route([BILL], "billing"), alternatives: [{ ...route([SHIP], "shipping"), status: "tied" }] },
        { anchor: "item", selected: route([ITEM], "item"), alternatives: changes.alternatives ?? [] },
      ],
      selected_edges: changes.selected ?? [
        { ...BILL, use: "present" },
        { ...ITEM, use: "missing" },
      ],
      actual_edges: changes.actual ?? [],
      warnings: changes.warnings ?? [warning({})],
    },
  } as unknown as TraceDocument;
}

function made(left: string, right: string, foreign = true): ActualEdge {
  const [leftTable, leftColumn] = left.split(".");
  const [rightTable, rightColumn] = right.split(".");
  return {
    left_table: leftTable,
    left_column: leftColumn,
    right_table: rightTable,
    right_column: rightColumn,
    outer: false,
    union: false,
    foreign,
  };
}

// ---- the kinds, on real traces ------------------------------------------------

describe("a stored trace whose SQL diverged (trace_version 2)", () => {
  const model = fromTrace(GENDER);

  test("its tables are the subgraph's own, in its order", () => {
    expect(model.nodes.map((node) => node.id)).toEqual([
      "web_sales",
      "web_returns",
      "customer_demographics",
      "web_page",
      "date_dim",
    ]);
    expect(model.nodes.find((node) => node.id === "web_sales")?.columns).toHaveLength(34);
  });

  test("every key between them is one edge, of the strongest kind true of it", () => {
    expect(kinds(model)).toEqual({
      "web_sales.ws_bill_cdemo_sk>customer_demographics.cd_demo_sk": "used",
      "web_sales.ws_ship_cdemo_sk>customer_demographics.cd_demo_sk": "alternative:tied",
      "web_returns.wr_refunded_cdemo_sk>customer_demographics.cd_demo_sk": "alternative:withdrawn",
      "web_returns.wr_returning_cdemo_sk>customer_demographics.cd_demo_sk": "alternative:withdrawn",
      "web_returns.wr_item_sk+wr_order_number>web_sales.ws_item_sk+ws_order_number": "planned",
      "web_sales.ws_web_page_sk>web_page.wp_web_page_sk": "planned",
      "web_returns.wr_web_page_sk>web_page.wp_web_page_sk": "alternative:withdrawn",
      "web_page.wp_access_date_sk>date_dim.d_date_sk": "planned",
      "web_page.wp_creation_date_sk>date_dim.d_date_sk": "alternative:tied",
      "web_sales.ws_sold_date_sk>date_dim.d_date_sk": "actual",
      "web_sales.ws_ship_date_sk>date_dim.d_date_sk": "alternative:withdrawn",
      "web_returns.wr_returned_date_sk>date_dim.d_date_sk": "alternative:withdrawn",
    });
    expect(model.undrawn).toEqual([]);
  });

  test("the join the SQL made outside the plan is drawn as that, with the sentence the trace stored for it", () => {
    const actual = model.edges.filter((edge) => edge.kind === "actual");
    expect(actual).toHaveLength(1);
    expect(actual[0].description).toBe(
      "Each date dimension row has many web sales rows, through their sold date surrogate key.",
    );
  });

  test("the edges a loud warning names are loud, and one only a quiet warning names is not", () => {
    expect(loud(model)).toEqual([
      "web_page.wp_access_date_sk>date_dim.d_date_sk",
      "web_sales.ws_bill_cdemo_sk>customer_demographics.cd_demo_sk",
      "web_sales.ws_ship_cdemo_sk>customer_demographics.cd_demo_sk",
      "web_sales.ws_sold_date_sk>date_dim.d_date_sk",
    ]);
    const quiet = GENDER.paths!.warnings.filter((warning) => !warning.loud);
    expect(quiet.flatMap((warning) => warning.other_joins)).toContain(
      "web_page.wp_creation_date_sk = date_dim.d_date_sk",
    );
  });

  test("every sentence shown is one the trace holds", () => {
    const stored = new Set(
      GENDER.paths!.attachments.flatMap((attachment) =>
        [attachment.selected, ...attachment.alternatives].flatMap((route) =>
          route ? [route.description, ...route.joins.map((join) => join.description)] : [],
        ),
      ),
    );
    for (const edge of model.edges) {
      expect(edge.description).not.toBeNull();
      expect(stored.has(edge.description as string)).toBe(true);
    }
  });
});

describe("a trace stored at trace_version 1", () => {
  const model = fromTrace(DAYS);

  test("is drawn like any other", () => {
    const counted: Record<string, number> = {};
    for (const kind of Object.values(kinds(model))) counted[kind] = (counted[kind] ?? 0) + 1;
    expect(counted).toEqual({ used: 2, planned: 2, "alternative:tied": 6, "alternative:withdrawn": 6 });
    expect(model.edges).toHaveLength(DAYS.subgraph!.edges.length);
  });

  test("a key of two columns is one edge", () => {
    const pair = model.edges.find((edge) => edge.fromColumns.length === 2);
    expect(pair?.id).toBe("catalog_returns.cr_item_sk+cr_order_number>catalog_sales.cs_item_sk+cs_order_number");
    expect(pair?.kind).toBe("planned");
    expect(pair?.source).toBe("overlay");
  });

  test("the three dates of the customer are loud: the loud warning names them as what it was tied with", () => {
    expect(loud(model)).toEqual([
      "catalog_sales.cs_bill_cdemo_sk>customer_demographics.cd_demo_sk",
      "catalog_sales.cs_ship_cdemo_sk>customer_demographics.cd_demo_sk",
      "catalog_sales.cs_sold_date_sk>date_dim.d_date_sk",
      "customer.c_first_sales_date_sk>date_dim.d_date_sk",
      "customer.c_first_shipto_date_sk>date_dim.d_date_sk",
      "customer.c_last_review_date_sk>date_dim.d_date_sk",
    ]);
  });
});

// ---- nothing but the trace's own copy (DD-05, NFR-18) --------------------------

describe("only the trace's own subgraph is drawn", () => {
  test.each([
    ["version 2", GENDER],
    ["version 1", DAYS],
    ["by hand", tiny()],
  ])("%s: every node and every end of every edge is in the subgraph", (_, trace) => {
    const model = fromTrace(trace);
    const tables = new Set(trace.subgraph!.nodes.map((node) => node.table));
    expect(model.nodes.map((node) => node.id)).toEqual([...tables]);
    for (const edge of model.edges) {
      expect(tables.has(edge.fromTable) && tables.has(edge.toTable)).toBe(true);
    }
  });

  test("a selected join whose key the subgraph does not hold is not drawn", () => {
    const stray = key("sale.store_sk", "store.store_id");
    const model = fromTrace(tiny({ selected: [{ ...BILL, use: "present" }, { ...stray, use: "present" }] }));
    expect(model.edges.map((edge) => edge.id)).not.toContain(edgeId(stray));
    expect(model.nodes.map((node) => node.id)).not.toContain("store");
  });

  test("a join the SQL made with a table that is not shown is listed in words and given no node", () => {
    const model = fromTrace(tiny({ actual: [made("sale.item_sk", "warehouse.warehouse_id")] }));
    expect(model.undrawn).toEqual(["sale.item_sk = warehouse.warehouse_id"]);
    expect(model.nodes.map((node) => node.id)).toEqual(["sale", "address", "item"]);
    expect(model.edges.some((edge) => edge.kind === "actual")).toBe(false);
  });

  test("and so is one on a column the table shown does not have", () => {
    const model = fromTrace(tiny({ actual: [made("sale.nothing", "address.state")] }));
    expect(model.undrawn).toEqual(["sale.nothing = address.state"]);
  });

  test("a trace with no subgraph draws nothing", () => {
    expect(fromTrace({ ...tiny(), subgraph: null })).toEqual({ nodes: [], edges: [], undrawn: [] });
  });

  test("a trace with no paths draws its keys as other keys", () => {
    const model = fromTrace({ ...tiny(), paths: null });
    expect(new Set(Object.values(kinds(model)))).toEqual(new Set(["other"]));
  });
});

// ---- each rule, by hand ----------------------------------------------------------

describe("the kind of an edge", () => {
  test("selected and made, selected and not made, tied, and on no route", () => {
    expect(kinds(fromTrace(tiny()))).toEqual({
      [edgeId(BILL)]: "used",
      [edgeId(SHIP)]: "alternative:tied",
      [edgeId(ITEM)]: "planned",
      [edgeId(SPARE)]: "other",
    });
  });

  test("a key made on part of its columns counts as made", () => {
    expect(kinds(fromTrace(tiny({ selected: [{ ...BILL, use: "partial" }] })))[edgeId(BILL)]).toBe("used");
  });

  test("when no SQL was checked the selected joins are planned, not used", () => {
    const model = fromTrace(tiny({ selected: [{ ...BILL, use: null }, { ...ITEM, use: null }] }));
    expect(kinds(model)[edgeId(BILL)]).toBe("planned");
    expect(kinds(model)[edgeId(ITEM)]).toBe("planned");
  });

  test("the tied route the SQL took instead is drawn as what the SQL did, whichever way round it wrote the join", () => {
    for (const actual of [made("sale.ship_sk", "address.address_id"), made("address.address_id", "sale.ship_sk")]) {
      const model = fromTrace(tiny({ selected: [{ ...BILL, use: "missing" }], actual: [actual] }));
      expect(kinds(model)[edgeId(SHIP)]).toBe("actual");
      expect(kinds(model)[edgeId(BILL)]).toBe("planned");
      expect(model.edges.find((edge) => edge.id === edgeId(SHIP))?.description).toBe("Sentence: shipping.");
    }
  });

  test("a join the SQL made that the plan selected is not drawn as outside the plan", () => {
    const model = fromTrace(tiny({ actual: [made("sale.bill_sk", "address.address_id", false)] }));
    expect(Object.values(kinds(model))).not.toContain("actual");
  });

  test("a join the SQL made on columns that are no key is drawn between them, with no sentence", () => {
    const model = fromTrace(tiny({ actual: [made("sale.note", "address.city")] }));
    const free = model.edges.find((edge) => edge.id === "sale.note>address.city");
    expect(free).toMatchObject({ kind: "actual", description: null, source: null, fromTable: "sale", toTable: "address" });
    expect(model.undrawn).toEqual([]);
    expect(model.edges).toHaveLength(5);
  });

  test("a join the SQL made on one column of a key of two is that key", () => {
    const pair: SchemaEdge = { ...key("item.maker_sk", "address.address_id"), from_columns: ["maker_sk", "category"], to_columns: ["address_id", "city"] };
    const model = fromTrace(tiny({ edges: [BILL, SHIP, ITEM, pair], actual: [made("address.city", "item.category")] }));
    expect(kinds(model)[edgeId(pair)]).toBe("actual");
    expect(model.edges).toHaveLength(4);
  });

  test("a join selected for one table stays selected though a route weighed for another passes along it", () => {
    const through = { ...route([SPARE, ITEM], "the maker's address"), status: "tied" };
    const model = fromTrace(tiny({ alternatives: [through] }));
    expect(kinds(model)[edgeId(ITEM)]).toBe("planned");
    expect(kinds(model)[edgeId(SPARE)]).toBe("alternative:tied");
  });

  test("and keeps its own sentence; the other join of that longer route is told by its own clause", () => {
    const through = { ...route([SPARE, ITEM], "the maker's address"), status: "tied" };
    const model = fromTrace(tiny({ alternatives: [through] }));
    expect(model.edges.find((edge) => edge.id === edgeId(ITEM))?.description).toBe("Sentence: item.");
    expect(model.edges.find((edge) => edge.id === edgeId(SPARE))?.description).toBe(
      "clause: the maker's address by maker_sk",
    );
  });

  test("a key tied on one route and set aside on another is drawn as tied, whichever came first", () => {
    const tied = { ...route([SPARE], "tied"), status: "tied" };
    const aside = { ...route([SPARE], "set aside"), status: "withdrawn" };
    expect(kinds(fromTrace(tiny({ alternatives: [tied, aside] })))[edgeId(SPARE)]).toBe("alternative:tied");
    expect(kinds(fromTrace(tiny({ alternatives: [aside, tied] })))[edgeId(SPARE)]).toBe("alternative:tied");
    expect(kinds(fromTrace(tiny({ alternatives: [aside] })))[edgeId(SPARE)]).toBe("alternative:withdrawn");
    expect(kinds(fromTrace(tiny({ alternatives: [aside, aside] })))[edgeId(SPARE)]).toBe("alternative:withdrawn");
  });

  test("a route of one join is told by the route's sentence", () => {
    expect(fromTrace(tiny()).edges.find((edge) => edge.id === edgeId(BILL))?.description).toBe("Sentence: billing.");
  });

  test("a key on no route has no sentence, and none is made up for it", () => {
    expect(fromTrace(tiny()).edges.find((edge) => edge.id === edgeId(SPARE))?.description).toBeNull();
  });
});

describe("which edges are loud", () => {
  test("the chosen join and the one it was tied with, when the warning is loud", () => {
    expect(loud(fromTrace(tiny()))).toEqual([edgeId(BILL), edgeId(SHIP)].sort());
  });

  test("none when no warning is loud", () => {
    expect(loud(fromTrace(tiny({ warnings: [warning({ loud: false, state: "not_used" })] })))).toEqual([]);
    expect(loud(fromTrace(tiny({ warnings: [] })))).toEqual([]);
  });

  test("`loud` is read as stored and never worked out from the state", () => {
    expect(loud(fromTrace(tiny({ warnings: [warning({ loud: false, state: "followed" })] })))).toEqual([]);
    expect(loud(fromTrace(tiny({ warnings: [warning({ loud: true, state: "not_used" })] })))).toHaveLength(2);
  });

  test("a join is written as a warning writes it", () => {
    expect(joinText(BILL)).toBe("sale.bill_sk = address.address_id");
    expect(joinText({ from_table: "r", from_columns: ["a", "b"], to_table: "s", to_columns: ["c", "d"] })).toBe(
      "r.a = s.c and r.b = s.d",
    );
  });
});

describe("the columns the question matched", () => {
  test("the element chosen for a term, when it is a column", () => {
    expect(matchedColumns(GENDER).get("customer_demographics")).toEqual(new Set(["cd_gender"]));
    expect(fromTrace(GENDER).nodes.find((node) => node.id === "customer_demographics")?.matched).toEqual(["cd_gender"]);
  });

  test("and the best-scored few, by rank", () => {
    const best = GENDER.retrieval!.candidates.filter((candidate) => candidate.kind === "column")
      .sort((a, b) => a.rank - b.rank)
      .slice(0, MATCHED_TOP);
    expect(best).toHaveLength(5);
    for (const candidate of best) {
      expect(matchedColumns(GENDER).get(candidate.table)?.has(candidate.column as string)).toBe(true);
    }
    const sixth = GENDER.retrieval!.candidates.filter((candidate) => candidate.kind === "column")
      .sort((a, b) => a.rank - b.rank)[MATCHED_TOP];
    expect(fromTrace(GENDER).nodes.find((node) => node.id === sixth.table)?.matched).not.toContain(sixth.column);
  });

  test("a term whose element is a table adds no column", () => {
    const terms = GENDER.retrieval!.term_alternatives.filter((term) => !term.chosen_element.includes("."));
    expect(terms.length).toBeGreaterThan(0);
    expect(matchedColumns(GENDER).has("date_dim")).toBe(false);
  });

  test("a table among the best-scored takes no column's place", () => {
    const scored = (name: string, rank: number) => {
      const [table, column] = name.split(".");
      return { kind: column ? "column" : "table", name, table, column: column ?? null, rank };
    };
    const candidates = [
      scored("sale", 1),
      scored("sale.amount", 2),
      scored("address", 3),
      scored("sale.note", 4),
      scored("address.state", 5),
      scored("address.city", 6),
      scored("item.category", 7),
      scored("item.maker_sk", 8),
    ];
    const trace = { ...tiny(), retrieval: { term_alternatives: [], candidates } } as unknown as TraceDocument;
    const matched = Object.fromEntries(fromTrace(trace).nodes.map((node) => [node.id, node.matched]));
    expect(matched).toEqual({ sale: ["amount", "note"], address: ["state", "city"], item: ["category"] });
  });

  test("none without retrieval", () => {
    expect(fromTrace(tiny()).nodes.every((node) => node.matched.length === 0)).toBe(true);
  });
});
