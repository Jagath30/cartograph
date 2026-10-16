"""Step 10's full-schema prompt, held to the owner's ruling 11: the two
modes' prompts differ only in what Cartograph contributes.

From committed files alone (the DDL and the generated overlay), so these
run in CI. The same checks on d4 as retrieval really selects it are in
tests/test_step10_fairness.py, which needs the databases.
"""

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.full_schema import (
    KEYS_RULES,
    all_tables,
    build_full_prompt,
    full_schema_located,
    graph_keys,
    render_keys,
)
from app.core.graph_builder import build_graph
from app.core.overlay import apply_overlay, parse_overlay
from app.core.path_finder import MANY_TO_ONE, Join
from app.core.prompt_builder import GENERAL_RULES, build_prompt, render_joins, render_tables

QUESTION = "How do purchases on the web split by the buyer's level of education?"


@pytest.fixture(scope="module")
def snapshot():
    return apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))


@pytest.fixture(scope="module")
def graph(snapshot):
    return build_graph(snapshot)


@pytest.fixture(scope="module")
def full(snapshot, graph):
    return build_full_prompt(QUESTION, snapshot, graph)


def _key(snapshot, from_table, to_table) -> Join:
    """One key of the snapshot, as a tree would hold it."""
    [key] = [k for k in snapshot.foreign_keys if (k.from_table, k.to_table) == (from_table, to_table)]
    return Join(key.from_table, key.from_columns, key.to_table, key.to_columns, key.source, key.name, MANY_TO_ONE)


@pytest.fixture(scope="module")
def retrieval(snapshot):
    """A retrieval-mode prompt of d4's shape: a tree of two tables, one
    join, and a table on a tied alternative in its subgraph."""
    joins = (_key(snapshot, "web_sales", "web_site"),)
    return build_prompt(QUESTION, snapshot, ("web_sales", "web_site", "web_page"), joins), joins


def _key_lines(user: str) -> list[str]:
    section = user.split(KEYS_RULES + "\n", 1)[1]
    return section.split("\n\nQuestion: ", 1)[0].split("\n")


def test_the_keys_section_is_the_owners_wording_word_for_word() -> None:
    assert KEYS_RULES == (
        "Keys between tables. These are the keys by which the tables above can be joined. A line with two "
        "conditions joined by AND is one key and needs both. Two tables may be joined by more than one key. "
        "Use only the tables the question needs: a table listed above need not appear in the query."
    )


def test_the_two_modes_system_messages_are_byte_identical(full, retrieval) -> None:
    prompt, _ = retrieval
    assert full.system.encode() == prompt.system.encode() == GENERAL_RULES.encode()


def test_with_the_tables_and_the_join_or_keys_section_cut_out_the_user_messages_are_byte_identical(
    snapshot, graph, full, retrieval
) -> None:
    prompt, joins = retrieval
    cut_retrieval = prompt.user.replace(render_tables(snapshot, prompt.tables), "", 1).replace(
        render_joins(joins), "", 1
    )
    cut_full = full.user.replace(render_tables(snapshot, all_tables(snapshot)), "", 1).replace(
        render_keys(graph_keys(graph)), "", 1
    )
    assert cut_full.encode() == cut_retrieval.encode()
    assert cut_full == f"Tables:\n\n\n\n\n\nQuestion: {QUESTION}"


def test_the_tables_part_is_exactly_render_tables_for_all_24_in_alphabetical_order(snapshot, full) -> None:
    names = sorted(table.name for table in snapshot.tables)
    assert len(names) == 24 and full.tables == tuple(names)
    assert full.user.startswith("Tables:\n\n" + render_tables(snapshot, tuple(names)) + "\n\n" + KEYS_RULES + "\n")


def test_the_keys_section_holds_every_key_of_the_graph_exactly_once(snapshot, full) -> None:
    """Against the snapshot's own keys, not the module's gathering of them:
    107 lines, none missing, repeated or invented."""
    expected = sorted(
        "  " + " AND ".join(f"{k.from_table}.{a} = {k.to_table}.{b}" for a, b in zip(k.from_columns, k.to_columns))
        for k in snapshot.foreign_keys
    )
    lines = _key_lines(full.user)
    assert len(snapshot.foreign_keys) == 107
    assert len(lines) == 107 and len(set(lines)) == 107
    assert lines == expected


def test_the_keys_carry_no_mark_of_where_they_came_from(snapshot, full) -> None:
    """Here every key comes from the overlay (the DDL declares none); the
    live graph, with both sources, is checked in test_step10_fairness."""
    section = "\n".join(_key_lines(full.user))
    for word in ("catalog", "overlay", "source", "--"):
        assert word not in section.replace("catalog_", "")


def test_a_key_of_two_columns_is_one_line_joined_by_and(full) -> None:
    lines = _key_lines(full.user)
    assert (
        "  store_returns.sr_item_sk = store_sales.ss_item_sk AND "
        "store_returns.sr_ticket_number = store_sales.ss_ticket_number"
    ) in lines


def test_each_key_line_is_the_line_the_frozen_builder_renders_for_that_key(snapshot, graph, full) -> None:
    lines = _key_lines(full.user)
    for key in graph_keys(graph):
        assert render_joins((key,)).split("\n")[-1] in lines
    assert render_joins((_key(snapshot, "web_sales", "web_site"),)).split("\n")[-1] in lines


def test_the_keys_section_stands_where_the_join_section_does_between_the_tables_and_the_question(
    snapshot, full, retrieval
) -> None:
    prompt, joins = retrieval
    tables_end = prompt.user.index(render_tables(snapshot, prompt.tables)) + len(render_tables(snapshot, prompt.tables))
    assert prompt.user[tables_end:].startswith("\n\n" + render_joins(joins) + "\n\nQuestion: ")
    tables_end = full.user.index(KEYS_RULES) - 2
    assert full.user[tables_end:].startswith("\n\n" + KEYS_RULES + "\n")
    assert full.user.endswith(f"\n\nQuestion: {QUESTION}")


def test_nothing_is_selected(full) -> None:
    assert full.has_join_section is False
    assert "selected" not in full.user


def test_the_stand_in_offers_every_table_no_join_and_no_decline(snapshot) -> None:
    located = full_schema_located(snapshot)
    assert located.declined is False and located.decline_reason is None
    assert located.tree.tables == located.tree.subgraph == all_tables(snapshot)
    assert located.tree.joins == () and located.tree.decline_reason is None
