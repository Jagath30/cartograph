"""Step 10b, v2.1: choosing. One model call, shown the question and the
choices the alphabet made in the tree, names for each the alternative the
question means, or none (the owner's rulings at stop A2).

Trees are built by the real JoinTree, on the hand-written fixture and on
the committed TPC-DS files. No score came from an embedding.
"""

import hashlib
import json
import re

import pytest
from tpcds_files import OVERLAY, ddl_snapshot

from app.core.choosing import (
    CHOOSE_MAX_OUTPUT_TOKENS,
    CHOOSE_REPLY_NAME,
    CHOOSE_RULES,
    build_choose_prompt,
    choices_of,
    choose_reply_schema,
    label,
    named_routes,
    parse_choose_reply,
    settle,
)
from app.core.explainer import describe_path
from app.core.graph_builder import build_graph
from app.core.join_tree import build_tree
from app.core.overlay import apply_overlay, parse_overlay

NAMING = """
naming:
  prefixes: { cs_: catalog sales, ss_: store sales, c_: customer, ca_: customer address, s_: store, ws_: web sales }
  words:    { sk: surrogate key, addr: address }
"""
QUESTION = "Where were the catalog orders sent?"


@pytest.fixture
def graph(small_snapshot):
    return build_graph(apply_overlay(small_snapshot, parse_overlay(NAMING)))


@pytest.fixture(scope="module")
def tpcds():
    snapshot = apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))
    return snapshot, build_graph(snapshot)


def _tree(graph, *anchors: str, **options):
    return build_tree(graph, anchors, {a: 1.0 - i / 10 for i, a in enumerate(anchors)}, **options)


def _order(question: str, paths) -> list[str]:
    """The ruled order, computed here on its own."""
    return [p.id for p in sorted(paths, key=lambda p: hashlib.sha256(f"{question}\n{p.id}".encode()).hexdigest())]


# --------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------


def test_the_choosing_rules_are_the_owners_word_for_word() -> None:
    assert CHOOSE_RULES == (
        "You choose how the tables of a data warehouse are joined to answer a question.\n"
        "\n"
        "The tables a query needs have been chosen. Between some of them there is more than one way to join. "
        "For each choice below you are given its alternatives, each as a join condition and a sentence saying what "
        "it joins.\n"
        "\n"
        "Rules:\n"
        "- For each choice, name the alternative the question means.\n"
        "- If the question does not say which is meant, name none for that choice. Do not guess.\n"
        "- Use only the labels given.\n"
        "\n"
        "Reply with one JSON object and nothing else:\n"
        '  {"choices": {"<choice number>": "<alternative letter>" or null, ...}}\n'
        "with every choice number once. Do not explain."
    )


def test_the_choosing_rules_name_no_table(tpcds) -> None:
    snapshot, _ = tpcds
    text = CHOOSE_RULES.replace("a data warehouse", "a data repository")
    for table in snapshot.tables:
        assert not re.search(rf"\b{re.escape(table.name)}\b", text), table.name
    assert CHOOSE_RULES.count("warehouse") == 1


# --------------------------------------------------------------------------
# The choices
# --------------------------------------------------------------------------


def test_a_tree_with_no_alphabetical_choice_has_no_choices(graph) -> None:
    evidence = {"catalog_sales.cs_bill_addr_sk": 0.25, "catalog_sales.cs_ship_addr_sk": 0.75}
    tree = _tree(graph, "catalog_sales", "customer_address", evidence=evidence, margin=0.25)
    assert choices_of(tree, QUESTION) == ()
    assert choices_of(_tree(graph, "store"), QUESTION) == ()


def test_each_attachment_the_alphabet_decided_is_one_choice_of_either_kind(tpcds) -> None:
    """web_sales, customer and date_dim: customer ties at web_sales between
    two keys; date_dim ties between customer and web_sales."""
    _, graph = tpcds
    tree = _tree(graph, "web_sales", "customer", "date_dim")
    choices = choices_of(tree, QUESTION)
    assert [(c.number, c.anchor, c.attached_to) for c in choices] == [(1, "customer", "web_sales"), (2, "date_dim", "customer")]
    for choice, attachment in zip(choices, tree.attachments[1:]):
        assert {p.id for p in choice.alternatives} == {p.id for p in attachment.tied}
        assert choice.alphabet == attachment.path.id
    # What a declared preference withdrew is never offered.
    withdrawn = {p.id for p in tree.attachments[2].withdrawn}
    assert withdrawn and not withdrawn & {p.id for p in choices[1].alternatives}


def test_the_alternatives_are_listed_by_the_hash_of_the_question_and_the_route(tpcds) -> None:
    _, graph = tpcds
    tree = _tree(graph, "web_sales", "customer", "date_dim")
    for question in (QUESTION, "Which web sites sold most in 2000?", "Who bought on the web?"):
        for choice, attachment in zip(choices_of(tree, question), tree.attachments[1:]):
            assert [p.id for p in choice.alternatives] == _order(question, attachment.tied)


def test_the_alphabets_pick_is_not_always_listed_first() -> None:
    """Over many questions the first-listed alternative is the alphabet's in
    some and not in others: the order owes nothing to the alphabet."""
    snapshot = apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))
    graph = build_graph(snapshot)
    tree = _tree(graph, "web_sales", "customer", "date_dim")
    firsts = {choices_of(tree, f"Question {n}?")[1].alternatives[0].id == tree.attachments[2].path.id for n in range(40)}
    assert firsts == {True, False}


def test_labels_run_a_to_z_and_on() -> None:
    assert [label(i) for i in (0, 1, 25, 26, 27)] == ["A", "B", "Z", "AA", "AB"]


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_the_prompt_is_the_rules_then_the_question_then_each_choice(graph) -> None:
    tree = _tree(graph, "catalog_sales", "customer_address")
    (choice,) = choices_of(tree, QUESTION)
    prompt = build_choose_prompt(QUESTION, (choice,), graph)
    assert prompt.system == CHOOSE_RULES
    lines = []
    for letter, path in zip("AB", choice.alternatives):
        (join,) = path.joins
        condition = f"{join.fk_table}.{join.fk_columns[0]} = {join.pk_table}.{join.pk_columns[0]}"
        lines.append(f"  {letter}. {condition} — {describe_path(path, graph).description}")
    assert prompt.user == (
        f"Question: {QUESTION}\n\nChoice 1: customer_address can be joined in these ways:\n" + "\n".join(lines)
    )
    assert "cs_bill_addr_sk" in prompt.user and "cs_ship_addr_sk" in prompt.user
    assert not prompt.has_join_section


def test_a_route_of_several_joins_is_its_conditions_in_order(tpcds) -> None:
    _, graph = tpcds
    tree = _tree(graph, "item", "date_dim")
    (choice,) = choices_of(tree, QUESTION)
    prompt = build_choose_prompt(QUESTION, (choice,), graph)
    first = choice.alternatives[0]
    conditions = "; then ".join(
        " AND ".join(f"{j.fk_table}.{a} = {j.pk_table}.{b}" for a, b in zip(j.fk_columns, j.pk_columns))
        for j in first.joins
    )
    assert f"  A. {conditions} — {describe_path(first, graph).description}" in prompt.user


def test_several_choices_are_numbered_in_the_trees_order(tpcds) -> None:
    _, graph = tpcds
    tree = _tree(graph, "web_sales", "customer", "date_dim")
    prompt = build_choose_prompt(QUESTION, choices_of(tree, QUESTION), graph)
    assert prompt.user.index("Choice 1: customer can be") < prompt.user.index("Choice 2: date_dim can be")


# --------------------------------------------------------------------------
# The reply
# --------------------------------------------------------------------------


def test_the_reply_schema_has_each_choice_once_with_its_own_letters_or_null(tpcds) -> None:
    _, graph = tpcds
    choices = choices_of(_tree(graph, "web_sales", "customer", "date_dim"), QUESTION)
    schema = choose_reply_schema(choices)
    assert CHOOSE_REPLY_NAME == "choose_reply"
    assert schema["required"] == ["choices"] and schema["additionalProperties"] is False
    inner = schema["properties"]["choices"]
    assert inner["required"] == ["1", "2"] and inner["additionalProperties"] is False
    for choice in choices:
        entry = inner["properties"][str(choice.number)]
        assert entry["type"] == ["string", "null"]
        assert entry["enum"] == [*(label(i) for i in range(len(choice.alternatives))), None]


def test_the_output_cap_holds_twenty_choices_at_once() -> None:
    reply = json.dumps({"choices": {str(n): "AB" for n in range(1, 21)}})
    assert len(reply) / 2 <= CHOOSE_MAX_OUTPUT_TOKENS
    assert CHOOSE_MAX_OUTPUT_TOKENS == 400


def _two(tpcds):
    _, graph = tpcds
    return choices_of(_tree(graph, "web_sales", "customer", "date_dim"), QUESTION)


def test_a_reply_names_a_letter_or_none_for_each_choice(tpcds) -> None:
    choices = _two(tpcds)
    read = parse_choose_reply('{"choices": {"1": null, "2": "B"}}', choices)
    assert (read.kind, read.named, read.fault) == ("named", (None, "B"), None)
    assert named_routes(choices, read.named) == frozenset({choices[1].alternatives[1].id})
    assert named_routes(choices, (None, None)) == frozenset()


@pytest.mark.parametrize(
    "text, fault",
    [
        ("not json", "the reply is not JSON"),
        ('{"choice": {}}', "the reply does not have exactly the field choices"),
        ('{"choices": {"1": null, "2": null}, "x": 1}', "the reply does not have exactly the field choices"),
        ('{"choices": ["A", "B"]}', "choices is not an object"),
        ('{"choices": {"1": null}}', "it does not answer every choice exactly once"),
        ('{"choices": {"1": null, "2": null, "3": null}}', "it does not answer every choice exactly once"),
        ('{"choices": {"1": "Z", "2": null}}', "it names an alternative choice 1 does not have: Z"),
        ('{"choices": {"1": 1, "2": null}}', "it names an alternative choice 1 does not have: 1"),
    ],
)
def test_a_reply_not_of_the_form_asked_for_is_malformed_and_says_why(tpcds, text, fault) -> None:
    read = parse_choose_reply(text, _two(tpcds))
    assert (read.kind, read.fault) == ("malformed", fault)


# --------------------------------------------------------------------------
# After the rebuild
# --------------------------------------------------------------------------


def test_each_choice_is_settled_against_the_rebuilt_tree(tpcds) -> None:
    _, graph = tpcds
    first = _tree(graph, "web_sales", "customer", "date_dim")
    choices = choices_of(first, QUESTION)
    sold = next(i for i, p in enumerate(choices[1].alternatives) if "ws_sold_date_sk" in p.id)
    named = (None, label(sold))
    rebuilt = _tree(graph, "web_sales", "customer", "date_dim", chosen=named_routes(choices, named))
    settled, arose = settle(choices, named, rebuilt)
    assert [(s.number, s.outcome) for s in settled] == [(1, "left_open"), (2, "applied")]
    assert settled[1].route == choices[1].alternatives[sold].id and settled[0].route is None
    assert arose == ()


def test_a_named_route_the_rebuilt_tree_never_meets_is_not_met(tpcds) -> None:
    _, graph = tpcds
    first = _tree(graph, "web_sales", "customer", "date_dim")
    choices = choices_of(first, QUESTION)
    # Rebuilt as if nothing had been named: the route is never met.
    settled, _ = settle(choices, ("A", None), first)
    assert [s.outcome for s in settled] == ["not_met", "left_open"]


def test_an_alphabetical_choice_that_arises_after_the_rebuild_is_recorded(tpcds) -> None:
    _, graph = tpcds
    first = _tree(graph, "web_sales", "customer", "date_dim")
    only_one = choices_of(first, QUESTION)[:1]
    _, arose = settle(only_one, (None,), first)
    assert [(a.anchor, a.attached_to) for a in arose] == [("date_dim", "customer")]


def test_a_key_of_two_columns_is_one_condition_joined_by_and(tpcds) -> None:
    from app.core.choosing import OpenChoice
    from app.core.path_finder import find_paths

    _, graph = tpcds
    path = find_paths(graph, "store_returns", "store_sales").selected
    prompt = build_choose_prompt(QUESTION, (OpenChoice(1, "store_returns", "store_sales", (path,), path.id),), graph)
    assert (
        "  A. store_returns.sr_item_sk = store_sales.ss_item_sk AND "
        "store_returns.sr_ticket_number = store_sales.ss_ticket_number — "
    ) in prompt.user
