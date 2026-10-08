"""Rule 3 of DD-12 as amended at step 6: what the question's wording says may
break a tie between equally short routes (FR-13, FR-45, DD-21).

From the hand-written fixture, with scores typed here. Nothing below is a
question of the evaluation set, and no score came from an embedding.

What has to be true, and is tested:
  - it decides only by MORE than the margin;
  - when it decides, the choice is not arbitrary and nothing warns, and the
    reason gives the scores and names the route not taken;
  - when it does not, everything is as it was: alphabetical, arbitrary,
    warned -- with the scores shown;
  - a declared preference comes first;
  - it can never promote a longer route;
  - handed no evidence, the PathFinder is exactly the one step 4 built.
"""

from dataclasses import replace

import pytest

from app.core.explainer import explain
from app.core.graph_builder import build_graph
from app.core.overlay import apply_overlay, parse_overlay
from app.core.path_finder import choose, find_paths, score_tied
from app.core.snapshot import Column, ForeignKey, Preference, Table

BILL = ("catalog_sales.cs_bill_addr_sk", "customer_address.ca_address_sk")
SHIP = ("catalog_sales.cs_ship_addr_sk", "customer_address.ca_address_sk")
BILL_ID = "catalog_sales.cs_bill_addr_sk=customer_address.ca_address_sk"
SHIP_ID = "catalog_sales.cs_ship_addr_sk=customer_address.ca_address_sk"

NAMING = """
naming:
  prefixes: { cs_: catalog sales, ss_: store sales, c_: customer, ca_: customer address, s_: store, ws_: web sales }
  words:    { sk: surrogate key, addr: address }
"""

PAIR = ("catalog_sales", "customer_address")


@pytest.fixture
def graph(small_snapshot):
    return build_graph(apply_overlay(small_snapshot, parse_overlay(NAMING)))


def _scores(bill: float, ship: float) -> dict[str, float]:
    return {"catalog_sales.cs_bill_addr_sk": bill, "catalog_sales.cs_ship_addr_sk": ship}


def codes(explanation) -> list[str]:
    return [warning.code for warning in explanation.warnings]


# --------------------------------------------------------------------------
# When evidence decides
# --------------------------------------------------------------------------


def test_evidence_beyond_the_margin_chooses_the_route_the_alphabet_would_not(graph) -> None:
    result = find_paths(graph, *PAIR, evidence=_scores(bill=0.20, ship=0.60), margin=0.10)

    assert result.selected.joins[0].fk_columns == ("cs_ship_addr_sk",)
    assert result.rule == "question_evidence"
    assert result.arbitrary is False
    assert result.evidence == ((SHIP_ID, 0.60), (BILL_ID, 0.20))
    assert result.margin == 0.10
    # Both are still there, and still marked as tied.
    assert len(result.tied) == 2 and len(result.discovered) == 2


def test_a_tie_the_question_decided_does_not_warn_and_says_why(graph) -> None:
    """DD-21: the warning is for a selection with no basis. This one had
    one. The reason gives the scores and names the route not taken."""
    explanation = explain(find_paths(graph, *PAIR, evidence=_scores(bill=0.20, ship=0.60), margin=0.10), graph)

    assert codes(explanation) == []
    assert explanation.reason.rule == "question_evidence"
    text = explanation.reason.text
    assert "0.600" in text and "0.200" in text and "0.100" in text
    assert "Not taken:" in text and "bill address surrogate key" in text
    assert "arbitrary" not in text
    assert explanation.chosen.joins[0].fk_columns == ("cs_ship_addr_sk",)
    assert explanation.alternatives[0].tied_with_chosen is True


def test_evidence_for_the_alphabetical_route_makes_the_same_choice_a_reasoned_one(graph) -> None:
    """The route does not change; what is said about it does. This is the
    difference between two questions that name the same two tables."""
    silent = find_paths(graph, *PAIR)
    told = find_paths(graph, *PAIR, evidence=_scores(bill=0.60, ship=0.20), margin=0.10)

    assert silent.selected == told.selected
    assert (silent.rule, told.rule) == ("alphabetical", "question_evidence")
    assert codes(explain(silent, graph)) == ["arbitrary_choice"]
    assert codes(explain(told, graph)) == []


# --------------------------------------------------------------------------
# When it does not
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ship", [0.25, 0.375, 0.5])
def test_a_difference_within_the_margin_decides_nothing(graph, ship) -> None:
    """At the margin exactly is still within it: evidence decides only by
    more. The numbers are binary fractions, so 0.5 - 0.25 is exactly 0.25;
    with 0.30, 0.20 and 0.10 the difference is 0.0999... and a rule wrongly
    written as "at least the margin" passed this test."""
    result = find_paths(graph, *PAIR, evidence=_scores(bill=0.25, ship=ship), margin=0.25)

    assert result.selected.joins[0].fk_columns == ("cs_bill_addr_sk",)
    assert result.rule == "alphabetical"
    assert result.arbitrary is True
    assert codes(explain(result, graph)) == ["arbitrary_choice"]


def test_evidence_that_did_not_decide_is_still_shown(graph) -> None:
    result = find_paths(graph, *PAIR, evidence=_scores(bill=0.20, ship=0.25), margin=0.10)
    assert result.evidence == ((SHIP_ID, 0.25), (BILL_ID, 0.20))

    text = explain(result, graph).reason.text
    assert "arbitrary" in text
    assert "did not separate them" in text and "0.250" in text and "0.200" in text


def test_just_beyond_the_margin_decides(graph) -> None:
    result = find_paths(graph, *PAIR, evidence=_scores(bill=0.20, ship=0.31), margin=0.10)
    assert result.rule == "question_evidence"


# --------------------------------------------------------------------------
# Its place in the order: after a preference, never above a shorter route
# --------------------------------------------------------------------------


def test_a_declared_preference_comes_before_the_questions_wording(small_snapshot) -> None:
    preference = Preference(between=PAIR, prefer=(BILL,), because="billing is what counts")
    graph = build_graph(replace(apply_overlay(small_snapshot, parse_overlay(NAMING)), preferences=(preference,)))

    result = find_paths(graph, *PAIR, evidence=_scores(bill=0.10, ship=0.90), margin=0.10)
    assert result.rule == "preference"
    assert result.selected.joins[0].fk_columns == ("cs_bill_addr_sk",)
    assert result.evidence == ()


def test_a_preference_that_singles_out_nothing_leaves_the_tie_to_the_evidence_and_is_reported(small_snapshot) -> None:
    stray = ("catalog_sales.cs_bill_customer_sk", "customer.c_customer_sk")
    snapshot = replace(
        small_snapshot,
        columns=small_snapshot.columns + (Column("catalog_sales", "cs_bill_customer_sk", "bigint"),),
        foreign_keys=small_snapshot.foreign_keys
        + (ForeignKey("catalog_sales", ("cs_bill_customer_sk",), "customer", ("c_customer_sk",), "catalog", "x_fk"),),
    )
    preference = Preference(between=PAIR, prefer=(stray,), because="a mistake")
    graph = build_graph(replace(apply_overlay(snapshot, parse_overlay(NAMING)), preferences=(preference,)))

    result = find_paths(graph, *PAIR, evidence=_scores(bill=0.20, ship=0.60), margin=0.10)
    assert result.rule == "question_evidence"
    assert result.preference_not_applied == preference
    assert codes(explain(result, graph)) == ["preference_not_applied"]


def test_evidence_never_promotes_a_longer_route(graph) -> None:
    """store_sales reaches customer_address in one join, and in two through
    customer. However strongly the wording favours the customer's own
    address, the one-join route wins by rule 1 and rule 3 is never asked."""
    evidence = {
        "store_sales.ss_addr_sk": 0.0,
        "store_sales.ss_customer_sk": 1.0,
        "customer.c_current_addr_sk": 1.0,
        "customer.c_customer_sk": 1.0,
    }
    result = find_paths(graph, "store_sales", "customer_address", evidence=evidence, margin=0.0)
    assert result.selected.length == 1
    assert result.rule == "shortest"
    assert result.evidence == () and result.margin is None


# --------------------------------------------------------------------------
# How a tied path is scored
# --------------------------------------------------------------------------


def test_a_path_is_scored_by_the_columns_that_tell_it_apart(graph) -> None:
    """Both routes end at ca_address_sk, so that column says nothing about
    either and is not looked up at all: no score for it is supplied here."""
    tied = find_paths(graph, *PAIR).tied
    assert score_tied(tied, _scores(bill=0.3, ship=0.7)) == {BILL_ID: 0.3, SHIP_ID: 0.7}


def test_a_two_join_route_is_scored_by_the_mean_of_its_telling_columns(small_snapshot) -> None:
    """A second table bridging store and customer_address. The two routes
    share both ends' key columns and differ in the two foreign keys each."""
    snapshot = replace(
        small_snapshot,
        tables=small_snapshot.tables + (Table("web_sales"),),
        columns=small_snapshot.columns
        + (Column("web_sales", "ws_addr_sk", "bigint"), Column("web_sales", "ws_store_sk", "bigint")),
        foreign_keys=small_snapshot.foreign_keys
        + (
            ForeignKey("web_sales", ("ws_addr_sk",), "customer_address", ("ca_address_sk",), "catalog", "a_fk"),
            ForeignKey("web_sales", ("ws_store_sk",), "store", ("s_store_sk",), "catalog", "b_fk"),
        ),
    )
    graph = build_graph(apply_overlay(snapshot, parse_overlay(NAMING)))
    evidence = {
        "store_sales.ss_addr_sk": 0.2,
        "store_sales.ss_store_sk": 0.4,
        "web_sales.ws_addr_sk": 0.9,
        "web_sales.ws_store_sk": 0.5,
    }
    result = find_paths(graph, "store", "customer_address", evidence=evidence, margin=0.1)

    assert [score for _, score in result.evidence] == pytest.approx([0.7, 0.3])
    assert result.rule == "question_evidence"
    assert result.selected.tables == ("store", "web_sales", "customer_address")
    # The alphabet alone would have gone through store_sales.
    assert find_paths(graph, "store", "customer_address").selected.tables[1] == "store_sales"


def test_among_three_the_best_must_beat_the_second_not_merely_the_last(small_snapshot) -> None:
    snapshot = replace(
        small_snapshot,
        columns=small_snapshot.columns + (Column("catalog_sales", "cs_mail_addr_sk", "bigint"),),
        foreign_keys=small_snapshot.foreign_keys
        + (ForeignKey("catalog_sales", ("cs_mail_addr_sk",), "customer_address", ("ca_address_sk",), "catalog", "m"),),
    )
    graph = build_graph(apply_overlay(snapshot, parse_overlay(NAMING)))
    evidence = {**_scores(bill=0.10, ship=0.60), "catalog_sales.cs_mail_addr_sk": 0.55}

    close = find_paths(graph, *PAIR, evidence=evidence, margin=0.10)
    assert close.rule == "alphabetical"
    assert close.selected.joins[0].fk_columns == ("cs_bill_addr_sk",)

    clear = find_paths(graph, *PAIR, evidence={**evidence, "catalog_sales.cs_mail_addr_sk": 0.40}, margin=0.10)
    assert clear.rule == "question_evidence"
    assert clear.selected.joins[0].fk_columns == ("cs_ship_addr_sk",)


def test_a_telling_column_with_no_score_raises(graph) -> None:
    """A missing score read as 0 would decide ties silently."""
    with pytest.raises(ValueError, match="no score was supplied"):
        find_paths(graph, *PAIR, evidence={"catalog_sales.cs_bill_addr_sk": 0.5}, margin=0.1)


def test_choose_alone_falls_back_to_the_first_of_the_tied_paths(graph) -> None:
    tied = find_paths(graph, *PAIR).tied
    assert choose(tied).rule == "alphabetical"
    assert choose(tied).selected is tied[0]
    assert choose(tied, preferred=(tied[1],)).selected is tied[1]


# --------------------------------------------------------------------------
# Without evidence nothing has changed
# --------------------------------------------------------------------------


def test_handed_no_evidence_the_result_is_the_one_step_4_gave(graph) -> None:
    result = find_paths(graph, *PAIR)
    assert (result.rule, result.evidence, result.margin) == ("alphabetical", (), None)
    assert "wording" not in explain(result, graph).reason.text
