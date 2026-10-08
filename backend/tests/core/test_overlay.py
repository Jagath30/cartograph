"""The overlay, parsed and applied (FR-43, DD-08, DD-16).

No database: overlay text written in the test, applied to the hand-written
snapshot in conftest.py. Three parts -- reading the file's text, merging
its relationships catalog-first, and applying its naming section.
"""

from dataclasses import replace

import pytest

from app.core.graph_builder import build_graph, foreign_key_edges
from app.core.naming import Naming, type_family
from app.core.overlay import Overlay, Relationship, apply_overlay, parse_overlay
from app.core.snapshot import Column, Preference, Table

NAMING = Naming(
    prefixes={"ss_": "store sales", "ca_": "customer address", "s_": "store", "d_": "date"},
    words={"ext": "extended", "sk": "key", "addr": "address", "moy": "month of year", "dim": "dimension"},
)

TEXT = """
relationships:
  - from: store_sales.ss_store_sk
    to:   store.s_store_sk
naming:
  prefixes: { ss_: store sales, ca_: customer address }
  words:    { ext: extended, sk: key }
"""


def catalog_only(snapshot):
    return replace(snapshot, foreign_keys=tuple(k for k in snapshot.foreign_keys if k.source == "catalog"))


# --------------------------------------------------------------------------
# 1. Reading the text
# --------------------------------------------------------------------------


def test_the_two_sections_are_read() -> None:
    overlay = parse_overlay(TEXT)

    assert overlay.relationships == (Relationship("store_sales", "ss_store_sk", "store", "s_store_sk"),)
    assert overlay.naming.prefixes == {"ss_": "store sales", "ca_": "customer address"}
    assert overlay.naming.words == {"ext": "extended", "sk": "key"}


def test_an_empty_file_and_a_missing_section_are_an_empty_overlay() -> None:
    assert parse_overlay("") == Overlay()
    assert parse_overlay("# nothing but a comment\n") == Overlay()
    assert parse_overlay("relationships: []\n").naming == Naming()


@pytest.mark.parametrize(
    "text",
    [
        "namng:\n  words: { sk: key }\n",  # a misspelt section must not be skipped
        "preferences:\n  - between: [a, b]\n",  # a preference with no route and no reason
        "naming:\n  abbreviations: { sk: key }\n",
        "- just\n- a list\n",
    ],
)
def test_a_section_that_is_not_understood_is_refused(text) -> None:
    with pytest.raises(ValueError):
        parse_overlay(text)


@pytest.mark.parametrize(
    "entry",
    [
        "{ from: store_sales.ss_store_sk }",  # no `to`
        "{ from: store_sales, to: store.s_store_sk }",  # a table, not a column
        "{ from: public.store_sales.ss_store_sk, to: store.s_store_sk }",
        "{ from: store_sales.ss_store_sk, to: store.s_store_sk, via: x }",
        "{ from: store_sales., to: store.s_store_sk }",
    ],
)
def test_a_malformed_relationship_is_refused(entry) -> None:
    with pytest.raises(ValueError):
        parse_overlay(f"relationships:\n  - {entry}\n")


def test_a_relationship_listed_twice_is_refused() -> None:
    twice = "relationships:\n" + "  - { from: store_sales.ss_store_sk, to: store.s_store_sk }\n" * 2

    with pytest.raises(ValueError, match="more than once"):
        parse_overlay(twice)


def test_a_word_that_yaml_reads_as_a_boolean_is_refused_and_not_dropped() -> None:
    """`on` is a real fragment of inv_quantity_on_hand, and YAML turns a
    bare `on` into True. Unchecked, that entry would never match anything."""
    with pytest.raises(ValueError, match="text to text"):
        parse_overlay("naming:\n  words: { on: on }\n")

    assert parse_overlay('naming:\n  words: { "on": "on" }\n').naming.words == {"on": "on"}


# --------------------------------------------------------------------------
# 2. Relationships: catalog first, overlay filling the gaps
# --------------------------------------------------------------------------


def test_the_overlay_fills_what_the_catalog_does_not_declare(small_snapshot) -> None:
    before = catalog_only(small_snapshot)
    overlay = Overlay(
        relationships=(Relationship("catalog_sales", "cs_ship_addr_sk", "customer_address", "ca_address_sk"),)
    )

    after = apply_overlay(before, overlay)

    assert len(before.foreign_keys) == 5
    assert len(after.foreign_keys) == 6
    added = after.foreign_keys[-1]
    assert (added.from_table, added.from_columns, added.to_table, added.to_columns) == (
        "catalog_sales", ("cs_ship_addr_sk",), "customer_address", ("ca_address_sk",),
    )  # fmt: skip
    assert added.source == "overlay"
    assert added.name is None


def test_the_catalog_wins_where_both_declare_the_same_edge(small_snapshot) -> None:
    """The situation on the real warehouse, where every constraint applied:
    the overlay restates what the catalog already says, and adds nothing."""
    before = catalog_only(small_snapshot)
    restated = Overlay(relationships=(Relationship("store_sales", "ss_store_sk", "store", "s_store_sk"),))

    after = apply_overlay(before, restated)

    assert after.foreign_keys == before.foreign_keys
    assert {key.source for key in after.foreign_keys} == {"catalog"}


def test_with_no_catalog_keys_at_all_every_edge_comes_from_the_overlay(small_snapshot) -> None:
    """The situation DuckDB's output is in before any constraint is applied
    (R-09): the catalog declares nothing, and the graph still has edges."""
    bare = replace(small_snapshot, foreign_keys=())
    overlay = Overlay(
        relationships=(
            Relationship("store_sales", "ss_store_sk", "store", "s_store_sk"),
            Relationship("store_sales", "ss_addr_sk", "customer_address", "ca_address_sk"),
        )
    )

    edges = foreign_key_edges(build_graph(apply_overlay(bare, overlay)))

    assert len(edges) == 2
    assert [data["source"] for _, _, data in edges] == ["overlay", "overlay"]


@pytest.mark.parametrize(
    "relationship",
    [
        Relationship("store_sales", "ss_shop_sk", "store", "s_store_sk"),
        Relationship("store_sales", "ss_store_sk", "store", "s_shop_sk"),
        Relationship("store_sales", "ss_store_sk", "shop", "s_store_sk"),
    ],
)
def test_an_overlay_edge_to_something_the_warehouse_does_not_have_is_refused(small_snapshot, relationship) -> None:
    with pytest.raises(ValueError, match="is not a column of this warehouse"):
        apply_overlay(small_snapshot, Overlay(relationships=(relationship,)))


def test_applying_leaves_the_original_snapshot_as_it_was(small_snapshot) -> None:
    before = catalog_only(small_snapshot)
    overlay = Overlay(
        relationships=(Relationship("catalog_sales", "cs_ship_addr_sk", "customer_address", "ca_address_sk"),),
        naming=NAMING,
    )

    apply_overlay(before, overlay)

    assert len(before.foreign_keys) == 5
    assert all(column.readable == "" for column in before.columns)


# --------------------------------------------------------------------------
# 2b. Preferences: read strictly, and checked against the warehouse's keys
# --------------------------------------------------------------------------

PREFERENCE = """
preferences:
  - between: [customer_address, catalog_sales]
    prefer:
      - from: catalog_sales.cs_bill_addr_sk
        to:   customer_address.ca_address_sk
    because: a catalog sale's region is where it was billed
"""


def test_a_preference_is_read_with_its_tables_in_alphabetical_order() -> None:
    [preference] = parse_overlay(PREFERENCE).preferences

    assert preference == Preference(
        between=("catalog_sales", "customer_address"),
        prefer=(("catalog_sales.cs_bill_addr_sk", "customer_address.ca_address_sk"),),
        because="a catalog sale's region is where it was billed",
    )


@pytest.mark.parametrize(
    "entry",
    [
        "{ between: [a, b], prefer: [{ from: a.x, to: b.y }] }",  # no reason given
        "{ between: [a, b], prefer: [{ from: a.x, to: b.y }], because: '  ' }",
        "{ between: [a, b], prefer: via_store, because: r }",  # DD-16's own example: a label, not a route
        "{ between: [a, b], prefer: [], because: r }",
        "{ between: [a], prefer: [{ from: a.x, to: b.y }], because: r }",
        "{ between: [a, a], prefer: [{ from: a.x, to: b.y }], because: r }",
        "{ between: a, prefer: [{ from: a.x, to: b.y }], because: r }",
        "{ between: [a, b], prefer: [{ from: a, to: b.y }], because: r }",
        "{ between: [a, b], prefer: [{ from: a.x, to: b.y }], because: r, weight: 2 }",
    ],
)
def test_a_malformed_preference_is_refused(entry) -> None:
    with pytest.raises(ValueError):
        parse_overlay(f"preferences:\n  - {entry}\n")


def test_two_preferences_for_the_same_pair_are_refused() -> None:
    one = "  - { between: [a, b], prefer: [{ from: a.x, to: b.y }], because: r }\n"
    other = "  - { between: [b, a], prefer: [{ from: a.z, to: b.y }], because: r }\n"

    with pytest.raises(ValueError, match="more than one preference"):
        parse_overlay("preferences:\n" + one + other)


def test_a_valid_preference_travels_with_the_snapshot_and_into_the_graph(small_snapshot) -> None:
    after = apply_overlay(small_snapshot, parse_overlay(PREFERENCE))

    assert len(after.preferences) == 1
    assert build_graph(after).graph["preferences"] == after.preferences
    assert build_graph(small_snapshot).graph["preferences"] == ()


def test_a_preference_may_name_an_edge_the_overlay_itself_supplies(small_snapshot) -> None:
    """Checked against the merged keys, not only the catalog's."""
    bare = replace(small_snapshot, foreign_keys=())
    text = "relationships:\n  - { from: catalog_sales.cs_bill_addr_sk, to: customer_address.ca_address_sk }\n" + PREFERENCE

    assert len(apply_overlay(bare, parse_overlay(text)).preferences) == 1


def preference_text(between: str, *edges: str) -> str:
    prefer = ", ".join("{ from: %s, to: %s }" % tuple(edge.split(" -> ")) for edge in edges)
    return f"preferences:\n  - {{ between: {between}, prefer: [{prefer}], because: r }}\n"


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        # not a foreign key at all
        (
            preference_text("[catalog_sales, customer_address]", "catalog_sales.cs_bill_addr_sk -> customer_address.ca_state"),
            "not a foreign key",
        ),
        # a real key written backwards
        (
            preference_text("[catalog_sales, customer_address]", "customer_address.ca_address_sk -> catalog_sales.cs_bill_addr_sk"),
            "not a foreign key",
        ),
        # a real key that does not connect the two tables named
        (
            preference_text("[catalog_sales, store]", "catalog_sales.cs_bill_addr_sk -> customer_address.ca_address_sk"),
            "does not describe one route",
        ),
        # two real keys that are two routes, not one
        (
            preference_text(
                "[catalog_sales, customer_address]",
                "catalog_sales.cs_bill_addr_sk -> customer_address.ca_address_sk",
                "catalog_sales.cs_ship_addr_sk -> customer_address.ca_address_sk",
            ),
            "does not describe one route",
        ),
        # a route with a stray extra edge
        (
            preference_text(
                "[store_sales, customer_address]",
                "store_sales.ss_addr_sk -> customer_address.ca_address_sk",
                "store_sales.ss_store_sk -> store.s_store_sk",
            ),
            "does not describe one route",
        ),
    ],
)
def test_a_preference_the_warehouse_cannot_honour_is_refused_at_ingestion(small_snapshot, text, complaint) -> None:
    with pytest.raises(ValueError, match=complaint):
        apply_overlay(small_snapshot, parse_overlay(text))


def test_a_two_join_preference_is_accepted_in_either_order_of_its_edges(small_snapshot) -> None:
    edges = ["store_sales.ss_customer_sk -> customer.c_customer_sk", "customer.c_current_addr_sk -> customer_address.ca_address_sk"]

    for ordering in (edges, edges[::-1]):
        text = preference_text("[customer_address, store_sales]", *ordering)
        assert len(apply_overlay(small_snapshot, parse_overlay(text)).preferences) == 1


# --------------------------------------------------------------------------
# 3. Naming: the overlay's words actually reach the snapshot and the graph
# --------------------------------------------------------------------------


def described(snapshot, naming=NAMING) -> dict[str, Column]:
    after = apply_overlay(snapshot, Overlay(naming=naming))
    return {f"{column.table}.{column.name}": column for column in after.columns}


def test_a_column_gets_the_reading_the_design_promises(small_snapshot) -> None:
    """DD-08's own worked example, word for word."""
    column = described(small_snapshot)["store_sales.ss_ext_sales_price"]

    assert column.readable == "store sales — extended sales price"
    assert column.description == "store sales — extended sales price. Numeric column in table store_sales."


def test_the_naming_section_is_what_changes_the_result(small_snapshot) -> None:
    """The same snapshot under no naming, then under one. If both gave the
    same text, the overlay would be loaded and then ignored."""
    without = described(small_snapshot, Naming())["store_sales.ss_ext_sales_price"]
    with_naming = described(small_snapshot)["store_sales.ss_ext_sales_price"]

    assert without.readable == "ss ext sales price"
    assert without.description == "ss ext sales price. Numeric column in table store_sales."
    assert with_naming.readable != without.readable


def test_prefixes_and_words_are_both_applied(small_snapshot) -> None:
    columns = described(small_snapshot)

    assert columns["customer_address.ca_address_sk"].readable == "customer address — address key"
    assert columns["customer_address.ca_state"].description == (
        "customer address — state. Text column in table customer_address."
    )
    assert columns["store.s_store_sk"].readable == "store — store key"
    assert columns["store_sales.ss_addr_sk"].readable == "store sales — address key"


def test_a_column_with_no_mapping_keeps_its_own_words(small_snapshot) -> None:
    """Graceful degradation: `c_` and `cs_` are not in this naming, so those
    columns fall back to their fragments -- known words still expanded."""
    columns = described(small_snapshot)

    assert columns["customer.c_current_addr_sk"].readable == "c current address key"
    assert columns["catalog_sales.cs_bill_addr_sk"].readable == "cs bill address key"


def test_the_longest_prefix_wins(small_snapshot) -> None:
    naming = Naming(prefixes={"s_": "store", "ss_": "store sales", "ss_ext_": "WRONG IF SHORTER WINS"})
    column = described(small_snapshot, naming)["store_sales.ss_ext_sales_price"]

    assert column.readable == "WRONG IF SHORTER WINS — sales price"


def test_a_database_comment_beats_the_derived_name(small_snapshot) -> None:
    commented = replace(
        small_snapshot,
        tables=tuple(
            replace(t, comment="Every sale made in a physical store.") if t.name == "store_sales" else t
            for t in small_snapshot.tables
        ),
        columns=tuple(
            replace(c, comment="Revenue for the line, before tax") if c.name == "ss_ext_sales_price" else c
            for c in small_snapshot.columns
        ),
    )
    after = apply_overlay(commented, Overlay(naming=NAMING))
    column = next(c for c in after.columns if c.name == "ss_ext_sales_price")
    table = next(t for t in after.tables if t.name == "store_sales")

    assert column.readable == "Revenue for the line, before tax"
    assert column.description == "Revenue for the line, before tax. Numeric column in table store_sales."
    assert table.readable == "Every sale made in a physical store"
    assert table.description == "Every sale made in a physical store. Table store_sales."


def test_tables_are_named_from_their_words(small_snapshot) -> None:
    with_dim = replace(small_snapshot, tables=small_snapshot.tables + (Table("date_dim"),))
    tables = {t.name: t for t in apply_overlay(with_dim, Overlay(naming=NAMING)).tables}

    assert tables["store_sales"].readable == "store sales"
    assert tables["date_dim"].readable == "date dimension"
    assert tables["date_dim"].description == "date dimension. Table date_dim."


@pytest.mark.parametrize(
    ("data_type", "family"),
    [
        ("bigint", "Integer"),
        ("integer", "Integer"),
        ("numeric(7,2)", "Numeric"),
        ("decimal(5,2)", "Numeric"),
        ("character varying", "Text"),
        ("character varying(10)", "Text"),
        ("varchar", "Text"),
        ("date", "Date"),
        ("timestamp without time zone", "Timestamp"),
        ("time without time zone", "Time"),
        ("jsonb", None),
    ],
)
def test_data_types_are_named_in_one_plain_word(data_type, family) -> None:
    assert type_family(data_type) == family


def test_an_unlisted_type_is_reported_as_it_is(small_snapshot) -> None:
    odd = replace(small_snapshot, columns=small_snapshot.columns + (Column("store", "s_hours", "jsonb"),))

    assert described(odd)["store.s_hours"].description == "store — hours. Column of type jsonb in table store."


def test_the_readable_names_reach_the_graph(small_snapshot) -> None:
    graph = build_graph(apply_overlay(small_snapshot, Overlay(naming=NAMING)))

    assert graph.nodes["store_sales.ss_ext_sales_price"]["readable"] == "store sales — extended sales price"
    assert graph.nodes["store_sales"]["readable"] == "store sales"
