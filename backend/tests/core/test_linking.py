"""Step 10b: linking. One model call names the tables a question needs;
from those tables on, v1 runs unchanged (the owner's rulings at stop A).

The prompt and the reply schema are tested on the committed TPC-DS files;
locate_linked on the hand-written fixture with scores typed here, as
test_locate does.
"""

import json

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.full_schema import build_full_prompt
from app.core.graph_builder import build_graph, column_nodes, table_nodes
from app.core.join_tree import ANCHORS_NOT_CONNECTED
from app.core.linking import (
    LINK_MAX_OUTPUT_TOKENS,
    LINK_REPLY_NAME,
    LINK_RULES,
    LinkedLocated,
    build_link_prompt,
    link_reply_schema,
    locate_linked,
    parse_link_reply,
)
from app.core.locate import locate
from app.core.overlay import apply_overlay, parse_overlay
from app.core.retriever import RawScore, Settings, Term
from app.core.snapshot import Table

QUESTION = "How do purchases on the web split by the buyer's level of education?"


@pytest.fixture(scope="module")
def tpcds():
    snapshot = apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))
    return snapshot, build_graph(snapshot)


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_the_linking_rules_are_the_owners_word_for_word() -> None:
    assert LINK_RULES == (
        "You name the tables of a data warehouse that a question needs.\n"
        "\n"
        "You are given the tables as CREATE TABLE statements, then the keys by which they can be joined. "
        "The comment above each table and after each column says what it holds.\n"
        "\n"
        "Rules:\n"
        "- Name every table a query answering the question would read, including a table needed only to join "
        "two others.\n"
        "- Name no other table.\n"
        "- Use only the table names given.\n"
        "- If the question cannot be answered from these tables, do not guess: name none.\n"
        "\n"
        "Reply with one JSON object and nothing else:\n"
        '  {"tables": ["<table name>", ...]}\n'
        "or, when the question cannot be answered from these tables:\n"
        '  {"tables": []}\n'
        "Do not explain."
    )


def test_the_linking_rules_name_no_table(tpcds) -> None:
    """No table's name as a word. The one exception is the phrase "data
    warehouse", which the frozen GENERAL_RULES uses as well: it names the
    kind of database, and the table `warehouse` only shares the word."""
    import re

    snapshot, _ = tpcds
    text = LINK_RULES.replace("a data warehouse", "a data repository")
    for table in snapshot.tables:
        assert not re.search(rf"\b{re.escape(table.name)}\b", text), table.name
    assert LINK_RULES.count("warehouse") == 1 and "a data warehouse" in LINK_RULES


def test_the_user_message_is_the_full_schema_prompts_byte_for_byte(tpcds) -> None:
    snapshot, graph = tpcds
    linking = build_link_prompt(QUESTION, snapshot, graph)
    full = build_full_prompt(QUESTION, snapshot, graph)
    assert linking.user.encode() == full.user.encode()
    assert linking.system == LINK_RULES
    assert linking.tables == full.tables and len(linking.tables) == 24
    assert linking.has_join_section is False


def test_the_question_closes_the_user_message(tpcds) -> None:
    snapshot, graph = tpcds
    assert build_link_prompt("  Which stores?  ", snapshot, graph).user.endswith("\n\nQuestion: Which stores?")


# --------------------------------------------------------------------------
# The reply schema
# --------------------------------------------------------------------------


def test_the_reply_schema_names_every_table_of_the_snapshot_and_nothing_else(tpcds) -> None:
    snapshot, _ = tpcds
    schema = link_reply_schema(snapshot)
    assert LINK_REPLY_NAME == "link_reply"
    assert schema == {
        "type": "object",
        "properties": {
            "tables": {"type": "array", "items": {"type": "string", "enum": sorted(t.name for t in snapshot.tables)}}
        },
        "required": ["tables"],
        "additionalProperties": False,
    }


def test_the_enum_is_built_from_the_snapshot_at_run_time(tpcds) -> None:
    snapshot, _ = tpcds
    from dataclasses import replace

    larger = replace(snapshot, tables=snapshot.tables + (Table("a_new_table"),))
    assert "a_new_table" in link_reply_schema(larger)["properties"]["tables"]["items"]["enum"]
    assert "a_new_table" not in link_reply_schema(snapshot)["properties"]["tables"]["items"]["enum"]


def test_the_output_cap_holds_every_table_named_at_once(tpcds) -> None:
    """At two characters a token, fewer than any English text takes."""
    snapshot, _ = tpcds
    everything = json.dumps({"tables": [t.name for t in snapshot.tables]})
    assert len(everything) / 2 <= LINK_MAX_OUTPUT_TOKENS
    assert LINK_MAX_OUTPUT_TOKENS == 400


# --------------------------------------------------------------------------
# The reply
# --------------------------------------------------------------------------

KNOWN = frozenset({"store", "store_sales", "date_dim"})


def test_a_reply_names_tables_in_its_own_order() -> None:
    got = parse_link_reply('{"tables": ["store_sales", "store"]}', KNOWN)
    assert (got.kind, got.tables, got.fault) == ("tables", ("store_sales", "store"), None)


def test_no_tables_is_the_decline_and_not_a_fault() -> None:
    got = parse_link_reply('{"tables": []}', KNOWN)
    assert (got.kind, got.tables, got.fault) == ("none", (), None)


@pytest.mark.parametrize(
    "text, fault",
    [
        ("tables: store", "the reply is not JSON"),
        ("```json\n{\"tables\": [\"store\"]}\n```", "the reply is not JSON"),
        ('["store"]', "the reply does not have exactly the field tables"),
        ('{"tables": ["store"], "why": "x"}', "the reply does not have exactly the field tables"),
        ("{}", "the reply does not have exactly the field tables"),
        ('{"tables": "store"}', "tables is not a list of table names"),
        ('{"tables": ["store", 3]}', "tables is not a list of table names"),
        ('{"tables": null}', "tables is not a list of table names"),
        ('{"tables": ["store", "stores"]}', "it names a table not given: stores"),
        ('{"tables": ["store", "store"]}', "it names a table twice: store"),
    ],
)
def test_a_reply_not_of_the_form_asked_for_is_malformed_and_says_why(text, fault) -> None:
    got = parse_link_reply(text, KNOWN)
    assert (got.kind, got.tables, got.fault) == ("malformed", (), fault)


# --------------------------------------------------------------------------
# From the linked tables on, v1 unchanged
# --------------------------------------------------------------------------

NAMING = """
naming:
  prefixes: { cs_: catalog sales, ss_: store sales, c_: customer, ca_: customer address, s_: store }
  words:    { sk: surrogate key, addr: address }
"""

SETTINGS = Settings(alpha=1.0, anchor_cut=0.5, anchor_cap=5, margin=0.25)


@pytest.fixture
def graph(small_snapshot):
    return build_graph(apply_overlay(small_snapshot, parse_overlay(NAMING)))


def _scores(graph, **semantic: float) -> tuple[RawScore, ...]:
    scores = [RawScore(table, None, semantic.get(table, 0.3), 0.0) for table in table_nodes(graph)]
    for node in column_nodes(graph):
        table, column = node.split(".")
        scores.append(RawScore(table, column, semantic.get(f"{table}__{column}", 0.3), 0.0))
    return tuple(scores)


def test_the_linked_tables_are_the_anchors_whatever_retrieval_would_have_chosen(graph) -> None:
    scores = _scores(graph, store=1.0, store_sales=0.9, customer__c_customer_sk=0.0)
    assert locate("q", scores, (), graph, SETTINGS).retrieval.anchors == ("store", "store_sales")

    located = locate_linked("q", ("catalog_sales", "customer_address"), scores, (), graph, SETTINGS)
    assert isinstance(located, LinkedLocated)
    assert located.linked == ("catalog_sales", "customer_address")
    assert set(located.tree.anchors) == {"catalog_sales", "customer_address"}
    assert set(located.tables) == {"catalog_sales", "customer_address"}
    # Retrieval is still run and recorded: it gives the scores and the evidence.
    assert located.retrieval.anchors == ("store", "store_sales")


def test_retrievals_scores_order_the_anchors_not_the_order_they_were_named(graph) -> None:
    scores = _scores(graph, store=1.0, store_sales=0.9, customer__c_customer_sk=0.0)
    located = locate_linked("q", ("store_sales", "store"), scores, (), graph, SETTINGS)
    assert located.tree.seed == "store"


def test_the_seed_is_told_as_one_of_the_tables_named(graph) -> None:
    """The owner's wording at stop F2; without linking it reads as before."""
    scores = _scores(graph, store=1.0, store_sales=0.9, customer__c_customer_sk=0.0)
    (seed, *_) = locate_linked("q", ("store_sales", "store"), scores, (), graph, SETTINGS).explanation.attachments
    assert seed.reason.text == "Started from store: of the tables named, the question matched it best."
    (seed, *_) = locate("q", scores, (), graph, SETTINGS).explanation.attachments
    assert seed.reason.text == "Started from store, the table the question matched best."


def test_the_questions_wording_still_breaks_a_tie_between_keys(graph) -> None:
    """As in test_locate: the shipping key scored far above the billing key
    decides the tie, and nothing warns."""
    scores = _scores(
        graph, catalog_sales=1.0, customer_address=0.9,
        catalog_sales__cs_ship_addr_sk=0.8, catalog_sales__cs_bill_addr_sk=0.0,
    )  # fmt: skip
    located = locate_linked("q", ("catalog_sales", "customer_address"), scores, (), graph, SETTINGS)
    [join] = located.tree.joins
    assert join.fk_columns == ("cs_ship_addr_sk",)
    assert located.warning_codes == frozenset()


def test_without_the_wordings_evidence_the_same_tie_is_arbitrary(graph) -> None:
    scores = _scores(graph, catalog_sales=1.0, customer_address=0.9)
    located = locate_linked("q", ("catalog_sales", "customer_address"), scores, (), graph, SETTINGS)
    assert "arbitrary_choice" in located.warning_codes


def test_no_rival_is_set_aside_when_the_tables_were_named(graph) -> None:
    """Rivals are retrieval's way of choosing anchors; linking chose them.
    test_locate's fixture: "address" is best matched in customer_address and
    almost as well in store, so retrieval sets store aside and the tree
    reports it. Named by linking, the same tables report nothing aside."""
    question = _scores(graph, store_sales=1.0, customer_address=0.9, store=0.8, customer__c_customer_sk=0.0)
    address = _scores(graph, customer_address=1.0, store=0.9, customer__c_customer_sk=0.0)
    terms = ((Term("address", "word"), address),)
    retrieved = locate("q", question, terms, graph, SETTINGS)
    assert retrieved.tree.ambiguities, "the fixture must make retrieval set a rival aside"
    located = locate_linked("q", ("store_sales", "customer_address"), question, terms, graph, SETTINGS)
    assert located.tree.ambiguities == ()
    assert located.retrieval.anchor_bound.set_aside_as_rivals == ("store",)


def test_linked_tables_that_cannot_be_joined_are_declined_as_in_v1(graph) -> None:
    scores = _scores(graph, store=1.0, catalog_sales=0.9)
    located = locate_linked("q", ("store", "catalog_sales"), scores, (), graph, SETTINGS, max_joins=1)
    assert located.declined is True and located.decline_reason == ANCHORS_NOT_CONNECTED


def test_no_tables_never_reach_locate_and_an_unknown_one_is_refused(graph) -> None:
    scores = _scores(graph)
    with pytest.raises(ValueError, match="no tables"):
        locate_linked("q", (), scores, (), graph, SETTINGS)
    with pytest.raises(ValueError, match="not in the graph"):
        locate_linked("q", ("store", "warehouse"), scores, (), graph, SETTINGS)


def test_a_route_named_for_the_question_reaches_the_tree(graph) -> None:
    """v2.1: the routes the choosing call named are build_tree's `chosen`."""
    scores = _scores(graph, catalog_sales=1.0, customer_address=0.9)
    plain = locate_linked("q", ("catalog_sales", "customer_address"), scores, (), graph, SETTINGS)
    (ship,) = [p for p in plain.tree.attachments[1].tied if "cs_ship_addr_sk" in p.id]
    located = locate_linked("q", ("catalog_sales", "customer_address"), scores, (), graph, SETTINGS,
                            chosen=frozenset({ship.id}))  # fmt: skip
    assert located.tree.attachments[1].rule == "model_choice" and located.tree.attachments[1].path.id == ship.id
    assert located.warning_codes == frozenset()
    assert located.linked == plain.linked and located.retrieval == plain.retrieval
