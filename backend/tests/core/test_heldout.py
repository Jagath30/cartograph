"""The held-out evaluation set, guarded (DR-16, SRS threat T-04).

Reads only committed files -- the set, the DDL and the generated overlay --
so every test here runs in CI, which has no warehouse. The generated
warehouse declares no foreign key of its own (FR-43): every edge of the
graph comes from the overlay, so the overlay is all that the joins need
checking against.

THE RULE. backend/eval/heldout.yaml is never run, embedded, sent to a model
or used to choose any setting before step 10. Nothing here does any of
those: the set is parsed and its names are looked up, and that is all.

  the hash    The set's sha256 is pinned below, in this file. Changing one
              byte of the set fails the suite until the value here is
              changed too, so an edited expectation and the edit to this
              test always sit in the same diff, in front of whoever reviews
              it. It cannot stop an edit. It stops a quiet one.
  the names   Every table, column and join the set names exists in the DDL
              and the overlay. An expectation with a typo in it can never
              match, and would read as the system's failure, not the set's.
"""

import hashlib
from pathlib import Path

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.heldout import parse_heldout
from app.core.overlay import apply_overlay, parse_overlay

HELDOUT = Path(__file__).resolve().parents[2] / "eval" / "heldout.yaml"

# THE FREEZE. If this assertion fails, backend/eval/heldout.yaml changed.
# It is frozen from its first commit: an expectation is never edited to
# match what the system does, or to make a check pass. Do not update this
# value to make the test pass. A genuinely wrong expectation is the owner's
# to correct, with the reason in CHECKPOINTS.md -- and then this value
# changes in the same commit, with that reason.
PINNED_SHA256 = "14e1441e5c9717c519c00410ad27c1bf9b78caf07184141064dffc2377f806fe"


@pytest.fixture(scope="module")
def heldout():
    return parse_heldout(HELDOUT.read_text())


@pytest.fixture(scope="module")
def snapshot():
    return apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))


def test_the_set_is_byte_for_byte_the_one_that_was_frozen() -> None:
    assert hashlib.sha256(HELDOUT.read_bytes()).hexdigest() == PINNED_SHA256, (
        f"{HELDOUT.name} is not the file that was frozen. Read the comment above PINNED_SHA256 before doing anything."
    )


def test_the_set_holds_twelve_questions_and_says_where_they_came_from(heldout) -> None:
    assert [question.id for question in heldout.questions] == [f"H{number}" for number in range(1, 13)]
    assert "from the schema alone with no retrieval output consulted" in heldout.provenance
    assert "Approved by Jagath Manjunath" in heldout.provenance
    for question in heldout.questions:
        assert question.question.endswith("?")


def test_one_question_is_declined_one_needs_no_join_and_one_joins_on_two_columns(heldout) -> None:
    by_id = {question.id: question for question in heldout.questions}
    assert [question.id for question in heldout.questions if question.decline] == ["H9"]
    assert [question.id for question in heldout.questions if not question.joins and not question.decline] == ["H5"]
    assert by_id["H5"].tables == ("promotion",)

    composite = [(question.id, join) for question in heldout.questions for join in question.joins if len(join.edges) > 1]
    assert [(number, join.edges) for number, join in composite] == [
        (
            "H4",
            (
                ("web_returns.wr_item_sk", "web_sales.ws_item_sk"),
                ("web_returns.wr_order_number", "web_sales.ws_order_number"),
            ),
        )
    ]


def test_every_table_column_and_join_the_set_names_exists(heldout, snapshot) -> None:
    tables = {table.name for table in snapshot.tables}
    columns = {(column.table, column.name) for column in snapshot.columns}
    # Two real columns paired wrongly are still a typo. The order of a
    # composite key's columns is not part of what a join means, so compare
    # the pairs -- both of them, for a key of two columns.
    real = {
        frozenset((f"{key.from_table}.{start}", f"{key.to_table}.{end}") for start, end in zip(key.from_columns, key.to_columns))
        for key in snapshot.foreign_keys
    }

    for question in heldout.questions:
        assert sorted(set(question.tables) - tables) == [], question.id
        for join in question.joins:
            named = {(join.from_table, column) for column in join.from_columns}
            named |= {(join.to_table, column) for column in join.to_columns}
            assert sorted(named - columns) == [], question.id
            assert frozenset(join.edges) in real, (question.id, join)


def test_the_joins_of_a_question_connect_all_of_its_tables(heldout) -> None:
    """A table the joins never reach would be an expectation nothing could
    meet: a correct answer cannot use a table it has no way to join."""
    for question in heldout.questions:
        reached = set(question.tables[:1])
        for _ in question.joins:
            for join in question.joins:
                if {join.from_table, join.to_table} & reached:
                    reached |= {join.from_table, join.to_table}
        assert reached == set(question.tables), question.id
        assert len(question.joins) == max(len(question.tables) - 1, 0), question.id


def test_the_parser_refuses_what_it_does_not_understand() -> None:
    def text(body: str) -> str:
        return f"provenance: someone\nquestions:\n  - id: H1\n    question: Why?\n{body}"

    parse_heldout(text("    tables: [a, b]\n    joins:\n      - {from: a.x, to: b.y}\n"))
    for body in (
        # a warning: this set carries none
        "    tables: [a]\n    joins: []\n    warning: none\n",
        # a decline that names a table, and an answered question that names none
        "    tables: [a]\n    joins: []\n    decline: true\n",
        "    tables: []\n    joins: []\n",
        "    tables: [a]\n    joins: []\n    decline: false\n",
        # a join to a table the question does not list
        "    tables: [a]\n    joins:\n      - {from: a.x, to: b.y}\n",
        # a key of two columns against a key of one
        "    tables: [a, b]\n    joins:\n      - {from: [a.x, a.z], to: b.y}\n",
    ):
        with pytest.raises(ValueError, match="held-out set"):
            parse_heldout(text(body))
