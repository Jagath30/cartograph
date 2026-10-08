"""The snapshot store, against a scratch database (FR-04, DR-10, DD-17).

The claim that matters: a graph rebuilt from stored rows is the graph built
from the warehouse, element for element. It is checked on a snapshot
written by hand, which holds one of everything awkward, and on the live
warehouse, where the numbers are measured here and not quoted.
"""

import os
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from app.core.graph_builder import build_graph, column_nodes, foreign_key_edges, table_nodes
from app.core.search_text import key_columns, search_texts
from app.core.snapshot import Column, ForeignKey, Preference, PrimaryKey, SchemaSnapshot, Table
from app.shell.schema_ingestor import SchemaIngestor
from app.shell.snapshot_store import NoCurrentSnapshot, SnapshotStore, snapshot_hash

OVERLAY = Path(__file__).resolve().parents[1] / "overlays" / "tpcds.yaml"

# One of everything awkward: a table comment, a column comment, a nullable
# and a not-null column, a two-column primary key, a two-column overlay key
# with a note, a catalog key with a name, and text that needs quoting.
SMALL = SchemaSnapshot(
    tables=(
        Table("sale", "every sale", "sale", "sale. Table sale."),
        Table("return", None, "return", "return. Table return."),
        Table("shop", None, "shop", "shop. Table shop."),
    ),
    columns=(
        Column("sale", "item", "bigint", False, None, "sale — item", "sale — item. Integer column in table sale."),
        Column("sale", "ticket", "bigint", False, None, "sale — ticket", "sale — ticket. Integer column in table sale."),
        Column("sale", "shop_key", "bigint", True, None, "sale — shop key", "sale — shop key. Integer column in table sale."),
        Column("sale", "price", "numeric(7,2)", True, "what it's sold for", "what it's sold for", "what it's sold for. Numeric column in table sale."),
        Column("return", "item", "bigint", True, None, "return — item", "return — item. Integer column in table return."),
        Column("return", "ticket", "bigint", True, None, "return — ticket", "return — ticket. Integer column in table return."),
        Column("return", "amount", "numeric(7,2)", True, None, "return — amount", "return — amount. Numeric column in table return."),
        Column("shop", "key", "bigint", False, None, "shop — key", "shop — key. Integer column in table shop."),
        Column("shop", "region", "character varying(20)", True, None, "shop — region", "shop — region. Text column in table shop."),
    ),
    primary_keys=(PrimaryKey("sale", ("ticket", "item")), PrimaryKey("shop", ("key",))),
    foreign_keys=(
        ForeignKey("sale", ("shop_key",), "shop", ("key",), "catalog", "sale_shop_fk"),
        ForeignKey("return", ("item", "ticket"), "sale", ("item", "ticket"), "overlay", None, "measured: every return matches one sale"),
    ),
)  # fmt: skip


@pytest.fixture
def store(scratch_database) -> SnapshotStore:
    return SnapshotStore(scratch_database)


def _count(url: str, table: str) -> int:
    with psycopg.connect(url) as connection:
        return connection.execute(f"select count(*) from {table}").fetchone()[0]


def assert_same_graph(rebuilt, original) -> None:
    """Element for element: every node with every attribute, every edge
    with every attribute, and the graph's own attributes."""
    assert dict(rebuilt.nodes(data=True)) == dict(original.nodes(data=True))
    assert list(rebuilt.nodes) == list(original.nodes)
    assert sorted(rebuilt.edges(data=True), key=lambda e: e[:2]) == sorted(original.edges(data=True), key=lambda e: e[:2])
    assert rebuilt.graph == original.graph


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_what_is_read_back_is_what_was_saved(store) -> None:
    saved = store.save(SMALL, "test")
    stored, loaded = store.load_current()

    assert loaded == SMALL
    assert (stored.id, stored.hash, stored.source) == (saved.id, snapshot_hash(SMALL), "test")
    assert_same_graph(build_graph(loaded), build_graph(SMALL))


def test_a_two_column_key_is_two_rows_and_comes_back_as_one_key_in_order(store, scratch_database) -> None:
    store.save(SMALL, "test")
    assert _count(scratch_database, "schema_edges") == 3

    _, loaded = store.load_current()
    composite = loaded.foreign_keys[1]
    assert (composite.from_columns, composite.to_columns) == (("item", "ticket"), ("item", "ticket"))
    assert composite.note == "measured: every return matches one sale"
    assert composite.source == "overlay" and composite.name is None
    # The primary key keeps the order it was declared in, not column order.
    assert loaded.primary_keys[0].columns == ("ticket", "item")

    graph = build_graph(loaded)
    numbers = {data["foreign_key"] for _, _, data in foreign_key_edges(graph)}
    assert numbers == {0, 1}


def test_preferences_are_not_stored_and_are_handed_in_when_loading(store) -> None:
    preference = Preference(("sale", "shop"), (("sale.shop_key", "shop.key"),), "because")
    with_preference = replace(SMALL, preferences=(preference,))

    saved = store.save(with_preference, "test")
    assert saved.hash == snapshot_hash(SMALL)

    assert store.load_current()[1].preferences == ()
    assert store.load_current(preferences=(preference,))[1] == with_preference


# --------------------------------------------------------------------------
# Added, never replaced (DD-17)
# --------------------------------------------------------------------------


def test_saving_the_same_snapshot_twice_stores_it_once(store, scratch_database) -> None:
    first = store.save(SMALL, "test")
    second = store.save(SMALL, "test")

    assert (first.created, second.created) == (True, False)
    assert first.id == second.id
    assert _count(scratch_database, "schema_snapshots") == 1
    assert _count(scratch_database, "schema_elements") == len(SMALL.tables) + len(SMALL.columns)


def test_a_changed_schema_is_added_and_becomes_current_and_the_old_one_stays(store, scratch_database) -> None:
    old = store.save(SMALL, "test")
    changed = replace(SMALL, columns=SMALL.columns + (Column("shop", "city", "text", True, None, "shop — city", "shop — city."),))
    new = store.save(changed, "test")

    assert new.id != old.id and new.hash != old.hash
    assert _count(scratch_database, "schema_snapshots") == 2
    assert store.current().id == new.id
    assert store.load_current()[1] == changed

    # Going back to the old schema makes the old rows current again; nothing
    # is written a second time.
    again = store.save(SMALL, "test")
    assert (again.id, again.created) == (old.id, False)
    assert store.load_current()[1] == SMALL
    assert _count(scratch_database, "schema_snapshots") == 2


def test_the_hash_changes_with_a_name_a_note_or_a_key_and_not_with_a_preference() -> None:
    base = snapshot_hash(SMALL)
    renamed = replace(SMALL, tables=(replace(SMALL.tables[0], readable="sales"), *SMALL.tables[1:]))
    renoted = replace(SMALL, foreign_keys=(SMALL.foreign_keys[0], replace(SMALL.foreign_keys[1], note="other")))
    fewer = replace(SMALL, foreign_keys=SMALL.foreign_keys[:1])
    preferred = replace(SMALL, preferences=(Preference(("sale", "shop"), (("sale.shop_key", "shop.key"),), "x"),))

    assert len({base, snapshot_hash(renamed), snapshot_hash(renoted), snapshot_hash(fewer)}) == 4
    assert snapshot_hash(preferred) == base


def test_the_hash_changes_when_the_search_text_would_be_derived_differently(monkeypatch) -> None:
    """The search text is derived from the snapshot, so for one version of
    the code it adds nothing to the hash. It is hashed for the day the
    derivation changes: the same warehouse would then be stored again, and
    embedded again, and not left searching by vectors of text it no longer
    holds. (With the search text left out of the hash, every other test
    here passed.)"""
    import app.shell.snapshot_store as module

    base = snapshot_hash(SMALL)
    derived = search_texts(SMALL)
    # A table's text alone, then a column's text alone.
    for changed in (("shop", None), ("shop", "region")):
        monkeypatch.setattr(module, "search_texts", lambda snapshot, changed=changed: derived | {changed: "other"})
        assert snapshot_hash(SMALL) != base


def test_an_empty_store_says_so(store) -> None:
    with pytest.raises(NoCurrentSnapshot, match="nothing has been ingested"):
        store.load_current()


# --------------------------------------------------------------------------
# The text each element is searched by (ruling f)
# --------------------------------------------------------------------------


def test_a_tables_text_lists_what_it_holds_and_leaves_its_keys_out(store, scratch_database) -> None:
    assert key_columns(SMALL) == {
        ("sale", "item"), ("sale", "ticket"), ("sale", "shop_key"),
        ("return", "item"), ("return", "ticket"), ("shop", "key"),
    }  # fmt: skip
    texts = search_texts(SMALL)
    assert texts["sale", None] == "sale. Table sale. Holds: what it's sold for."
    assert texts["shop", None] == "shop. Table shop. Holds: region."
    assert texts["shop", "region"] == "shop — region. Text column in table shop."

    store.save(SMALL, "test")
    with psycopg.connect(scratch_database) as connection:
        stored = connection.execute(
            "select search_text from schema_elements where table_name = 'return' and column_name is null"
        ).fetchone()[0]
    assert stored == "return. Table return. Holds: amount."


# --------------------------------------------------------------------------
# The live warehouse
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_snapshot() -> SchemaSnapshot:
    dsn = os.environ.get("WAREHOUSE_DATABASE_URL")
    if not dsn:
        pytest.skip("WAREHOUSE_DATABASE_URL is not set")
    try:
        with psycopg.connect(dsn, connect_timeout=3) as connection:
            loaded = connection.execute("select count(*) from pg_tables where schemaname = 'public'").fetchone()[0]
    except psycopg.OperationalError:
        pytest.skip("the warehouse is not reachable")
    if loaded == 0:
        pytest.skip("the warehouse is empty -- run ./scripts/warehouse.sh")
    return SchemaIngestor(dsn, OVERLAY).ingest()


def test_the_graph_rebuilt_from_rows_is_the_graph_live_ingestion_builds(store, scratch_database, live_snapshot) -> None:
    store.save(live_snapshot, "warehouse")
    _, loaded = store.load_current(preferences=live_snapshot.preferences)

    assert loaded == live_snapshot
    rebuilt, original = build_graph(loaded), build_graph(live_snapshot)
    assert_same_graph(rebuilt, original)

    # Measured on the rebuilt graph and on the rows, then compared with
    # each other -- and with the figures recorded in CHECKPOINTS.md, so a
    # change to the warehouse shows up here as a number.
    tables, columns = len(table_nodes(rebuilt)), len(column_nodes(rebuilt))
    pairs = len(foreign_key_edges(rebuilt))
    keys = len({data["foreign_key"] for _, _, data in foreign_key_edges(rebuilt)})

    assert _count(scratch_database, "schema_elements") == tables + columns == rebuilt.number_of_nodes()
    assert _count(scratch_database, "schema_edges") == pairs
    assert rebuilt.number_of_edges() == columns + pairs
    assert keys == len(live_snapshot.foreign_keys)
    assert (tables, columns, keys, pairs, rebuilt.number_of_nodes(), rebuilt.number_of_edges()) == (
        24, 425, 107, 110, 449, 535,
    )  # fmt: skip
