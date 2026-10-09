"""ConformanceCheck (FR-39, DD-13, DD-20, NFR-26, criterion 13, T-02).

Every statement here was written by hand, before any model had written
one. The schema is TPC-DS from the committed DDL, so all of it runs in CI.

NFR-26 and T-02 are the reason for most of this file: SQL that joins
differently from the path while looking as if it conforms. If one of
those passes, the checker is decorative.
"""

import pytest

from app.core.conformance import REASONS, Equality, compare, edges_of, extract_joins
from app.core.path_finder import Join
from app.core.sql_reading import schema_of
from tests.core.tpcds_files import ddl_snapshot

SCHEMA = schema_of(ddl_snapshot())


def col(text: str) -> tuple[str, str]:
    table, column = text.split(".")
    return (table, column)


def edge(*pairs: str):
    """edge("a.x = b.y") or, for a two-column key, edge("a.x = b.y", "a.z = b.w")."""
    return tuple(tuple(col(side.strip()) for side in pair.split("=")) for pair in pairs)


def joins(sql: str) -> set[str]:
    """The equalities extracted, as text, outer joins marked."""
    return {
        f"{e.left[0]}.{e.left[1]} = {e.right[0]}.{e.right[1]}" + (" OUTER" if e.outer else "")
        for e in extract_joins(sql, SCHEMA).equalities
    }


def reasons(sql: str) -> set[str]:
    return {item.reason for item in extract_joins(sql, SCHEMA).unchecked}


def outcome(sql: str, *edges) -> str:
    return compare(extract_joins(sql, SCHEMA), tuple(edges)).outcome


SS_ITEM = edge("store_sales.ss_item_sk = item.i_item_sk")
CS_ITEM = edge("catalog_sales.cs_item_sk = item.i_item_sk")
SS_DATE = edge("store_sales.ss_sold_date_sk = date_dim.d_date_sk")
SS_STORE = edge("store_sales.ss_store_sk = store.s_store_sk")
CS_BILL = edge("catalog_sales.cs_bill_addr_sk = customer_address.ca_address_sk")
SR_SS = edge("store_returns.sr_item_sk = store_sales.ss_item_sk", "store_returns.sr_ticket_number = store_sales.ss_ticket_number")


# --------------------------------------------------------------------------
# Extraction: the forms a join is written in
# --------------------------------------------------------------------------


def test_an_explicit_join_is_read_with_its_aliases_resolved() -> None:
    sql = "SELECT i.i_category FROM store_sales AS ss JOIN item AS i ON ss.ss_item_sk = i.i_item_sk"
    assert joins(sql) == {"item.i_item_sk = store_sales.ss_item_sk"}
    assert extract_joins(sql, SCHEMA).tables == ("item", "store_sales")
    assert reasons(sql) == set()


def test_a_comma_join_with_where_equalities_is_read_the_same_way() -> None:
    """Correction 9: the benchmark's own queries have no JOIN keyword."""
    sql = """select d_year, i_brand_id, sum(ss_ext_sales_price)
             from date_dim, store_sales, item
             where d_date_sk = ss_sold_date_sk and ss_item_sk = i_item_sk and i_manufact_id = 128 and d_moy = 11
             group by d_year, i_brand_id"""
    assert joins(sql) == {"date_dim.d_date_sk = store_sales.ss_sold_date_sk", "item.i_item_sk = store_sales.ss_item_sk"}
    assert reasons(sql) == set()
    assert outcome(sql, SS_DATE, SS_ITEM) == "conforms"


def test_the_two_sides_of_an_equality_may_be_written_either_way_round() -> None:
    a = "SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk"
    b = "SELECT 1 FROM item i JOIN store_sales ss ON (i.i_item_sk) = (ss.ss_item_sk)"
    assert joins(a) == joins(b) and outcome(b, SS_ITEM) == "conforms"


def test_names_are_read_in_lower_case_however_they_are_written() -> None:
    sql = "SELECT 1 FROM STORE_SALES AS SS JOIN ITEM AS I ON SS.SS_ITEM_SK = I.I_ITEM_SK"
    assert joins(sql) == {"item.i_item_sk = store_sales.ss_item_sk"} and outcome(sql, SS_ITEM) == "conforms"


def test_a_table_of_the_same_name_in_another_schema_is_not_the_warehouse_s() -> None:
    sql = "SELECT 1 FROM elsewhere.store_sales AS ss JOIN item AS i ON ss.ss_item_sk = i.i_item_sk"
    assert joins(sql) == set() and reasons(sql) == {"unresolved_column"}
    assert extract_joins(sql, SCHEMA).tables == ("elsewhere.store_sales", "item")
    assert extract_joins(sql, SCHEMA).cross_joins == ()
    assert outcome(sql, SS_ITEM) == "not_checked"


def test_on_and_where_may_be_mixed() -> None:
    sql = """SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk, date_dim d
             WHERE ss.ss_sold_date_sk = d.d_date_sk AND d.d_year = 2001"""
    assert outcome(sql, SS_ITEM, SS_DATE) == "conforms"


def test_a_filter_is_not_a_join() -> None:
    sql = """SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk AND i.i_category = 'Books'
             WHERE ss.ss_quantity > 1 AND ss.ss_list_price = ss.ss_sales_price AND (i.i_class = 'a' OR i.i_class = 'b')"""
    assert joins(sql) == {"item.i_item_sk = store_sales.ss_item_sk"} and reasons(sql) == set()


def test_a_two_column_key_is_two_equalities_and_one_edge() -> None:
    sql = """SELECT 1 FROM store_returns sr JOIN store_sales ss
             ON sr.sr_item_sk = ss.ss_item_sk AND sr.sr_ticket_number = ss.ss_ticket_number"""
    assert len(joins(sql)) == 2
    result = compare(extract_joins(sql, SCHEMA), (SR_SS,))
    assert result.outcome == "conforms" and result.present == (SR_SS,)


def test_no_join_and_no_path_conforms_and_no_join_with_a_path_is_incomplete() -> None:
    sql = "SELECT s_store_name FROM store"
    assert outcome(sql) == "conforms"
    result = compare(extract_joins(sql, SCHEMA), (SS_STORE,))
    assert result.outcome == "incomplete" and result.missing == (SS_STORE,)
    assert result.findings == ("an edge of the path is not used: store_sales.ss_store_sk = store.s_store_sk",)


def test_a_tree_s_joins_become_edges_with_every_column_pair() -> None:
    one = Join("store_sales", ("ss_item_sk",), "item", ("i_item_sk",), "catalog", "fk", "many_to_one")
    two = Join("store_returns", ("sr_item_sk", "sr_ticket_number"), "store_sales", ("ss_item_sk", "ss_ticket_number"),
               "overlay", None, "many_to_one")  # fmt: skip
    assert edges_of((one, two)) == (SS_ITEM, SR_SS)


# --------------------------------------------------------------------------
# NFR-26: joins differently while appearing to conform
# --------------------------------------------------------------------------


def test_the_right_tables_on_the_wrong_column_is_diverged() -> None:
    """The shipping address where the path says the billing address."""
    sql = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk"
    result = compare(extract_joins(sql, SCHEMA), (CS_BILL,))
    assert result.outcome == "diverged"
    assert result.foreign == (Equality(("catalog_sales", "cs_ship_addr_sk"), ("customer_address", "ca_address_sk")),)
    assert result.missing == (CS_BILL,)
    assert "joins outside the path: catalog_sales.cs_ship_addr_sk = customer_address.ca_address_sk" in result.findings


def test_the_wrong_column_is_diverged_in_the_comma_form_too() -> None:
    sql = "select ca_state from catalog_sales, customer_address where cs_ship_addr_sk = ca_address_sk"
    assert outcome(sql, CS_BILL) == "diverged"


def test_both_address_keys_at_once_is_diverged() -> None:
    """The right join is there, and so is another one."""
    sql = """SELECT 1 FROM catalog_sales cs JOIN customer_address ca
             ON cs.cs_bill_addr_sk = ca.ca_address_sk AND cs.cs_ship_addr_sk = ca.ca_address_sk"""
    result = compare(extract_joins(sql, SCHEMA), (CS_BILL,))
    assert result.outcome == "diverged" and result.present == (CS_BILL,) and len(result.foreign) == 1


def test_an_alias_spelled_like_another_table_does_not_fool_it() -> None:
    """`store_returns` here is store_sales. Read by its alias this would
    look like the return-to-sale key; it is a self-join of store_sales."""
    sql = """SELECT 1 FROM store_sales AS store_returns JOIN store_sales AS ss
             ON store_returns.ss_item_sk = ss.ss_item_sk"""
    assert reasons(sql) == {"self_join"}
    sql = "SELECT 1 FROM catalog_sales AS store_sales JOIN item AS i ON store_sales.cs_item_sk = i.i_item_sk"
    assert joins(sql) == {"catalog_sales.cs_item_sk = item.i_item_sk"}
    assert outcome(sql, SS_ITEM) == "diverged"


def test_a_return_joined_to_its_sale_on_the_item_alone_is_a_wrong_join() -> None:
    sql = "SELECT 1 FROM store_returns sr JOIN store_sales ss ON sr.sr_item_sk = ss.ss_item_sk"
    result = compare(extract_joins(sql, SCHEMA), (SR_SS,))
    assert result.outcome == "diverged" and result.partial == (SR_SS,) and result.present == ()
    assert result.findings[0].startswith("only part of a two-column key is joined")


def test_a_two_column_key_half_made_through_another_table_is_still_half() -> None:
    """Both joined to item: the item halves are equal by transitivity, and
    the ticket numbers are not joined at all."""
    sql = """SELECT 1 FROM store_returns sr, store_sales ss, item i
             WHERE sr.sr_item_sk = i.i_item_sk AND ss.ss_item_sk = i.i_item_sk"""
    assert outcome(sql, SR_SS, SS_ITEM) == "diverged"


def test_a_join_to_a_table_the_path_does_not_have_is_diverged() -> None:
    sql = """SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk
             JOIN customer c ON ss.ss_customer_sk = c.c_customer_sk"""
    result = compare(extract_joins(sql, SCHEMA), (SS_ITEM,))
    assert result.outcome == "diverged" and result.present == (SS_ITEM,)


def test_an_equality_that_is_no_foreign_key_at_all_is_still_a_join_outside_the_path() -> None:
    sql = "SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_quantity = i.i_item_sk"
    assert outcome(sql, SS_ITEM) == "diverged"


def test_a_table_with_no_join_condition_is_a_cross_join_and_diverged() -> None:
    for sql in (
        "SELECT 1 FROM store_sales ss, item i WHERE ss.ss_quantity > 1",
        "SELECT 1 FROM store_sales ss CROSS JOIN item i",
        "SELECT 1 FROM store_sales ss JOIN item i ON true",
        "SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk, date_dim d",
    ):
        result = compare(extract_joins(sql, SCHEMA), (SS_ITEM, SS_DATE))
        assert result.outcome == "diverged", sql
        assert len(result.cross_joins) == 1 and any("cross join" in finding for finding in result.findings)


def test_a_single_row_of_totals_set_beside_a_table_is_not_a_cross_join() -> None:
    """A share of a total. The derived source is one row by construction:
    aggregates and no GROUP BY."""
    sql = """SELECT s.s_store_name, SUM(ss.ss_net_paid) / MAX(t.total)
             FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk
             CROSS JOIN (SELECT SUM(ss_net_paid) AS total FROM store_sales) AS t
             GROUP BY s.s_store_name"""
    assert outcome(sql, SS_STORE) == "conforms"
    grouped = sql.replace("AS total FROM store_sales", "AS total FROM store_sales GROUP BY ss_store_sk")
    assert outcome(grouped, SS_STORE) == "diverged"
    windowed = sql.replace("SUM(ss_net_paid) AS total", "SUM(ss_net_paid) OVER () AS total")
    assert outcome(windowed, SS_STORE) == "diverged"


# --------------------------------------------------------------------------
# Comparison by meaning: equivalence classes
# --------------------------------------------------------------------------

THROUGH_ITEM = """SELECT 1 FROM store_sales ss, catalog_sales cs, item i
                  WHERE ss.ss_item_sk = i.i_item_sk AND cs.cs_item_sk = i.i_item_sk"""
DIRECT_WITH_ITEM = """SELECT 1 FROM store_sales ss, catalog_sales cs, item i
                      WHERE ss.ss_item_sk = cs.cs_item_sk AND cs.cs_item_sk = i.i_item_sk"""
DIRECT_WITHOUT_ITEM = "SELECT 1 FROM store_sales ss JOIN catalog_sales cs ON ss.ss_item_sk = cs.cs_item_sk"


def test_through_item_and_directly_are_one_join_when_item_is_joined() -> None:
    assert outcome(THROUGH_ITEM, SS_ITEM, CS_ITEM) == "conforms"
    assert outcome(DIRECT_WITH_ITEM, SS_ITEM, CS_ITEM) == "conforms"
    extraction = extract_joins(DIRECT_WITH_ITEM, SCHEMA)
    assert extraction.classes == (
        frozenset({("store_sales", "ss_item_sk"), ("catalog_sales", "cs_item_sk"), ("item", "i_item_sk")}),
    )


def test_two_fact_tables_joined_directly_without_item_is_incomplete() -> None:
    result = compare(extract_joins(DIRECT_WITHOUT_ITEM, SCHEMA), (SS_ITEM, CS_ITEM))
    assert result.outcome == "incomplete"
    assert result.foreign == () and set(result.missing) == {SS_ITEM, CS_ITEM}


def test_the_same_direct_join_is_foreign_to_a_path_that_does_not_make_the_two_equal() -> None:
    assert outcome(DIRECT_WITHOUT_ITEM, SS_ITEM) == "diverged"
    assert outcome(DIRECT_WITHOUT_ITEM, SS_ITEM, SS_DATE) == "diverged"


def test_an_extra_equality_that_merges_two_classes_of_the_path_is_diverged() -> None:
    sql = """SELECT 1 FROM store_sales ss, item i, store s
             WHERE ss.ss_item_sk = i.i_item_sk AND ss.ss_store_sk = s.s_store_sk AND i.i_item_sk = s.s_store_sk"""
    result = compare(extract_joins(sql, SCHEMA), (SS_ITEM, SS_STORE))
    assert result.outcome == "diverged" and set(result.present) == {SS_ITEM, SS_STORE}
    assert [finding for finding in result.findings if "outside the path" in finding] == [
        "joins outside the path: item.i_item_sk = store.s_store_sk"
    ]


def test_an_equality_the_path_implies_is_not_foreign_however_it_is_spelled() -> None:
    """Three tables on one key: any two of the three equalities say it."""
    edges = (SS_ITEM, CS_ITEM, edge("web_sales.ws_item_sk = item.i_item_sk"))
    sql = """SELECT 1 FROM store_sales ss, catalog_sales cs, web_sales ws, item i
             WHERE ss.ss_item_sk = cs.cs_item_sk AND cs.cs_item_sk = ws.ws_item_sk AND ws.ws_item_sk = i.i_item_sk"""
    assert outcome(sql, *edges) == "conforms"


def test_two_uses_of_one_table_in_two_ctes_are_not_merged_into_one() -> None:
    """Each channel has its own date_dim. Their dates are not thereby equal
    to each other, and no class says so."""
    sql = """WITH s AS (SELECT ss.ss_item_sk AS item_sk FROM store_sales ss JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk),
                  c AS (SELECT cs.cs_item_sk AS item_sk FROM catalog_sales cs JOIN date_dim d ON cs.cs_sold_date_sk = d.d_date_sk)
             SELECT 1 FROM s JOIN c ON s.item_sk = c.item_sk"""
    extraction = extract_joins(sql, SCHEMA)
    assert extraction.unchecked == ()
    assert not any(
        ("store_sales", "ss_sold_date_sk") in members and ("catalog_sales", "cs_sold_date_sk") in members
        for members in extraction.classes
    )
    assert len(extraction.classes) == 3


# --------------------------------------------------------------------------
# Outer joins take no part in transitivity
# --------------------------------------------------------------------------


def test_an_outer_join_on_a_pair_of_the_path_is_that_edge() -> None:
    sql = "SELECT 1 FROM store_sales ss LEFT JOIN item i ON ss.ss_item_sk = i.i_item_sk"
    assert joins(sql) == {"item.i_item_sk = store_sales.ss_item_sk OUTER"}
    assert outcome(sql, SS_ITEM) == "conforms"
    assert extract_joins(sql, SCHEMA).classes == ()


@pytest.mark.parametrize("kind", ["LEFT", "RIGHT", "FULL", "LEFT OUTER", "FULL OUTER"])
def test_an_outer_join_does_not_make_its_two_sides_equal_for_anything_else(kind) -> None:
    """Inner, this conforms by transitivity. Outer, store_sales to
    catalog_sales is a join of its own, and the path has no such pair."""
    sql = f"""SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk
              {kind} JOIN catalog_sales cs ON ss.ss_item_sk = cs.cs_item_sk"""
    result = compare(extract_joins(sql, SCHEMA), (SS_ITEM, CS_ITEM))
    assert result.outcome == "diverged"
    assert result.present == (SS_ITEM,) and result.missing == (CS_ITEM,)
    assert [equality.outer for equality in result.foreign] == [True]
    inner = sql.replace(f"{kind} JOIN", "JOIN")
    assert outcome(inner, SS_ITEM, CS_ITEM) == "conforms"


def test_an_outer_join_on_the_wrong_column_is_diverged() -> None:
    sql = "SELECT 1 FROM catalog_sales cs LEFT JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk"
    assert outcome(sql, CS_BILL) == "diverged"


# --------------------------------------------------------------------------
# CTEs and derived tables (option 2)
# --------------------------------------------------------------------------


def test_joins_inside_a_cte_and_on_its_passed_through_column_are_read() -> None:
    sql = """WITH per_item AS (
                 SELECT ss.ss_item_sk, SUM(ss.ss_net_paid) AS paid
                 FROM store_sales ss JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
                 WHERE d.d_year = 2001 GROUP BY ss.ss_item_sk)
             SELECT i.i_category, SUM(p.paid) FROM per_item p JOIN item i ON i.i_item_sk = p.ss_item_sk GROUP BY i.i_category"""
    assert joins(sql) == {"date_dim.d_date_sk = store_sales.ss_sold_date_sk", "item.i_item_sk = store_sales.ss_item_sk"}
    assert reasons(sql) == set()
    assert outcome(sql, SS_ITEM, SS_DATE) == "conforms"


def test_a_passed_through_column_is_followed_under_a_new_name() -> None:
    sql = """WITH s AS (SELECT ss_item_sk AS the_item, ss_net_paid FROM store_sales)
             SELECT 1 FROM s JOIN item i ON s.the_item = i.i_item_sk"""
    assert joins(sql) == {"item.i_item_sk = store_sales.ss_item_sk"} and outcome(sql, SS_ITEM) == "conforms"


def test_a_renamed_column_is_followed_to_what_it_is_and_not_to_what_it_is_called() -> None:
    """The CTE calls the shipping address key by the billing key's name."""
    sql = """WITH cs AS (SELECT cs_ship_addr_sk AS cs_bill_addr_sk FROM catalog_sales)
             SELECT 1 FROM cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"""
    assert joins(sql) == {"catalog_sales.cs_ship_addr_sk = customer_address.ca_address_sk"}
    assert outcome(sql, CS_BILL) == "diverged"


def test_a_cte_named_like_a_table_of_the_schema_is_the_cte() -> None:
    """`item` here is catalog_sales under another name. The join looks like
    store_sales to item and is store_sales to catalog_sales."""
    sql = """WITH item AS (SELECT cs_item_sk AS i_item_sk FROM catalog_sales)
             SELECT 1 FROM store_sales ss JOIN item ON ss.ss_item_sk = item.i_item_sk"""
    extraction = extract_joins(sql, SCHEMA)
    assert joins(sql) == {"catalog_sales.cs_item_sk = store_sales.ss_item_sk"}
    assert extraction.tables == ("catalog_sales", "store_sales")
    assert outcome(sql, SS_ITEM) == "diverged"


def test_a_cte_inside_a_cte_is_followed_all_the_way_down() -> None:
    sql = """WITH a AS (SELECT ss_item_sk AS k1, ss_sold_date_sk FROM store_sales),
                  b AS (SELECT a.k1 AS k2 FROM a JOIN date_dim d ON a.ss_sold_date_sk = d.d_date_sk)
             SELECT 1 FROM b JOIN item i ON b.k2 = i.i_item_sk"""
    assert joins(sql) == {"date_dim.d_date_sk = store_sales.ss_sold_date_sk", "item.i_item_sk = store_sales.ss_item_sk"}
    assert outcome(sql, SS_ITEM, SS_DATE) == "conforms"
    nested = """WITH b AS (WITH a AS (SELECT ss_item_sk AS k1 FROM store_sales) SELECT k1 AS k2 FROM a)
                SELECT 1 FROM b JOIN item i ON b.k2 = i.i_item_sk"""
    assert joins(nested) == {"item.i_item_sk = store_sales.ss_item_sk"}


def test_a_derived_table_and_a_star_are_followed_too() -> None:
    sql = """SELECT 1 FROM (SELECT * FROM store_sales WHERE ss_quantity > 1) AS big
             JOIN item i ON big.ss_item_sk = i.i_item_sk"""
    assert joins(sql) == {"item.i_item_sk = store_sales.ss_item_sk"} and reasons(sql) == set()
    renamed = "SELECT 1 FROM (SELECT ss_item_sk, ss_quantity FROM store_sales) AS d(k, q) JOIN item i ON d.k = i.i_item_sk"
    assert joins(renamed) == {"item.i_item_sk = store_sales.ss_item_sk"}


@pytest.mark.parametrize(
    "key",
    ["ss_item_sk + 0", "COALESCE(ss_item_sk, 0)", "MAX(ss_item_sk)", "CAST(ss_item_sk AS bigint)", "ss_item_sk * 1", "1"],
)
def test_a_join_on_a_column_the_cte_computed_is_not_checked(key) -> None:
    sql = f"""WITH s AS (SELECT {key} AS k FROM store_sales)
              SELECT 1 FROM s JOIN item i ON s.k = i.i_item_sk"""
    extraction = extract_joins(sql, SCHEMA)
    assert reasons(sql) == {"computed_join_key"} and extraction.equalities == ()
    assert outcome(sql, SS_ITEM) == "not_checked"


def test_two_channel_ctes_joined_on_their_item_keys() -> None:
    """The correct way to compare two channels per item. With item joined
    it conforms; without, it is incomplete; never diverged for its spelling."""
    with_item = """WITH s AS (SELECT ss_item_sk, SUM(ss_net_paid) AS paid FROM store_sales GROUP BY ss_item_sk),
                        c AS (SELECT cs_item_sk, SUM(cs_net_paid) AS paid FROM catalog_sales GROUP BY cs_item_sk)
                   SELECT i.i_item_id, s.paid, c.paid FROM s JOIN c ON s.ss_item_sk = c.cs_item_sk
                   JOIN item i ON i.i_item_sk = s.ss_item_sk"""
    assert outcome(with_item, SS_ITEM, CS_ITEM) == "conforms"
    without = with_item.replace("JOIN item i ON i.i_item_sk = s.ss_item_sk", "").replace("i.i_item_id, ", "")
    assert outcome(without, SS_ITEM, CS_ITEM) == "incomplete"


def test_a_cte_nothing_reads_contributes_nothing() -> None:
    sql = """WITH unused AS (SELECT 1 AS x FROM store_sales ss JOIN customer c ON ss.ss_customer_sk = c.c_customer_sk)
             SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk"""
    assert outcome(sql, SS_ITEM) == "conforms"


# --------------------------------------------------------------------------
# not_checked: never "conforms" (T-02)
# --------------------------------------------------------------------------

NOT_CHECKED = [
    ("self_join", "SELECT 1 FROM date_dim a JOIN date_dim b ON a.d_date_sk = b.d_date_sk"),
    ("self_join", "SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk JOIN item j ON ss.ss_item_sk = j.i_item_sk"),
    ("self_join", "WITH s AS (SELECT ss_item_sk AS k FROM store_sales) SELECT 1 FROM s a JOIN s b ON a.k = b.k"),
    # One CTE reached by two different routes is still one use of the table.
    ("self_join", "WITH s AS (SELECT ss_item_sk AS k FROM store_sales) SELECT 1 FROM (SELECT k FROM s) a JOIN (SELECT k FROM s) b ON a.k = b.k"),
    ("or", "SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk OR ss.ss_promo_sk = i.i_item_sk"),
    ("or", "SELECT 1 FROM store_sales ss, item i WHERE (ss.ss_item_sk = i.i_item_sk OR i.i_item_sk IS NULL)"),
    ("or", "SELECT 1 FROM catalog_sales cs, customer_address ca WHERE cs.cs_bill_addr_sk = ca.ca_address_sk OR cs.cs_ship_addr_sk = ca.ca_address_sk"),
    ("negated", "SELECT 1 FROM store_sales ss JOIN item i ON NOT (ss.ss_item_sk = i.i_item_sk)"),
    ("non_equality", "SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk > i.i_item_sk"),
    ("non_equality", "SELECT 1 FROM store_sales ss, item i WHERE ss.ss_item_sk <> i.i_item_sk"),
    ("non_equality", "SELECT 1 FROM store_sales ss JOIN date_dim d ON ss.ss_sold_date_sk BETWEEN d.d_date_sk AND d.d_date_sk + 7"),
    ("non_equality", "SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk IN (i.i_item_sk, i.i_manufact_id)"),
    ("computed_join_key", "SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk + 0 = i.i_item_sk"),
    ("computed_join_key", "SELECT 1 FROM store_sales ss JOIN item i ON CAST(ss.ss_item_sk AS bigint) = i.i_item_sk"),
    ("computed_join_key", "SELECT 1 FROM store_sales ss, item i WHERE COALESCE(ss.ss_item_sk, 0) = i.i_item_sk"),
    ("natural_join", "SELECT 1 FROM store_sales NATURAL JOIN item"),
    ("using", "SELECT 1 FROM date_dim a JOIN time_dim b USING (d_date_sk)"),
    ("set_operation", "SELECT ss_item_sk FROM store_sales UNION ALL SELECT cs_item_sk FROM catalog_sales"),
    ("set_operation", "WITH u AS (SELECT ss_item_sk AS k FROM store_sales UNION SELECT cs_item_sk FROM catalog_sales) SELECT 1 FROM u JOIN item i ON u.k = i.i_item_sk"),
    ("recursive_cte", "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 3) SELECT 1 FROM r"),
    ("correlated_subquery", "SELECT 1 FROM item i WHERE EXISTS (SELECT 1 FROM store_sales ss WHERE ss.ss_item_sk = i.i_item_sk)"),
    ("correlated_subquery", "SELECT 1 FROM item WHERE EXISTS (SELECT 1 FROM store_sales WHERE ss_item_sk = i_item_sk)"),
    ("correlated_subquery", "SELECT (SELECT COUNT(*) FROM store_sales ss WHERE ss.ss_item_sk = i.i_item_sk) FROM item i"),
    ("correlated_subquery", "SELECT 1 FROM item i WHERE i.i_current_price > (SELECT AVG(j.i_current_price) FROM item j WHERE j.i_category = i.i_category)"),
    ("subquery_predicate", "SELECT 1 FROM store_sales ss WHERE ss.ss_item_sk IN (SELECT i.i_item_sk FROM item i WHERE i.i_category = 'Books')"),
    ("subquery_predicate", "SELECT 1 FROM store_sales WHERE ss_item_sk NOT IN (SELECT i_item_sk FROM item)"),
    ("subquery_predicate", "SELECT 1 FROM store_sales WHERE ss_item_sk = (SELECT i_item_sk FROM item WHERE i_item_id = 'X')"),
    ("unread_source", "SELECT 1 FROM store_sales ss JOIN LATERAL (SELECT i.i_item_sk FROM item i WHERE i.i_item_sk = ss.ss_item_sk) x ON true"),
    ("unread_source", "SELECT 1 FROM store_sales ss, (VALUES (1), (2)) AS v(k) WHERE ss.ss_item_sk = v.k"),
    ("unresolved_column", "SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_nope"),
    ("unresolved_column", "SELECT 1 FROM date_dim a, date_dim b, store_sales WHERE ss_sold_date_sk = d_date_sk"),
    ("not_parsed", "SELECT (a FROM"),
    ("not_a_select", "DELETE FROM store_sales"),
    ("not_a_select", "SELEC nothing"),
    ("not_a_select", "VALUES (1)"),
    ("not_one_statement", "SELECT 1 FROM store; SELECT 2 FROM item"),
]


@pytest.mark.parametrize(("reason", "sql"), NOT_CHECKED)
def test_what_cannot_be_read_with_confidence_is_not_checked_with_its_reason(reason, sql) -> None:
    extraction = extract_joins(sql, SCHEMA)
    assert reason in {item.reason for item in extraction.unchecked}, [(i.reason, i.detail) for i in extraction.unchecked]
    result = compare(extraction, ())
    assert result.outcome in ("not_checked", "diverged")
    assert result.outcome != "conforms" and any(f"not checked ({reason})" in finding for finding in result.findings)


def test_every_reason_is_one_of_the_fixed_words() -> None:
    for reason, sql in NOT_CHECKED:
        assert reason in REASONS
        assert {item.reason for item in extract_joins(sql, SCHEMA).unchecked} <= set(REASONS)
    assert {reason for reason, _ in NOT_CHECKED} == set(REASONS)


def test_a_subquery_that_only_computes_a_value_is_not_a_join() -> None:
    """The latest date, a maximum, an average: values, however they are
    compared. The join around them is still checked."""
    sql = """SELECT 1 FROM store_sales ss JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
             WHERE d.d_date = (SELECT MAX(d_date) FROM date_dim)
               AND ss.ss_net_paid > (SELECT AVG(ss_net_paid) FROM store_sales)
               AND EXISTS (SELECT 1 FROM store)"""
    assert reasons(sql) == set() and outcome(sql, SS_DATE) == "conforms"


def test_joins_inside_a_subquery_that_computes_a_value_are_read() -> None:
    sql = """SELECT 1 FROM item i WHERE i.i_current_price >
             (SELECT AVG(ss.ss_sales_price) FROM store_sales ss JOIN customer c ON ss.ss_customer_sk = c.c_customer_sk)"""
    assert joins(sql) == {"customer.c_customer_sk = store_sales.ss_customer_sk"}
    assert outcome(sql) == "diverged"


# --------------------------------------------------------------------------
# Which outcome wins
# --------------------------------------------------------------------------


def test_diverged_wins_over_not_checked_when_a_foreign_join_was_read_with_confidence() -> None:
    sql = """SELECT 1 FROM catalog_sales cs JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk
             WHERE cs.cs_item_sk IN (SELECT i_item_sk FROM item)"""
    result = compare(extract_joins(sql, SCHEMA), (CS_BILL,))
    assert result.outcome == "diverged"
    assert [item.reason for item in result.unchecked] == ["subquery_predicate"]
    assert any(finding.startswith("not checked (subquery_predicate)") for finding in result.findings)


def test_not_checked_wins_over_incomplete_and_over_conforms() -> None:
    right = "SELECT 1 FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
    hidden = " WHERE cs.cs_item_sk IN (SELECT i_item_sk FROM item)"
    assert outcome(right, CS_BILL) == "conforms"
    assert outcome(right + hidden, CS_BILL) == "not_checked"
    assert outcome(right, CS_BILL, CS_ITEM) == "incomplete"
    assert outcome(right + hidden, CS_BILL, CS_ITEM) == "not_checked"


def test_diverged_wins_over_incomplete_and_every_finding_is_still_listed() -> None:
    sql = "SELECT 1 FROM catalog_sales cs JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk"
    result = compare(extract_joins(sql, SCHEMA), (CS_BILL, CS_ITEM))
    assert result.outcome == "diverged" and set(result.missing) == {CS_BILL, CS_ITEM}
    assert len(result.findings) == 3


def test_half_a_key_beside_something_unread_is_not_known_to_be_wrong() -> None:
    """The other half may be inside the part that could not be read."""
    sql = """SELECT 1 FROM store_returns sr JOIN store_sales ss
             ON sr.sr_item_sk = ss.ss_item_sk AND (sr.sr_ticket_number = ss.ss_ticket_number OR sr.sr_ticket_number IS NULL)"""
    result = compare(extract_joins(sql, SCHEMA), (SR_SS,))
    assert result.outcome == "not_checked" and result.partial == (SR_SS,)
