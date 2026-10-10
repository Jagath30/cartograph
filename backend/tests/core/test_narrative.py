"""The narrative: the stored plain-English account of how an answer was
reached (FR-26, DD-06, DD-19, criterion 14; the owner's ruling 8).

Five labelled strings, composed once by a pure function, for a reader who
does not write SQL: what was found, which route was taken and why, which
routes were not, whether the SQL kept to it, and what came back.

Each test below is one choice the narrative makes. The trees are built by
the real JoinTree on TPC-DS; the "model's" SQL is written by hand.
"""

import re

import pytest
from tracing import GRAPH, Located, Runs, document, located, reply

from app.core.explainer import explain_tree
from app.core.join_tree import build_tree
from app.core.narrative import narrate
from app.shell.model_client import FAILURES, ModelReply

D4 = ("customer_demographics", "web_sales", "web_site", "web_page")
D4_SQL = (
    "SELECT cd.cd_education_status, COUNT(*) FROM web_sales ws JOIN customer_demographics cd "
    "ON ws.ws_bill_cdemo_sk = cd.cd_demo_sk GROUP BY cd.cd_education_status"
)
ADDRESS = ("catalog_sales", "customer_address")
BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
SHIP = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk"
STORE = ("store_sales", "store")
STORE_SQL = "SELECT s.s_store_name FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk"
# The sentences are the Explainer's. In these tests every key comes from
# the overlay file (the DDL declares none), so each sentence goes on to say
# so; on the live warehouse the catalog declares them and it does not.
BILL_SENTENCE = "Each customer address row has many catalog sales rows, through their bill address surrogate key"
SHIP_SENTENCE = "Each customer address row has many catalog sales rows, through their ship address surrogate key"


def told(script, *anchors, **arguments):
    return document(script, *anchors, **arguments).narrative


# ---- its shape -------------------------------------------------------------


def test_the_narrative_is_five_labelled_strings_none_of_them_empty() -> None:
    narrative = told([reply(D4_SQL)], *D4)
    assert list(narrative.model_dump()) == ["found", "route", "not_taken", "sql", "result"]
    assert all(isinstance(text, str) and text.strip() for text in narrative.model_dump().values())


def test_it_is_the_same_every_time_it_is_composed_from_the_same_trace() -> None:
    first = document([reply(D4_SQL)], *D4)
    second = document([reply(D4_SQL)], *D4)
    assert first.narrative == second.narrative
    again = narrate(
        outcome=first.outcome, message=first.message, retrieval=first.retrieval, subgraph=first.subgraph,
        paths=first.paths, generation=first.generation, validation=first.validation, execution=first.execution,
        names=NAMES,
    )  # fmt: skip
    assert again == first.narrative


NAMES = {
    "tables": {t: GRAPH.nodes[t]["readable"] or t for t in GRAPH.nodes if GRAPH.nodes[t].get("kind") == "table"},
    "columns": {
        c: (GRAPH.nodes[c]["readable"] or c).split(" — ", 1)[-1] for c in GRAPH.nodes if GRAPH.nodes[c].get("kind") == "column"
    },
}

_IDENTIFIER = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b")


@pytest.mark.parametrize(
    ("script", "anchors", "arguments"),
    [
        ([reply(D4_SQL)], D4, {}),
        ([reply(SHIP)], ADDRESS, {}),
        ([reply(BILL)], ADDRESS, {}),
        ([reply("SELECT COUNT(*) FROM catalog_sales")], ADDRESS, {}),
        ([reply(status="not_answerable")], D4, {}),
        ([reply("SELECT i.i_category FROM item i")], ("item", "date_dim"), {}),
        ([reply(STORE_SQL)], ("store", "store_sales", "store_returns"), {}),
        ([], ("income_band", "ship_mode"), {"max_joins": 1}),
        ([reply("DELETE FROM store")], STORE, {}),
    ],
)
def test_no_table_or_column_is_named_by_its_identifier(script, anchors, arguments) -> None:
    """Criterion 14: a reader who does not write SQL. `ws_bill_cdemo_sk`
    tells that reader nothing; "bill customer demographics surrogate key"
    does."""
    narrative = told(script, *anchors, **arguments)
    for label, text in narrative.model_dump().items():
        assert not _IDENTIFIER.findall(text), (label, text)
        assert "SELECT" not in text and " JOIN " not in text and " FROM " not in text


# ---- what was found --------------------------------------------------------


def test_found_names_the_tables_the_question_matched_in_readable_words() -> None:
    found = told([reply(D4_SQL)], *D4).found
    assert found.startswith("The question matched 4 tables: customer demographics, web sales, web site and web page.")


def test_found_says_which_tables_were_added_to_connect_the_rest() -> None:
    found = told([reply("SELECT i.i_category FROM item i")], "item", "date_dim").found
    assert "The question matched 2 tables: item and date dimension." in found
    assert "To connect them, catalog returns was added: the question did not name it." in found


def test_found_says_which_tables_the_model_was_shown_beyond_the_plan() -> None:
    found = told([reply("SELECT i.i_category FROM item i")], "item", "date_dim").found
    assert "The model was also shown" in found and "store sales" in found
    assert "on routes that were equally possible" in found


def test_found_with_one_table() -> None:
    assert told([reply("SELECT COUNT(*) FROM store")], "store").found == "The question matched one table: store."


# ---- which route, and why: one wording for each rule -----------------------


def test_an_arbitrary_choice_is_said_to_be_arbitrary_in_the_route_itself() -> None:
    route = told([reply(BILL)], *ADDRESS).route
    assert route.startswith("The plan starts from catalog sales. Customer address is joined to catalog sales. ")
    assert BILL_SENTENCE in route
    assert "2 routes were equally short and nothing said which was meant, so the alphabet chose: this choice is arbitrary." in route


def test_a_shortest_route_gives_its_reason_and_no_alarm() -> None:
    route = told([reply(STORE_SQL)], *STORE).route
    assert "16 routes existed; this is the shortest." in route
    assert "arbitrary" not in route


def test_an_only_route_says_so() -> None:
    route = told([reply(STORE_SQL)], *STORE, max_joins=1).route
    assert "It is the only route between them within 1 join." in route


def test_a_declared_preference_is_named_with_its_reason() -> None:
    sql = "SELECT d.d_year FROM catalog_sales cs JOIN date_dim d ON cs.cs_sold_date_sk = d.d_date_sk"
    where = located("catalog_sales", "date_dim")
    assert where.tree.attachments[1].rule == "preference"
    route = told([reply(sql)], where=where).route
    because = where.tree.attachments[1].preference_applied.because
    assert f"2 routes were equally short; a preference declared for this warehouse chose this one, because: {because}" in route
    assert "arbitrary" not in route


def test_the_wording_of_the_question_is_named_when_it_decided() -> None:
    evidence = {"catalog_sales.cs_ship_addr_sk": 0.9, "catalog_sales.cs_bill_addr_sk": 0.1}
    where = located(*ADDRESS, evidence=evidence, margin=0.1)
    assert where.tree.attachments[1].rule == "question_evidence"
    route = told([reply(SHIP)], where=where).route
    assert SHIP_SENTENCE in route
    assert "2 routes were equally short; the wording of the question pointed to this one." in route
    assert "arbitrary" not in route


def test_a_preference_that_left_a_tie_says_both_what_it_set_aside_and_that_the_rest_is_arbitrary() -> None:
    """d7: demographics could hang on the sale or its return, by two keys
    each. The preference sets the return aside; billing against shipping
    is still the alphabet's."""
    where = located("catalog_sales", "catalog_returns", "customer_demographics")
    attachment = where.tree.attachments[2]
    assert attachment.rule == "alphabetical" and len(attachment.withdrawn) == 2
    sql = "SELECT cd.cd_marital_status FROM catalog_sales cs JOIN customer_demographics cd ON cs.cs_bill_cdemo_sk = cd.cd_demo_sk"
    narrative = told([reply(sql)], where=where)
    assert "4 routes were equally short. A preference declared for this warehouse set aside 2 of them, because: " in narrative.route
    assert "Between the 2 left nothing said which was meant, so the alphabet chose: this choice is arbitrary." in narrative.route
    assert "A declared preference set aside 2 more:" in narrative.not_taken


def test_two_declared_reasons_are_each_a_sentence() -> None:
    """d7's date table: one preference sets the return aside and another
    the ship date."""
    where = located("catalog_sales", "catalog_returns", "date_dim")
    attachment = where.tree.attachments[2]
    reasons = [p.because for p in (*attachment.attach_preferences, *attachment.route_preferences)]
    assert len(reasons) == 2
    sql = "SELECT d.d_year FROM catalog_sales cs JOIN date_dim d ON cs.cs_sold_date_sk = d.d_date_sk"
    route = told([reply(sql)], where=where).route
    first, second = (reason.rstrip(".") + "." for reason in reasons)
    assert f"because: {first} Also: {second}" in route and ".." not in route and ".;" not in route


def test_joins_the_query_did_not_use_get_one_sentence_and_no_detail() -> None:
    route = told([reply(D4_SQL)], *D4).route
    assert "Web sales is joined to customer demographics." in route
    assert route.endswith("The plan also joined web site and web page; the query did not use those joins.")
    assert "web site surrogate key" not in route


def test_a_query_that_used_none_of_the_plan_is_told_so_briefly() -> None:
    route = told([reply("SELECT COUNT(*) FROM catalog_sales")], *ADDRESS).route
    assert route == "The plan starts from catalog sales and joins customer address to it; the query used none of the planned joins."


def test_when_no_query_ran_the_whole_plan_is_told() -> None:
    route = told([reply(status="not_answerable")], *D4).route
    for name in ("Web sales is joined", "Web site is joined", "Web page is joined"):
        assert name in route
    assert "did not use" not in route


def test_one_table_has_no_route() -> None:
    assert told([reply("SELECT COUNT(*) FROM store")], "store").route == "Only store was needed, so no join was planned."


# ---- which routes were not taken -------------------------------------------


def test_the_route_not_taken_is_given_in_full() -> None:
    not_taken = told([reply(BILL)], *ADDRESS).not_taken
    assert f"Customer address could equally have been joined another way. {SHIP_SENTENCE}" in not_taken
    assert "151 longer routes also existed and were not considered." in not_taken


def test_longer_routes_are_a_count_and_never_an_alarm() -> None:
    not_taken = told([reply(STORE_SQL)], *STORE).not_taken
    assert not_taken == "No other route was equally short. 15 longer routes also existed and were not considered."


def test_when_the_query_took_the_other_route_not_taken_says_so() -> None:
    not_taken = told([reply(SHIP)], *ADDRESS).not_taken
    assert SHIP_SENTENCE in not_taken
    assert not_taken.endswith("The query itself took one of these other routes, not the one planned.")
    assert "The query itself" not in told([reply(BILL)], *ADDRESS).not_taken


def test_a_choice_the_query_went_against_is_told_and_never_called_unused() -> None:
    """The query took the ship address where the plan chose billing. That
    choice touched the answer: it is told in full, and nothing says it
    did not affect the answer."""
    narrative = told([reply(SHIP)], *ADDRESS)
    assert BILL_SENTENCE in narrative.route and "this choice is arbitrary" in narrative.route
    assert "none of the planned joins" not in narrative.route
    assert "did not affect this answer" not in narrative.not_taken


def test_choices_in_the_unused_part_of_the_plan_are_said_not_to_touch_the_answer() -> None:
    sql = "SELECT COUNT(*) FROM web_sales ws JOIN web_site s ON ws.ws_web_site_sk = s.web_site_sk"
    not_taken = told([reply(sql)], *D4).not_taken
    assert "ship customer demographics" not in not_taken
    assert "Choices were also made in the part of the plan the query did not use; they did not affect this answer." in not_taken


def test_one_table_has_no_route_not_taken() -> None:
    assert told([reply("SELECT COUNT(*) FROM store")], "store").not_taken == "With one table there was no route to choose."


# ---- whether the SQL kept to it: one wording for each outcome --------------


def test_conforms() -> None:
    assert told([reply(STORE_SQL)], *STORE).sql == "The query made exactly the 1 join planned."
    assert told([reply("SELECT COUNT(*) FROM store")], "store").sql == "No join was planned and the query made none."


def test_incomplete_names_the_joins_left_out() -> None:
    sql = told([reply(D4_SQL)], *D4).sql
    assert sql.startswith("The query kept to the plan and did not need all of it: it made 1 of the 3 planned joins. Left out: ")
    assert "web sales to web site (through web site surrogate key)" in sql
    assert "web sales to web page (through web page surrogate key)" in sql


def test_incomplete_with_no_join_at_all() -> None:
    sql = told([reply("SELECT COUNT(*) FROM catalog_sales")], *ADDRESS).sql
    assert sql == "The query read catalog sales and joined nothing: the 1 planned join was not needed."


def test_diverged_names_the_join_made_and_the_join_planned() -> None:
    sql = told([reply(SHIP)], *ADDRESS).sql
    assert sql.startswith("The query did not keep to the plan. ")
    assert "It joined catalog sales to customer address (matching ship address surrogate key to address surrogate key), which the plan did not select." in sql
    assert "Planned and not made: catalog sales to customer address (through bill address surrogate key)." in sql


def test_diverged_by_a_cross_join_says_what_that_does_to_the_rows() -> None:
    sql = told([reply("SELECT COUNT(*) FROM store_sales ss, store s")], *STORE).sql
    assert "The query did not keep to the plan." in sql
    assert "without joining them, so every row of one is paired with every row of the other." in sql


def test_not_checked_does_not_claim_the_plan_was_kept() -> None:
    script = [reply(STORE_SQL + " WHERE ss.ss_item_sk IN (SELECT i.i_item_sk FROM item i)")]
    narrative = document(script, "store_sales", "store", "item")
    assert narrative.validation.conformance == "not_checked"
    sql = narrative.narrative.sql
    assert sql.startswith("Part of the query could not be read with confidence, so it is not confirmed that it kept to the plan.")
    assert "Of what could be read, it made 1 of the 2 planned joins and none outside them." in sql
    assert "kept to the plan and" not in sql and "exactly" not in sql


def test_a_retry_is_mentioned() -> None:
    sql = told([reply("SELECT nothing FROM store"), reply(STORE_SQL)], *STORE).sql
    assert sql.startswith("The model was asked 2 times; the earlier reply was not accepted. The query made exactly")


# ---- what came back: one ending for each outcome ---------------------------


@pytest.mark.parametrize(
    ("rows", "expected"),
    [(2, "2 rows came back."), (1, "1 row came back."), (0, "The query ran and no rows came back.")],
)
def test_answered_gives_the_number_of_rows(rows, expected) -> None:
    assert told([reply(STORE_SQL)], *STORE, executor=Runs(rows=rows)).result == expected


def test_a_result_cut_at_the_cap_says_so() -> None:
    result = told([reply(STORE_SQL)], *STORE, executor=Runs(rows=3, truncated=True)).result
    assert result == "3 rows came back. More rows matched than the limit of 1000 allows; these are the first."


def test_a_decline_by_the_model_lists_the_tables_it_was_shown() -> None:
    """Item 62: the decline is about the tables shown, and the reader is
    given them to judge it by."""
    narrative = told([reply(status="not_answerable")], *D4)
    assert narrative.result == (
        "No answer was given. The model was shown 4 tables: customer demographics, web sales, web site and web page. "
        "It said they do not hold the answer. That is a statement about these tables, not about the whole warehouse."
    )
    assert narrative.sql == "No query was written: the model said the tables it was shown do not hold the answer."


def test_a_decline_for_want_of_any_table_says_no_model_was_asked() -> None:
    tree = build_tree(GRAPH, (), {})
    where = Located(tree, explain_tree(tree, GRAPH))
    narrative = told([], where=where)
    assert narrative.found == "No table matched the question closely enough to start from."
    assert narrative.route == "No route was planned."
    assert narrative.sql == "No query was written, and no model was asked."
    assert narrative.result == "No answer was given: nothing in the tables retrieved matched the question."


def test_a_decline_for_tables_that_do_not_connect_names_them() -> None:
    narrative = told([], "income_band", "ship_mode", max_joins=1)
    assert narrative.found == (
        "The question matched 2 tables: income band and ship mode. "
        "Ship mode could not be connected to the rest within 1 join."
    )
    assert narrative.sql == "No query was written, and no model was asked."
    assert narrative.result == "No answer was given: the tables the question matched could not be joined to one another."


def test_a_refused_write_is_told_plainly() -> None:
    narrative = told([reply("DELETE FROM store")], *STORE)
    assert narrative.sql == (
        "No query was run. What the model wrote would have done something other than read the data. "
        "It was refused, and the model was not asked again."
    )
    assert narrative.result == "No answer was given."


def test_a_query_refused_three_times_is_told_plainly() -> None:
    narrative = told([reply("SELECT nothing FROM store")] * 3, *STORE)
    assert narrative.sql == (
        "No query was run. What the model wrote was refused 3 times: it was not a query this warehouse could run."
    )
    assert narrative.result == "No answer was given."


def test_a_model_failure_is_told_plainly() -> None:
    failed = ModelReply("m", None, None, None, 0, 0, 0, 0.0, 0, "transport", FAILURES["transport"])
    narrative = told([failed], *STORE)
    assert narrative.sql == "No query was written: the model request did not complete."
    assert narrative.result == "No answer was given."


def test_a_query_that_failed_in_the_database_says_it_ran_and_failed() -> None:
    narrative = told([reply(STORE_SQL)], *STORE, executor=Runs(failure="database_error"))
    assert narrative.sql == "The query made exactly the 1 join planned."
    assert narrative.result == "The query was run and the database stopped it: division by zero. No rows came back."


# ---- choices found by mutation, each with its test -------------------------


def test_what_a_preference_set_aside_is_not_counted_among_the_equal_routes() -> None:
    where = located("catalog_sales", "catalog_returns", "customer_demographics")
    sql = "SELECT cd.cd_marital_status FROM catalog_sales cs JOIN customer_demographics cd ON cs.cs_bill_cdemo_sk = cd.cd_demo_sk"
    not_taken = told([reply(sql)], where=where).not_taken
    assert "Customer demographics could equally have been joined another way. " in not_taken
    assert "other ways" not in not_taken


def test_an_unused_join_that_involved_no_choice_is_not_mentioned_as_one() -> None:
    not_taken = told([reply(D4_SQL)], *D4).not_taken
    assert "Choices were also made" not in not_taken


def test_left_out_lists_only_the_joins_left_out() -> None:
    sql = told([reply(D4_SQL)], *D4).sql
    assert sql.count("(through") == 2 and "customer demographics" not in sql


def test_half_a_key_is_not_a_planned_join_made() -> None:
    script = [
        reply(
            "SELECT COUNT(*) FROM store_returns sr JOIN store_sales ss ON sr.sr_item_sk = ss.ss_item_sk "
            "WHERE ss.ss_store_sk IN (SELECT s.s_store_sk FROM store s)"
        )
    ]
    found = document(script, "store_sales", "store_returns")
    assert found.validation.conformance == "not_checked" and found.paths.selected_edges[0].use == "partial"
    assert "it made 0 of the 1 planned joins" in found.narrative.sql


def test_diverged_names_only_the_join_the_plan_did_not_select() -> None:
    sql = told(
        [reply(STORE_SQL + " JOIN customer_address ca ON ss.ss_addr_sk = ca.ca_address_sk")], *STORE
    ).sql
    assert "It joined customer address to store sales (matching address surrogate key to address surrogate key), which" in sql
    assert "store sales to store (matching" not in sql and "Planned and not made" not in sql


def test_a_decline_about_one_table_is_worded_for_one() -> None:
    result = told([reply(status="not_answerable")], "store").result
    assert result == (
        "No answer was given. The model was shown one table: store. It said it does not hold the answer. "
        "That is a statement about this table, not about the whole warehouse."
    )


@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        (1, "For 1 word of the question another table scored about as well; that is listed with the tables retrieved."),
        (2, "For 2 words of the question another table scored about as well; those are listed with the tables retrieved."),
    ],
)
def test_close_calls_are_one_quiet_sentence(calls, expected) -> None:
    from types import SimpleNamespace

    made = document([reply(STORE_SQL)], *STORE)
    narrative = narrate(
        outcome=made.outcome, message=made.message, retrieval=SimpleNamespace(close_calls=[object()] * calls),
        subgraph=made.subgraph, paths=made.paths, generation=made.generation, validation=made.validation,
        execution=made.execution, names=NAMES,
    )  # fmt: skip
    assert narrative.found.endswith(expected)
    assert "another table scored" not in made.narrative.found


def test_an_anchor_dropped_to_keep_within_the_bound_is_named() -> None:
    where = located("catalog_sales", "catalog_returns", "customer_demographics", "customer", "date_dim", subgraph_bound=3)
    assert where.tree.subgraph_bound.dropped_anchors
    found = told([reply("SELECT COUNT(*) FROM catalog_sales")], where=where).found
    dropped = where.tree.subgraph_bound.dropped_anchors
    assert f"left out to keep the plan within 3 tables." in found
    assert len(dropped) >= 1 and "also matched and" in found
