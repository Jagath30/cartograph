"""A plain sentence for each loud warning, stored with the warning (step 9,
the owner's ruling at stop 1; trace_version 2).

The amber block at the head of the panel is for a reader who does not
write SQL (DD-19, DD-21, criterion 14), and a warning's `text` is the
Explainer's: "customer demographics row", "surrogate key". So each loud
warning also carries `plain`, composed once by the narrative module from
its own role vocabulary (DD-06) and stored with it.

Each ends as the narrative does: "... the answer may differ; ask again
naming it." (stop 2, ruling 10).

  A   only a loud warning has one; a quiet one has None.
  B   followed: what the answer uses, what it could equally have used,
      that the question did not say, and that the answer may differ.
  C   other_route_taken: what the answer uses, and that it is not what
      was planned.
  D   unknown: what was planned and what it was tied with, and that it is
      not confirmed which the answer uses.
  E   many_to_many: what happened to the totals. Nothing was chosen, so
      nothing is "meant".
  F   it speaks of meaning: no identifier, no "row", no "surrogate key".
  G   the narrative's own five strings are what they were.

The trees are built by the real JoinTree on TPC-DS; the "model's" SQL is
written by hand.
"""

import re
from types import SimpleNamespace

import pytest
from tracing import document, reply

from app.core.narrative import ASK_AGAIN_ONE, ASK_AGAIN_SEVERAL, ASK_AGAIN_UNSURE, _role_alone, plain_warnings
from app.core.trace_document import TRACE_VERSION, TraceDocument

D4 = ("customer_demographics", "web_sales", "web_site", "web_page")
D4_SQL = (
    "SELECT cd.cd_education_status, COUNT(*) FROM web_sales ws JOIN customer_demographics cd "
    "ON ws.ws_bill_cdemo_sk = cd.cd_demo_sk GROUP BY cd.cd_education_status"
)
D7 = ("catalog_sales", "catalog_returns", "customer_demographics", "customer", "date_dim")
D7_SQL = (
    "SELECT d.d_day_name, SUM(cs.cs_quantity) FROM catalog_sales cs JOIN customer_demographics cd "
    "ON cs.cs_bill_cdemo_sk = cd.cd_demo_sk JOIN date_dim d ON cs.cs_sold_date_sk = d.d_date_sk GROUP BY d.d_day_name"
)
ADDRESS = ("catalog_sales", "customer_address")
BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
SHIP = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk"
UNREAD = (
    "SELECT COUNT(*) FROM catalog_sales cs WHERE cs.cs_bill_addr_sk IN "
    "(SELECT ca.ca_address_sk FROM customer_address ca WHERE ca.ca_state = 'TN')"
)
CUSTOMER = ("catalog_sales", "customer")
CUSTOMER_SQL = "SELECT c.c_last_name FROM catalog_sales cs JOIN customer c ON cs.cs_bill_customer_sk = c.c_customer_sk"
CUSTOMER_DATES = ("customer", "date_dim")
FIRST_SALE = "SELECT d.d_year FROM customer c JOIN date_dim d ON c.c_first_sales_date_sk = d.d_date_sk"
LAST_REVIEW = "SELECT d.d_year FROM customer c JOIN date_dim d ON c.c_last_review_date_sk = d.d_date_sk"
PIVOT = ("store", "store_sales", "store_returns")
STORE_SQL = "SELECT s.s_store_name FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk"
BOTH_SIDES = STORE_SQL + " JOIN store_returns sr ON sr.sr_store_sk = s.s_store_sk"

NOT_SAID = "nothing in your question said which"


def warnings(sql: str, *anchors: str):
    return document([reply(sql)], *anchors).paths.warnings


def loud(sql: str, *anchors: str) -> list[str]:
    return [warning.plain for warning in warnings(sql, *anchors) if warning.loud]


# ---- A: which warnings have one ---------------------------------------------


def test_every_loud_warning_has_a_plain_sentence_and_no_quiet_one_does() -> None:
    made = warnings(D7_SQL, *D7)
    assert [w.loud for w in made] == [True, False, False, True]
    assert [w.plain is not None for w in made] == [True, False, False, True]
    assert all(w.plain.strip() for w in made if w.loud)


def test_a_query_that_used_none_of_the_plan_has_no_plain_sentence_at_all() -> None:
    made = warnings("SELECT COUNT(*) FROM catalog_sales", *ADDRESS)
    assert made and not any(w.loud for w in made)
    assert [w.plain for w in made] == [None] * len(made)


def test_a_decline_has_none() -> None:
    made = document([reply(status="not_answerable")], *D4).paths.warnings
    assert made and [w.plain for w in made] == [None] * len(made)


def test_the_explainers_text_is_kept_beside_it_unchanged() -> None:
    (warning,) = warnings(BILL, *ADDRESS)
    assert warning.text.startswith("I had no basis for this choice. 2 routes of 1 join connect")
    assert warning.plain != warning.text


# ---- B: followed ------------------------------------------------------------


def test_the_owners_example_word_for_word() -> None:
    """Stop 1, ruling 2."""
    assert loud(D4_SQL, *D4) == [
        "This answer uses the billed customer's demographics. It could equally have used the shipped-to "
        "customer's; nothing in your question said which. If you meant the other, the answer may differ; "
        "ask again naming it."
    ]


def test_a_pair_with_a_phrase_of_its_own_is_said_whole() -> None:
    assert loud(BILL, *ADDRESS) == [
        f"This answer uses the billing address. It could equally have used the shipping address; {NOT_SAID}. "
        f"{ASK_AGAIN_ONE}"
    ]


def test_the_noun_is_dropped_only_when_it_ends_a_possessive() -> None:
    """ "the shipped-to customer's" stands for "…'s demographics"; "the
    shipped-to" would stand for nothing."""
    assert loud(CUSTOMER_SQL, *CUSTOMER) == [
        f"This answer uses the billed customer. It could equally have used the shipped-to customer; {NOT_SAID}. "
        f"{ASK_AGAIN_ONE}"
    ]


def test_several_alternatives_are_grouped_as_the_narrative_groups_them_and_asked_about_as_several() -> None:
    assert loud(FIRST_SALE, *CUSTOMER_DATES) == [
        "This answer uses the date of first purchase. It could equally have used the dates of first shipment and "
        f"last review; {NOT_SAID}. {ASK_AGAIN_SEVERAL}"
    ]


def test_a_choice_between_two_places_says_whose_each_is() -> None:
    """d7's date table could hang on the sale or on the customer."""
    assert loud(D7_SQL, *D7) == [
        "This answer uses the billed customer's demographics. It could equally have used the shipped-to "
        f"customer's; {NOT_SAID}. {ASK_AGAIN_ONE}",
        "This answer uses the catalog sale's date of sale. It could equally have used the customer's dates of first "
        f"purchase, first shipment and last review; {NOT_SAID}. {ASK_AGAIN_SEVERAL}",
    ]


def test_what_a_declared_preference_set_aside_is_not_offered_as_equal() -> None:
    """The ship date and the return's readings were withdrawn by a
    preference: they are in the narrative, and not in the alarm."""
    for sentence in loud(D7_SQL, *D7):
        assert "ship date" not in sentence and "return" not in sentence


def test_it_ends_as_the_narrative_does() -> None:
    """Stop 2, ruling 10: one ending, in the narrative's own words, so
    the amber block and the account beneath it ask the same thing."""
    assert ASK_AGAIN_ONE == "If you meant the other, the answer may differ; ask again naming it."
    assert ASK_AGAIN_SEVERAL == "If you meant one of those, the answer may differ; ask again naming it."
    made = document([reply(D7_SQL)], *D7)
    assert ASK_AGAIN_SEVERAL in made.narrative.not_taken
    one, several = (w.plain for w in made.paths.warnings if w.loud)
    assert one.endswith(ASK_AGAIN_ONE) and several.endswith(ASK_AGAIN_SEVERAL)


# ---- C: the query took the other route ---------------------------------------


def test_a_query_that_took_the_other_route_is_told_what_the_answer_uses() -> None:
    (warning,) = warnings(SHIP, *ADDRESS)
    assert warning.state == "other_route_taken"
    assert warning.plain == (
        "This answer uses the shipping address, not the billing address as planned. They were equally valid; "
        f"{NOT_SAID}. {ASK_AGAIN_ONE}"
    )


def test_with_several_the_one_the_query_took_is_named_and_the_rest_are_still_offered() -> None:
    (warning,) = warnings(LAST_REVIEW, *CUSTOMER_DATES)
    assert warning.state == "other_route_taken"
    assert warning.plain == (
        "This answer uses the date of last review, not the date of first purchase as planned. It could equally have "
        f"used the date of first shipment. They were equally valid; {NOT_SAID}. {ASK_AGAIN_SEVERAL}"
    )


def test_it_never_says_the_answer_uses_what_the_query_did_not() -> None:
    (warning,) = warnings(SHIP, *ADDRESS)
    assert not warning.plain.startswith("This answer uses the billing address")


BRIDGE = ("item", "date_dim")
# The plan bridges item and the calendar through catalog returns; this
# goes through store sales, whole ...
THROUGH_STORE_SALES = (
    "SELECT i.i_category FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk "
    "JOIN date_dim d ON ss.ss_sold_date_sk = d.d_date_sk"
)
# ... and this makes one of that route's two joins and stops.
HALF_THROUGH_STORE_SALES = "SELECT i.i_category FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk"


def test_a_route_of_two_joins_is_named_as_used_when_the_query_made_both() -> None:
    (warning,) = warnings(THROUGH_STORE_SALES, *BRIDGE)
    assert warning.state == "other_route_taken"
    assert warning.plain.startswith(
        "This answer uses a route through store sales (date of sale), not a route through catalog returns "
        "(date of return) as planned. It could equally have used "
    )
    assert warning.plain.endswith(f". They were equally valid; {NOT_SAID}. {ASK_AGAIN_SEVERAL}")


def test_a_route_made_in_part_is_not_named_as_what_the_answer_uses() -> None:
    """One of its two joins is in the query, and that is enough for the
    alarm to be loud. It is not enough to say the answer uses it."""
    (warning,) = warnings(HALF_THROUGH_STORE_SALES, *BRIDGE)
    assert warning.state == "other_route_taken" and warning.loud
    assert "This answer uses" not in warning.plain
    assert warning.plain.startswith(
        "This answer does not use a route through catalog returns (date of return), which was planned. The query "
        "went by one of those it was tied with: a route through "
    )
    assert "a route through store sales (date of sale)" in warning.plain
    assert warning.plain.endswith(f". They were equally valid; {NOT_SAID}. {ASK_AGAIN_SEVERAL}")


# ---- when the noun is dropped -----------------------------------------------

DEMOGRAPHICS = "customer's demographics"
BILLED = (None, ("adj", "billed", DEMOGRAPHICS))
SHIPPED = (None, ("adj", "shipped-to", DEMOGRAPHICS))


def test_the_role_alone_stands_for_one_alternative_that_differs_only_in_its_role() -> None:
    assert _role_alone(BILLED, [SHIPPED]) == "the shipped-to customer's"


@pytest.mark.parametrize(
    ("mine", "theirs"),
    [
        # two alternatives: which noun was dropped from which would not be plain
        (BILLED, [SHIPPED, (None, ("adj", "current", DEMOGRAPHICS))]),
        # another thing altogether
        (BILLED, [(None, ("adj", "shipped-to", "household's demographics"))]),
        # not a role and a thing: an article and a phrase
        (BILLED, [(None, ("plain", "its", DEMOGRAPHICS))]),
        ((None, ("plain", "its", DEMOGRAPHICS)), [SHIPPED]),
        # somebody else's
        (BILLED, [("customer", ("adj", "shipped-to", DEMOGRAPHICS))]),
        (("catalog sale", ("adj", "billed", DEMOGRAPHICS)), [SHIPPED]),
        # nobody's something: "the shipped-to" would stand for nothing
        ((None, ("adj", "billed", "customer")), [(None, ("adj", "shipped-to", "customer"))]),
        ((None, ("of", "date of", "first purchase")), [(None, ("of", "date of", "first shipment"))]),
    ],
)
def test_otherwise_it_is_said_whole(mine, theirs) -> None:
    assert _role_alone(mine, theirs) is None


# ---- D: the query could not all be read -------------------------------------


def test_an_unread_query_is_not_said_to_use_either() -> None:
    (warning,) = warnings(UNREAD, *ADDRESS)
    assert warning.state == "unknown" and warning.loud
    assert warning.plain == (
        f"The plan chose the billing address. It could equally have used the shipping address; {NOT_SAID}. "
        "Part of the query could not be read, so it is not confirmed which this answer uses. "
        "To be sure of the one you meant, ask again naming it."
    )
    assert "This answer uses" not in warning.plain
    # There is no "the other" to mean when it is not known which was used.
    assert "the other" not in warning.plain and warning.plain.endswith(ASK_AGAIN_UNSURE)


# ---- E: totals counted more than once ---------------------------------------


def test_many_to_many_says_what_happened_to_the_totals_and_asks_nothing() -> None:
    made = {w.code: w for w in warnings(BOTH_SIDES, *PIVOT)}
    pivot = made["many_to_many"]
    assert pivot.loud and pivot.plain == (
        "More than one table is linked through store, so their records are paired with one another and totals "
        "can be counted more than once."
    )
    assert "meant" not in pivot.plain
    quiet = {w.code: w for w in warnings(STORE_SQL, *PIVOT)}["many_to_many"]
    assert not quiet.loud and quiet.plain is None


# ---- F: meaning, not mechanics ----------------------------------------------

_IDENTIFIER = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b")

EVERY_LOUD = [
    (D4_SQL, D4), (D7_SQL, D7), (BILL, ADDRESS), (SHIP, ADDRESS), (UNREAD, ADDRESS), (CUSTOMER_SQL, CUSTOMER),
    (FIRST_SALE, CUSTOMER_DATES), (LAST_REVIEW, CUSTOMER_DATES), (BOTH_SIDES, PIVOT),
    (THROUGH_STORE_SALES, ("item", "date_dim")), (HALF_THROUGH_STORE_SALES, ("item", "date_dim")),
]  # fmt: skip


@pytest.mark.parametrize(("sql", "anchors"), EVERY_LOUD)
def test_no_identifier_and_no_mechanics(sql, anchors) -> None:
    said = loud(sql, *anchors)
    assert said
    for sentence in said:
        assert not _IDENTIFIER.search(sentence), sentence
        # "a route through ..." is the narrative's own phrase for a link of two steps.
        for word in (" row", "surrogate", "dimension", " join", "foreign key"):
            assert word not in sentence, (word, sentence)
        assert sentence.endswith(".") and "  " not in sentence and "None" not in sentence


# ---- a warning it has no words for ------------------------------------------


def _sections(made, **changed) -> dict:
    return {
        "outcome": made.outcome, "message": made.message, "retrieval": made.retrieval, "subgraph": made.subgraph,
        "paths": made.paths, "generation": made.generation, "validation": made.validation,
        "execution": made.execution, "names": {"tables": {}, "columns": {}},
    } | changed  # fmt: skip


def test_a_loud_warning_it_cannot_place_gets_none_and_never_a_guess() -> None:
    """A code it does not know, a route that is in no attachment, a
    warning with nothing it is about: the panel falls back to `text`."""
    made = document([reply(BILL)], *ADDRESS)
    (real,) = made.paths.warnings
    for changed in ({"code": "something_new"}, {"about": ["no.such=route.here", *real.about[1:]]}, {"about": []},
                    {"about": real.about[:1]}):  # fmt: skip
        odd = real.model_copy(update=changed)
        paths = made.paths.model_copy(update={"warnings": [odd]})
        assert plain_warnings(**_sections(made, paths=paths)) == [None], changed


def test_a_preference_that_could_not_be_applied_is_said_plainly() -> None:
    made = document([reply(BILL)], *ADDRESS)
    (real,) = made.paths.warnings
    declared = real.model_copy(update={"code": "preference_not_applied", "about": ["a.b=c.d"]})
    paths = made.paths.model_copy(update={"warnings": [declared]})
    assert plain_warnings(**_sections(made, paths=paths)) == [
        "A preference is declared for this warehouse that could not be applied to this choice, so it decided nothing."
    ]


def test_one_for_each_warning_in_the_warnings_own_order() -> None:
    made = document([reply(D7_SQL)], *D7)
    again = plain_warnings(**_sections(made) | {"names": _names()})
    assert again == [w.plain for w in made.paths.warnings] and len(again) == 4


def _names() -> dict:
    from tracing import GRAPH

    return {
        "tables": {t: GRAPH.nodes[t]["readable"] or t for t in GRAPH.nodes if GRAPH.nodes[t].get("kind") == "table"},
        "columns": {
            c: (GRAPH.nodes[c]["readable"] or c).split(" — ", 1)[-1]
            for c in GRAPH.nodes
            if GRAPH.nodes[c].get("kind") == "column"
        },
    }


# ---- G: the version, and what it does not change ----------------------------


def test_the_trace_is_version_2_and_a_warning_holds_plain() -> None:
    made = document([reply(BILL)], *ADDRESS)
    assert TRACE_VERSION == 2 and made.trace_version == 2
    assert "plain" in made.model_dump(mode="json")["paths"]["warnings"][0]


def test_a_version_1_body_without_plain_is_still_a_trace_document() -> None:
    """DD-07: a trace written before this still renders. Its warnings
    have no `plain`, and read as None."""
    body = document([reply(BILL)], *ADDRESS).model_dump(mode="json")
    body["trace_version"] = 1
    for warning in body["paths"]["warnings"]:
        del warning["plain"]
    old = TraceDocument.model_validate(body)
    assert old.trace_version == 1 and [w.plain for w in old.paths.warnings] == [None]
    assert old.paths.warnings[0].text.startswith("I had no basis for this choice.")


def test_the_narrative_does_not_read_it() -> None:
    """The five strings are composed from the same facts as before."""
    from app.core.narrative import narrate

    made = document([reply(D7_SQL)], *D7)
    bare = made.paths.model_copy(update={"warnings": [w.model_copy(update={"plain": None}) for w in made.paths.warnings]})
    assert narrate(**_sections(made, paths=bare) | {"names": _names()}) == made.narrative


def test_a_stand_in_for_a_warning_needs_only_what_the_trace_holds() -> None:
    """Nothing but the trace's own sections is read."""
    made = document([reply(BILL)], *ADDRESS)
    (real,) = made.paths.warnings
    stand_in = SimpleNamespace(code=real.code, about=real.about, state=real.state, loud=real.loud)
    paths = made.paths.model_copy(update={"warnings": [stand_in]})
    assert plain_warnings(**_sections(made, paths=paths) | {"names": _names()}) == [real.plain]
