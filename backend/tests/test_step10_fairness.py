"""The owner's ruling 11 on d4, as retrieval really selects it: the two
modes' prompts differ only in what Cartograph contributes.

Needs both databases: the live schema, the stored snapshot, and d4's
vectors from the cache. The embedding provider is replaced by one that
fails, so a vector not in the cache fails this test and is never paid
for. No model is called.
"""

import os

import pytest

from app.core.full_schema import KEYS_RULES, all_tables, build_full_prompt, graph_keys, render_keys
from app.core.graph_builder import build_graph
from app.core.locate import locate
from app.core.prompt_builder import GENERAL_RULES, build_prompt, render_joins, render_tables

D4 = "How do purchases on the web split by the buyer's level of education?"


class _NeverCalled:
    model = "text-embedding-3-small"

    def embed(self, texts):
        raise AssertionError(f"d4 was not in the vector cache: {texts}")


@pytest.fixture(scope="module")
def live():
    if not (os.environ.get("APP_DATABASE_URL") and os.environ.get("WAREHOUSE_DATABASE_URL")):
        pytest.skip("needs the application database and the warehouse")
    from app.config import get_settings
    from app.shell import retrieval_session
    from app.shell.schema_ingestor import SchemaIngestor

    patch = pytest.MonkeyPatch()
    patch.setattr(retrieval_session, "make_embedder", lambda settings: _NeverCalled())
    try:
        settings = get_settings()
        snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
        graph = build_graph(snapshot)
        opened = retrieval_session.open_retrieval(snapshot, 0.5, 0.5, 5, "test")
        scored = opened.index.score_question(D4)
        located = locate(D4, scored.scores, scored.terms, graph, opened.settings)
    finally:
        patch.undo()
    retrieval = build_prompt(D4, snapshot, located.tree.subgraph, located.tree.joins)
    full = build_full_prompt(D4, snapshot, graph)
    return snapshot, graph, located, retrieval, full


def test_d4_is_answered_from_a_tree_with_joins(live) -> None:
    _, _, located, retrieval, _ = live
    assert not located.declined and located.tree.joins and retrieval.has_join_section


def test_the_two_system_messages_are_byte_identical(live) -> None:
    _, _, _, retrieval, full = live
    assert retrieval.system.encode() == full.system.encode() == GENERAL_RULES.encode()


def test_with_the_tables_and_the_join_or_keys_section_cut_out_the_user_messages_are_byte_identical(live) -> None:
    snapshot, graph, located, retrieval, full = live
    cut_retrieval = retrieval.user.replace(render_tables(snapshot, located.tree.subgraph), "", 1).replace(
        render_joins(located.tree.joins), "", 1
    )
    cut_full = full.user.replace(render_tables(snapshot, all_tables(snapshot)), "", 1).replace(
        render_keys(graph_keys(graph)), "", 1
    )
    assert cut_full.encode() == cut_retrieval.encode() == f"Tables:\n\n\n\n\n\nQuestion: {D4}".encode()


def test_the_full_schema_tables_part_is_render_tables_for_all_24(live) -> None:
    snapshot, _, _, _, full = live
    assert full.tables == tuple(sorted(t.name for t in snapshot.tables)) and len(full.tables) == 24
    assert full.user.startswith("Tables:\n\n" + render_tables(snapshot, full.tables) + "\n\n" + KEYS_RULES + "\n")


def test_the_keys_section_holds_every_edge_of_the_live_graph_once_catalog_and_overlay_alike(live) -> None:
    snapshot, graph, _, _, full = live
    keys = graph_keys(graph)
    assert len(keys) == 107 and {k.source for k in keys} == {"catalog", "overlay"}
    expected = sorted(
        "  " + " AND ".join(f"{k.from_table}.{a} = {k.to_table}.{b}" for a, b in zip(k.from_columns, k.to_columns))
        for k in snapshot.foreign_keys
    )
    section = full.user.split(KEYS_RULES + "\n", 1)[1].split("\n\nQuestion: ", 1)[0].split("\n")
    assert section == expected and len(set(section)) == 107


def test_each_of_d4s_tree_edges_is_the_same_line_in_both_prompts(live) -> None:
    _, _, located, retrieval, full = live
    keys_lines = set(full.user.split(KEYS_RULES + "\n", 1)[1].split("\n\nQuestion: ", 1)[0].split("\n"))
    for join in located.tree.joins:
        line = render_joins((join,)).split("\n")[-1]
        assert line in retrieval.user.split("\n")
        assert line in keys_lines
