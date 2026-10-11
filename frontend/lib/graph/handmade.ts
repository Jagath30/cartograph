/**
 * THROWAWAY (R-05). A graph written by hand, after figure 3 of the Design:
 * a sale reaches a region by the address on the order or by the store.
 * It proves the library, the layout and the styling before any trace is
 * drawn. Deleted with app/throwaway once GraphView draws a real one.
 */
import type { GraphColumn, GraphEdge, GraphModel } from "./model";

function columns(prefix: string, table: string, names: string[], keys: string[], primary?: string): GraphColumn[] {
  return names.map((name) => ({
    name: `${prefix}_${name}`,
    readable: `${table} — ${name.replace(/_sk$/, " key").replaceAll("_", " ")}`,
    dataType: name.endsWith("_sk") ? "bigint" : name.includes("price") ? "numeric" : "text",
    primaryKey: name === primary,
    foreignKey: keys.includes(name),
  }));
}

function key(
  kind: GraphEdge["kind"],
  from: [string, string],
  to: [string, string],
  description: string | null,
  extra: Partial<GraphEdge> = {},
): GraphEdge {
  return {
    id: `${from[0]}.${from[1]}=${to[0]}.${to[1]}`,
    kind,
    fromTable: from[0],
    fromColumns: [from[1]],
    toTable: to[0],
    toColumns: [to[1]],
    description,
    status: null,
    source: "catalog",
    loud: false,
    ...extra,
  };
}

export const HANDMADE: GraphModel = {
  nodes: [
    {
      id: "store_sales",
      readable: "store sales",
      anchor: true,
      inTree: true,
      matched: ["ss_ext_sales_price"],
      columns: columns(
        "ss",
        "store sales",
        ["sold_date_sk", "item_sk", "customer_sk", "addr_sk", "store_sk", "quantity", "sales_price", "ext_sales_price", "net_paid", "net_profit"],
        ["sold_date_sk", "item_sk", "customer_sk", "addr_sk", "store_sk"],
      ),
    },
    {
      id: "customer_address",
      readable: "customer address",
      anchor: true,
      inTree: true,
      matched: ["ca_state"],
      columns: columns("ca", "customer address", ["address_sk", "city", "county", "state", "zip", "country"], [], "address_sk"),
    },
    {
      id: "store",
      readable: "store",
      anchor: true,
      inTree: false,
      matched: ["s_state"],
      columns: columns("s", "store", ["store_sk", "store_name", "city", "state", "manager"], [], "store_sk"),
    },
    {
      id: "item",
      readable: "item",
      anchor: true,
      inTree: true,
      matched: ["i_category"],
      columns: columns("i", "item", ["item_sk", "brand", "class", "category", "product_name"], [], "item_sk"),
    },
    {
      id: "date_dim",
      readable: "date dimension",
      anchor: true,
      inTree: true,
      matched: ["d_year"],
      columns: columns("d", "date", ["date_sk", "date", "year", "qoy", "moy", "day_name"], [], "date_sk"),
    },
  ],
  edges: [
    key(
      "used",
      ["store_sales", "ss_addr_sk"],
      ["customer_address", "ca_address_sk"],
      "Each store sales row has one customer address, through its address key: the address on the order.",
      { loud: true },
    ),
    key(
      "alternative",
      ["store_sales", "ss_store_sk"],
      ["store", "s_store_sk"],
      "Each store sales row has one store, through its store key: the store where the sale happened.",
      { status: "tied", loud: true },
    ),
    key("used", ["store_sales", "ss_item_sk"], ["item", "i_item_sk"], "Each store sales row has one item, through its item key."),
    key(
      "planned",
      ["store_sales", "ss_sold_date_sk"],
      ["date_dim", "d_date_sk"],
      "Each store sales row has one date, through its sold date key.",
    ),
  ],
  undrawn: [],
};
