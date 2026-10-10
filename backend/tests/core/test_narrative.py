"""The narrative: the stored plain-English account of how an answer was
reached (FR-26, DD-06, DD-19, criterion 14).

Five labelled strings, composed once by a pure function, for a manager
who does not write SQL. The owner's rules at stop 2 of step 8, each
tested below by its number:

  1  meaning, not mechanics: no "row", "surrogate key", "dimension";
  2  for each choice that touched the answer, what was used and what the
     alternatives were; an arbitrary one says so and says to ask again;
  3  a declared preference's reason once, as a clause;
  4  longer routes in one sentence in all, "the shortest was used";
  5  the unused part of the plan in one sentence; a close call names its
     word and its tables;
  7  plain phrases from a vocabulary only the narrative reads, with a
     fallback to the readable name without "surrogate key".

The trees are built by the real JoinTree on TPC-DS; the "model's" SQL is
written by hand.
"""

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from tracing import GRAPH, Located, Runs, document, located, reply

from app.core.explainer import explain_tree
from app.core.join_tree import build_tree
from app.core.narrative import ASK_AGAIN_ONE, ASK_AGAIN_SEVERAL, narrate
from app.shell.model_client import FAILURES, ModelReply

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
STORE = ("store_sales", "store")
STORE_SQL = "SELECT s.s_store_name FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk"
BRIDGE_SQL = (
    "SELECT i.i_category FROM catalog_returns cr JOIN item i ON cr.cr_item_sk = i.i_item_sk "
    "JOIN date_dim d ON cr.cr_returned_date_sk = d.d_date_sk"
)
ARBITRARY = "nothing in your question said which you meant, so the choice was made alphabetically: it is arbitrary."
RETURN_REASON = "a return refers to its sale, so whatever either could carry is taken to describe the sale"
DATE_REASON = "a question that names no date means the date of sale"

NAMES = {
    "tables": {t: GRAPH.nodes[t]["readable"] or t for t in GRAPH.nodes if GRAPH.nodes[t].get("kind") == "table"},
    "columns": {
        c: (GRAPH.nodes[c]["readable"] or c).split(" — ", 1)[-1] for c in GRAPH.nodes if GRAPH.nodes[c].get("kind") == "column"
    },
}


def told(script, *anchors, **arguments):
    return document(script, *anchors, **arguments).narrative


def again(made, **changed):
    """The narrative composed afresh from a document's own sections."""
    sections = {
        "outcome": made.outcome, "message": made.message, "retrieval": made.retrieval, "subgraph": made.subgraph,
        "paths": made.paths, "generation": made.generation, "validation": made.validation,
        "execution": made.execution, "names": NAMES,
    }  # fmt: skip
    return narrate(**(sections | changed))


def whole(narrative) -> str:
    return " ".join(narrative.model_dump().values())


# ---- its shape -------------------------------------------------------------


def test_the_narrative_is_five_labelled_strings_none_of_them_empty() -> None:
    narrative = told([reply(D4_SQL)], *D4)
    assert list(narrative.model_dump()) == ["found", "route", "not_taken", "sql", "result"]
    assert all(isinstance(text, str) and text.strip() for text in narrative.model_dump().values())


def test_it_is_the_same_every_time_it_is_composed_from_the_same_trace() -> None:
    first = document([reply(D7_SQL)], *D7)
    assert first.narrative == document([reply(D7_SQL)], *D7).narrative
    assert again(first) == first.narrative


def test_the_guide_the_owner_gave_for_d4() -> None:
    """The style to aim for, held as a whole so that a change to any part
    of it is seen."""
    narrative = told([reply(D4_SQL)], *D4, executor=Runs(rows=7))
    assert narrative.found == "Your question matched 4 tables: customer demographics, web sales, web site and web page."
    assert narrative.route == (
        "The answer links each web sale to the billed customer's demographics. Each web sale also records the "
        f"shipped-to customer's demographics; {ARBITRARY} The plan also brought in web site and web page; "
        "the query did not use them."
    )
    assert narrative.not_taken == (
        "Not used: the shipped-to customer's demographics. If you meant the other, the answer may differ; "
        "ask again naming it. 150 longer routes also existed; the shortest was used."
    )
    assert narrative.sql == "The query followed this plan, using 1 of its 3 joins; web site and web page were not needed."
    assert narrative.result == "7 rows came back."


# ---- rule 1: meaning, not mechanics ----------------------------------------

_IDENTIFIER = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b")

EVERY_SHAPE = [
    ([reply(D4_SQL)], D4, {}),
    ([reply(D7_SQL)], D7, {}),
    ([reply(SHIP)], ADDRESS, {}),
    ([reply(BILL)], ADDRESS, {}),
    ([reply("SELECT COUNT(*) FROM catalog_sales")], ADDRESS, {}),
    ([reply(status="not_answerable")], D4, {}),
    ([reply(BRIDGE_SQL)], ("item", "date_dim"), {}),
    ([reply("SELECT i.i_category FROM item i")], ("item", "date_dim"), {}),
    ([reply(STORE_SQL + " JOIN store_returns sr ON sr.sr_store_sk = s.s_store_sk")], ("store", "store_sales", "store_returns"), {}),
    ([reply("SELECT COUNT(*) FROM store_sales ss, store s")], STORE, {}),
    ([], ("income_band", "ship_mode"), {"max_joins": 1}),
    ([reply("DELETE FROM store")], STORE, {}),
    ([reply("SELECT COUNT(*) FROM time_dim t JOIN store_sales ss ON ss.ss_sold_time_sk = t.t_time_sk")], ("store_sales", "time_dim"), {}),
]


@pytest.mark.parametrize(("script", "anchors", "arguments"), EVERY_SHAPE)
def test_no_table_or_column_is_named_by_its_identifier(script, anchors, arguments) -> None:
    narrative = told(script, *anchors, **arguments)
    for label, text in narrative.model_dump().items():
        assert not _IDENTIFIER.findall(text), (label, text)
        assert "SELECT" not in text and " JOIN " not in text and " FROM " not in text


@pytest.mark.parametrize(("script", "anchors", "arguments"), EVERY_SHAPE)
def test_it_speaks_of_meaning_and_not_of_mechanics(script, anchors, arguments) -> None:
    """Rule 1, and rule 4's "never not considered"."""
    narrative = told(script, *anchors, **arguments)
    for label in ("found", "route", "not_taken", "sql"):
        text = getattr(narrative, label).lower()
        for banned in ("row", "surrogate key", "dimension", "the plan starts from", "not considered", "foreign key"):
            assert banned not in text, (label, banned, text)
    assert "surrogate" not in narrative.result and "dimension" not in narrative.result


def test_the_date_and_time_tables_have_plain_names() -> None:
    assert "catalog sales and calendar dates." in told([reply("SELECT 1 FROM catalog_sales")], "catalog_sales", "date_dim").found
    assert "store sales and times of day." in told([reply("SELECT 1 FROM store_sales")], "store_sales", "time_dim").found


# ---- what was found --------------------------------------------------------


def test_found_names_the_tables_added_to_link_the_rest() -> None:
    found = told([reply(BRIDGE_SQL)], "item", "date_dim").found
    assert found.startswith("Your question matched 2 tables: item and calendar dates. ")
    assert "To link them, catalog returns was added: your question did not name it." in found
    assert "The model was also shown" in found and "store sales" in found and "could have linked them equally well" in found


def test_found_with_one_table() -> None:
    assert told([reply("SELECT COUNT(*) FROM store")], "store").found == "Your question matched one table: store."


def test_a_close_call_names_its_word_and_its_tables_in_one_sentence() -> None:
    """Rule 5."""
    made = document([reply(STORE_SQL)], *STORE)
    calls = [
        SimpleNamespace(term="buyer", chosen="customer", rival="customer_address"),
        SimpleNamespace(term="day", chosen="date_dim", rival="time_dim"),
    ]
    found = again(made, retrieval=SimpleNamespace(close_calls=calls)).found
    assert found.endswith(
        '"Buyer" could also have meant customer address; customer was used. '
        '"Day" could also have meant times of day; calendar dates was used.'
    )
    assert "could also have meant" not in made.narrative.found


def test_an_anchor_dropped_to_keep_within_the_bound_is_named() -> None:
    where = located(*D7, subgraph_bound=3)
    assert where.tree.subgraph_bound.dropped_anchors
    found = told([reply("SELECT COUNT(*) FROM catalog_sales")], where=where).found
    assert "also matched and" in found and "left out to keep the plan within 3 tables." in found


# ---- rule 2: what was used, the alternatives, and the basis ----------------


def test_what_was_used_is_one_sentence_of_meaning() -> None:
    assert told([reply(BILL)], *ADDRESS).route.startswith("The answer links each catalog sale to the billing address. ")
    assert told([reply(STORE_SQL)], *STORE).route == "The answer links each store sale to its store."


def test_an_arbitrary_choice_names_the_alternative_says_so_and_says_to_ask_again() -> None:
    narrative = told([reply(BILL)], *ADDRESS)
    assert narrative.route == (
        f"The answer links each catalog sale to the billing address. Each catalog sale also records the shipping address; {ARBITRARY}"
    )
    assert narrative.not_taken.startswith(f"Not used: the shipping address. {ASK_AGAIN_ONE}")
    assert ASK_AGAIN_ONE == "If you meant the other, the answer may differ; ask again naming it."


def test_several_alternatives_are_asked_about_as_several() -> None:
    narrative = told([reply(D7_SQL)], *D7)
    assert (
        "Equally possible: the customer's date of first purchase, the customer's date of first shipment and the "
        f"customer's date of last review; {ARBITRARY}"
    ) in narrative.route
    assert (
        "Not used: the customer's date of first purchase, the customer's date of first shipment and the customer's "
        f"date of last review. {ASK_AGAIN_SEVERAL}"
    ) in narrative.not_taken


def test_a_shortest_or_only_route_has_no_alternative_to_name_and_no_alarm() -> None:
    for arguments in ({}, {"max_joins": 1}):
        narrative = told([reply(STORE_SQL)], *STORE, **arguments)
        assert narrative.route == "The answer links each store sale to its store."
        assert "arbitrary" not in whole(narrative) and "ask again" not in whole(narrative)
    assert told([reply(STORE_SQL)], *STORE).not_taken == "Nothing else was equally short. 15 longer routes also existed; the shortest was used."
    assert told([reply(STORE_SQL)], *STORE, max_joins=1).not_taken == "Nothing else was equally short."


def test_a_declared_preference_names_what_it_chose_over_and_is_not_an_alarm() -> None:
    where = located("catalog_sales", "date_dim")
    assert where.tree.attachments[1].rule == "preference"
    narrative = told([reply("SELECT d.d_year FROM catalog_sales cs JOIN date_dim d ON cs.cs_sold_date_sk = d.d_date_sk")], where=where)
    assert narrative.route == (
        "The answer links each catalog sale to its date of sale. The ship date was equally possible; a preference "
        f"declared for this warehouse chose this one, because {DATE_REASON} (an operator's default)."
    )
    assert narrative.not_taken.startswith("Not used: the ship date. 152 longer")
    assert "arbitrary" not in whole(narrative) and "ask again" not in whole(narrative)


def test_a_preference_between_two_places_names_the_place_it_set_aside() -> None:
    """An item could describe the sale or its return. The preference
    chooses the sale outright: what it set aside is the alternative."""
    where = located("store_sales", "store_returns", "item")
    attachment = where.tree.attachments[2]
    assert attachment.rule == "preference" and len(attachment.withdrawn) == 1
    narrative = told([reply("SELECT 1 FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk")], where=where)
    assert narrative.route.startswith(
        "The answer links each store sale to its item. The store return's item was equally possible; a preference "
        f"declared for this warehouse chose this one, because {RETURN_REASON} (an operator's default)."
    )
    assert "Set aside by a declared preference: the store return's item." in narrative.not_taken
    assert "arbitrary" not in whole(narrative)


def test_the_wording_of_the_question_is_named_when_it_decided() -> None:
    evidence = {"catalog_sales.cs_ship_addr_sk": 0.9, "catalog_sales.cs_bill_addr_sk": 0.1}
    where = located(*ADDRESS, evidence=evidence, margin=0.1)
    assert where.tree.attachments[1].rule == "question_evidence"
    narrative = told([reply(SHIP)], where=where)
    assert narrative.route == (
        "The answer links each catalog sale to the shipping address. The billing address was equally possible; "
        "the wording of your question pointed to this one."
    )
    assert "arbitrary" not in whole(narrative) and "ask again" not in whole(narrative)


def test_a_preference_that_left_a_tie_says_what_it_set_aside_and_that_the_rest_is_arbitrary() -> None:
    where = located("catalog_sales", "catalog_returns", "customer_demographics")
    assert where.tree.attachments[2].rule == "alphabetical" and len(where.tree.attachments[2].withdrawn) == 2
    sql = "SELECT cd.cd_marital_status FROM catalog_sales cs JOIN customer_demographics cd ON cs.cs_bill_cdemo_sk = cd.cd_demo_sk"
    narrative = told([reply(sql)], where=where)
    assert narrative.route.startswith(
        "The answer links each catalog sale to the billed customer's demographics. A preference declared for this "
        "warehouse set aside the catalog return's refunded customer's demographics and the catalog return's returning "
        f"customer's demographics, because {RETURN_REASON} (an operator's default). "
        f"Each catalog sale also records the shipped-to customer's demographics; {ARBITRARY}"
    )
    assert f"Not used: the shipped-to customer's demographics. {ASK_AGAIN_ONE}" in narrative.not_taken
    assert (
        "Set aside by a declared preference: the catalog return's refunded customer's demographics and the catalog "
        "return's returning customer's demographics."
    ) in narrative.not_taken


def test_a_route_of_two_joins_is_told_through_the_table_between() -> None:
    route = told([reply(BRIDGE_SQL)], "item", "date_dim").route
    assert route.startswith(
        "The answer links calendar dates and item through catalog returns: each catalog return to its date of return "
        "and each catalog return to its item. "
    )


def test_many_alternatives_are_counted_in_the_route_and_named_once_under_not_used() -> None:
    narrative = told([reply(BRIDGE_SQL)], "item", "date_dim")
    assert f"10 other readings were equally possible, named below; {ARBITRARY}" in narrative.route
    assert "a route through" not in narrative.route
    assert narrative.not_taken.startswith("Not used: a route through catalog sales (ship date), a route through catalog sales (date of sale), ")
    assert "a route through store sales (date of sale)" in narrative.not_taken and ASK_AGAIN_SEVERAL in narrative.not_taken


def test_a_second_choice_is_introduced_as_a_second() -> None:
    route = told([reply(D7_SQL)], *D7).route
    assert route.startswith("The answer links each catalog sale to the billed customer's demographics. ")
    assert " It also links each catalog sale to its date of sale. " in route and route.count("The answer links") == 1


def test_a_choice_the_query_went_against_is_told_and_never_called_unused() -> None:
    """The query took the shipping address where the plan chose billing.
    That choice touched the answer: it is told, the reader is told to ask
    again, and nothing says it did not affect the answer."""
    narrative = told([reply(SHIP)], *ADDRESS)
    assert "the billing address" in narrative.route and "it is arbitrary" in narrative.route
    assert "none of those links" not in narrative.route
    assert "did not affect this answer" not in narrative.not_taken
    assert "The query itself used one of these and not the one planned." in narrative.not_taken
    assert "The query itself" not in told([reply(BILL)], *ADDRESS).not_taken


def test_a_query_that_used_none_of_the_plan_is_told_so_and_nothing_alarms() -> None:
    narrative = told([reply("SELECT COUNT(*) FROM catalog_sales")], *ADDRESS)
    assert narrative.route == (
        "The plan linked catalog sales and customer address; the query used none of those links, so no choice "
        "among them affected this answer."
    )
    assert narrative.not_taken == (
        "Other choices were made in the part of the plan the query did not use; they did not affect this answer."
    )
    assert "arbitrary" not in whole(narrative)


def test_when_no_query_ran_the_whole_plan_is_told() -> None:
    route = told([reply(status="not_answerable")], *D4).route
    for name in ("to the billed customer's demographics", "to its web site", "to its web page"):
        assert name in route
    assert "did not use" not in route


def test_one_table_has_nothing_to_link_or_to_choose() -> None:
    narrative = told([reply("SELECT COUNT(*) FROM store")], "store")
    assert narrative.route == "Only store was needed, so there was nothing to link."
    assert narrative.not_taken == "With one table there was nothing to choose between."


# ---- rule 3: a preference's reason once, as a clause -----------------------


def test_each_declared_reason_is_given_once_in_a_narrative() -> None:
    """d7: the return is set aside for the demographics and again for the
    date, and the ship date for the date. Two reasons, each once."""
    text = whole(told([reply(D7_SQL)], *D7))
    assert text.count(RETURN_REASON) == 1 and text.count(DATE_REASON) == 1
    assert f", because {DATE_REASON} (an operator's default). " in text
    assert "because:" not in text and "Operator default." not in text


def test_a_reason_already_given_is_referred_to_and_not_repeated() -> None:
    where = located("catalog_sales", "catalog_returns", "customer_demographics", "household_demographics")
    sql = (
        "SELECT COUNT(*) FROM catalog_sales cs JOIN customer_demographics cd ON cs.cs_bill_cdemo_sk = cd.cd_demo_sk "
        "JOIN household_demographics hd ON cs.cs_bill_hdemo_sk = hd.hd_demo_sk"
    )
    route = told([reply(sql)], where=where).route
    assert route.count(RETURN_REASON) == 1
    assert (
        "set aside the catalog return's refunded household's demographics and the catalog return's returning "
        "household's demographics, for the reason already given. "
    ) in route


def test_the_reasons_in_the_overlay_read_as_clauses() -> None:
    from tracing import SNAPSHOT

    for preference in (*SNAPSHOT.preferences, *SNAPSHOT.attach_preferences):
        because = preference.because
        assert because[0].islower() and not because.endswith(".") and "Operator default." not in because


# ---- rule 4: longer routes, one sentence in all ----------------------------


def test_longer_routes_get_one_sentence_however_many_choices_there_were() -> None:
    narrative = told([reply(D7_SQL)], *D7)
    assert whole(narrative).count("longer route") == 1
    assert "732 longer routes also existed; the shortest was used." in narrative.not_taken
    one = told([reply(STORE_SQL)], *STORE)
    assert again(document([reply(STORE_SQL)], *STORE)) == one


def test_one_longer_route_is_singular() -> None:
    made = document([reply(STORE_SQL)], *STORE)
    attachments = [made.paths.attachments[0], made.paths.attachments[1].model_copy(update={"discovered": 2})]
    narrative = again(made, paths=made.paths.model_copy(update={"attachments": attachments}))
    assert narrative.not_taken == "Nothing else was equally short. 1 longer route also existed; the shortest was used."


# ---- rule 5: the unused part of the plan, one sentence ---------------------


def test_the_unused_part_of_the_plan_is_one_sentence_each_way() -> None:
    narrative = told([reply(D4_SQL)], *D4)
    assert narrative.route.endswith("The plan also brought in web site and web page; the query did not use them.")
    assert "its web site" not in narrative.route and "Other choices" not in narrative.not_taken

    sql = "SELECT COUNT(*) FROM web_sales ws JOIN web_site s ON ws.ws_web_site_sk = s.web_site_sk"
    other = told([reply(sql)], *D4)
    assert "shipped-to" not in whole(other)
    assert other.not_taken.endswith(
        "Other choices were made in the part of the plan the query did not use; they did not affect this answer."
    )
    assert other.route.endswith("The plan also brought in web sales and web page; the query did not use them.")


def test_one_unused_table_is_singular() -> None:
    sql = "SELECT COUNT(*) FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk"
    route = told([reply(sql)], "store_sales", "store", "item").route
    assert route.endswith("The plan also brought in item; the query did not use it.")


# ---- rule 7: the vocabulary ------------------------------------------------


def test_only_the_narrative_reads_the_vocabulary() -> None:
    """Ingestion must not: descriptions, embeddings and the snapshot stay
    what they were."""
    app = Path(__file__).resolve().parents[2] / "app"
    readers = set()
    for path in app.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""] + [f"{node.module}.{alias.name}" for alias in node.names]
            if any(module.endswith("narrative_words") for module in modules):
                readers.add(path.name)
    assert readers == {"narrative.py"}


@pytest.mark.parametrize(
    ("anchors", "sql", "phrase"),
    [
        (("web_sales", "customer"), "SELECT 1 FROM web_sales ws JOIN customer c ON ws.ws_bill_customer_sk = c.c_customer_sk", "each web sale to the billed customer."),
        (("web_returns", "customer"), "SELECT 1 FROM web_returns wr JOIN customer c ON wr.wr_refunded_customer_sk = c.c_customer_sk", "each web return to the refunded customer."),
        (("customer", "customer_address"), "SELECT 1 FROM customer c JOIN customer_address ca ON c.c_current_addr_sk = ca.ca_address_sk", "each customer to the current address."),
        (("store_sales", "time_dim"), "SELECT 1 FROM store_sales ss JOIN time_dim t ON ss.ss_sold_time_sk = t.t_time_sk", "each store sale to its time of sale."),
        (("store_sales", "customer_demographics"), "SELECT 1 FROM store_sales ss JOIN customer_demographics cd ON ss.ss_cdemo_sk = cd.cd_demo_sk", "each store sale to its customer's demographics."),
        (("store_returns", "store_sales"), "SELECT 1 FROM store_returns sr JOIN store_sales ss ON sr.sr_item_sk = ss.ss_item_sk AND sr.sr_ticket_number = ss.ss_ticket_number", "each store return to its store sale."),
        (("inventory", "warehouse"), "SELECT 1 FROM inventory i JOIN warehouse w ON i.inv_warehouse_sk = w.w_warehouse_sk", "each inventory count to its warehouse."),
    ],
)
def test_the_role_words(anchors, sql, phrase) -> None:
    assert f"The answer links {phrase}" in told([reply(sql)], *anchors).route


def test_without_a_phrase_it_falls_back_to_the_readable_name_without_surrogate_key() -> None:
    made = document([reply(BILL)], *ADDRESS)
    columns = NAMES["columns"] | {
        "catalog_sales.cs_bill_addr_sk": "invoiced address surrogate key",
        "catalog_sales.cs_ship_addr_sk": "somewhere else surrogate key",
    }
    route = again(made, names=NAMES | {"columns": columns}).route
    # A role the vocabulary does not hold is used as it reads ...
    assert "each catalog sale to the invoiced address." in route
    # ... and a name that is not a role and a thing is used whole.
    assert "Each catalog sale also records its somewhere else;" in route
    assert "surrogate key" not in route


# ---- whether the query followed the plan: one wording for each outcome -----


def test_conforms() -> None:
    assert told([reply(STORE_SQL)], *STORE).sql == "The query followed this plan exactly."
    assert told([reply("SELECT COUNT(*) FROM store")], "store").sql == "The query read the one table and linked nothing."


def test_incomplete_says_how_much_was_used_and_which_tables_were_not_needed() -> None:
    assert told([reply(D4_SQL)], *D4).sql == "The query followed this plan, using 1 of its 3 joins; web site and web page were not needed."
    one = told([reply(STORE_SQL)], "store_sales", "store", "item").sql
    assert one == "The query followed this plan, using 1 of its 2 joins; item was not needed."


def test_incomplete_with_no_join_at_all() -> None:
    sql = told([reply("SELECT COUNT(*) FROM catalog_sales")], *ADDRESS).sql
    assert sql == "The query read only catalog sales and linked nothing; customer address was not needed."


def test_diverged_names_what_the_query_used_and_what_the_plan_chose() -> None:
    sql = told([reply(SHIP)], *ADDRESS).sql
    assert sql == (
        "The query did not follow this plan. It linked each catalog sale to the shipping address, which the plan did "
        "not choose. Planned and not used: the link from each catalog sale to the billing address."
    )


def test_diverged_names_only_the_join_the_plan_did_not_choose() -> None:
    sql = told([reply(STORE_SQL + " JOIN customer_address ca ON ss.ss_addr_sk = ca.ca_address_sk")], *STORE).sql
    assert sql == (
        "The query did not follow this plan. It linked customer address to store sales by matching address with "
        "address, which the plan did not choose."
    )


def test_diverged_by_tables_left_unlinked_says_what_that_does() -> None:
    sql = told([reply("SELECT COUNT(*) FROM store_sales ss, store s")], *STORE).sql
    assert sql == (
        "The query did not follow this plan. It read store sales and store without linking them, so everything in "
        "one is paired with everything in the other. Planned and not used: the link from each store sale to its store."
    )


def test_half_a_key_is_said_and_is_not_a_planned_join_made() -> None:
    half = "SELECT COUNT(*) FROM store_returns sr JOIN store_sales ss ON sr.sr_item_sk = ss.ss_item_sk"
    sql = told([reply(half)], "store_sales", "store_returns").sql
    assert "It made the link from each store return to its store sale on only part of what identifies it." in sql
    unread = document([reply(half + " WHERE ss.ss_store_sk IN (SELECT s.s_store_sk FROM store s)")], "store_sales", "store_returns")
    assert unread.validation.conformance == "not_checked" and "used 0 of its 1 join" in unread.narrative.sql


def test_not_checked_does_not_claim_the_plan_was_followed() -> None:
    script = [reply(STORE_SQL + " WHERE ss.ss_item_sk IN (SELECT i.i_item_sk FROM item i)")]
    made = document(script, "store_sales", "store", "item")
    assert made.validation.conformance == "not_checked"
    assert made.narrative.sql == (
        "Part of the query could not be read with confidence, so it is not confirmed that it followed this plan. "
        "What could be read used 1 of its 2 joins and nothing outside them."
    )


def test_a_retry_is_mentioned() -> None:
    sql = told([reply("SELECT nothing FROM store"), reply(STORE_SQL)], *STORE).sql
    assert sql == "The model was asked 2 times; the earlier reply was not accepted. The query followed this plan exactly."
    three = told([reply("SELECT nothing FROM store")] * 2 + [reply(STORE_SQL)], *STORE).sql
    assert three.startswith("The model was asked 3 times; the earlier replies were not accepted. ")


def test_totals_counted_twice_are_warned_of_only_when_it_happened() -> None:
    pivot = ("store", "store_sales", "store_returns")
    both = told([reply(STORE_SQL + " JOIN store_returns sr ON sr.sr_store_sk = s.s_store_sk")], *pivot).sql
    assert both.endswith(
        "Take care: more than one table is linked through store, so their records are paired with one another and "
        "totals can be counted more than once."
    )
    assert "Take care" not in told([reply(STORE_SQL)], *pivot).sql


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
    assert "limit" not in told([reply(STORE_SQL)], *STORE).result


def test_a_decline_by_the_model_lists_the_tables_it_was_shown() -> None:
    """Item 62."""
    narrative = told([reply(status="not_answerable")], *D4)
    assert narrative.result == (
        "No answer was given. The model was shown 4 tables: customer demographics, web sales, web site and web page. "
        "It said they do not hold the answer. That is a statement about these tables, not about the whole warehouse."
    )
    assert narrative.sql == "No query was written: the model said the tables it was shown do not hold the answer."
    assert told([reply(status="not_answerable")], "store").result == (
        "No answer was given. The model was shown one table: store. It said it does not hold the answer. "
        "That is a statement about this table, not about the whole warehouse."
    )


def test_a_decline_for_want_of_any_table_says_no_model_was_asked() -> None:
    tree = build_tree(GRAPH, (), {})
    narrative = told([], where=Located(tree, explain_tree(tree, GRAPH)))
    assert narrative.found == "No table matched your question closely enough to start from."
    assert narrative.route == "No plan was made."
    assert narrative.not_taken == "No plan was made, so nothing was set aside."
    assert narrative.sql == "No query was written, and no model was asked."
    assert narrative.result == "No answer was given: nothing in the tables retrieved matched your question."


def test_a_decline_for_tables_that_do_not_link_names_them() -> None:
    narrative = told([], "income_band", "ship_mode", max_joins=1)
    assert narrative.found == (
        "Your question matched 2 tables: income band and ship mode. Ship mode could not be linked to the rest within 1 step."
    )
    assert narrative.sql == "No query was written, and no model was asked."
    assert narrative.result == "No answer was given: the tables your question matched could not be linked to one another."


def test_a_refused_write_is_told_plainly() -> None:
    narrative = told([reply("DELETE FROM store")], *STORE)
    assert narrative.sql == (
        "No query was run. What the model wrote would have done something other than read the data. "
        "It was refused, and the model was not asked again."
    )
    assert narrative.result == "No answer was given."


def test_a_query_refused_three_times_is_told_plainly() -> None:
    narrative = told([reply("SELECT nothing FROM store")] * 3, *STORE)
    assert narrative.sql == "No query was run. What the model wrote was refused 3 times: it was not a query this warehouse could run."
    assert narrative.result == "No answer was given."


def test_a_model_failure_is_told_plainly() -> None:
    failed = ModelReply("m", None, None, None, 0, 0, 0, 0.0, 0, "transport", FAILURES["transport"])
    narrative = told([failed], *STORE)
    assert narrative.sql == "No query was written: the model request did not complete."
    assert narrative.result == "No answer was given."


def test_a_query_the_database_stopped_says_it_ran_and_failed() -> None:
    narrative = told([reply(STORE_SQL)], *STORE, executor=Runs(failure="database_error"))
    assert narrative.sql == "The query followed this plan exactly."
    assert narrative.result == "The query was run and the database stopped it: division by zero. No rows came back."


def test_a_query_stopped_at_the_time_limit_and_one_never_run() -> None:
    made = document([reply(STORE_SQL)], *STORE, executor=Runs(failure="timeout"))
    late = made.execution.model_copy(update={"statement_timeout": "30s"})
    assert again(made, execution=late).result == "The query was run and was stopped at the time limit of 30s. No rows came back."
    never = made.execution.model_copy(update={"failure": "not_reachable", "error": "the warehouse could not be reached"})
    assert again(made, execution=never).result == "The query was not run: the warehouse could not be reached."
