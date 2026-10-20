"""The orchestrator's choose stage (step 10b, v2.1; the owner's rulings at
stop A2), with scripted models and no database.

After linking has located the tree: when it holds a choice the alphabet
made, one call is shown the question and those choices, and names for
each the alternative the question means, or none. A named route is
build_tree's `chosen` and the tree is rebuilt; from there nothing changes.
No call when there is no such choice. The three calls share one budget of
two retries a question. A failed choosing call keeps the v2 tree: every
choice left open, the fault recorded, the question goes on (option i).

catalog_sales and customer_address tie at one table between the bill and
the ship address, and nothing else decides: the alphabet takes the bill.
"""

import json

import pytest
from tracing import GRAPH, SCHEMA_REF, SNAPSHOT, Runs, located, reply

from app.core.choosing import CHOOSE_REPLY_NAME, CHOOSE_RULES, build_choose_prompt, choices_of, choose_reply_schema
from app.core.linking import LINK_REPLY_NAME, build_link_prompt, link_reply_schema, link_retry_message
from app.orchestrator import Chooser, Linker, Orchestrator
from app.shell.model_client import FakeModel, ModelReply, ReplyShape

QUESTION = "Where were the catalog orders shipped to?"
SQL = ("SELECT ca.ca_state, COUNT(*) FROM catalog_sales cs JOIN customer_address ca "
       "ON cs.cs_ship_addr_sk = ca.ca_address_sk GROUP BY ca.ca_state")  # fmt: skip
KNOWN = frozenset(table.name for table in SNAPSHOT.tables)
TIED = ("catalog_sales", "customer_address")


def linked(*tables: str) -> str:
    return json.dumps({"tables": list(tables)})


def chose(**answers) -> str:
    return json.dumps({"choices": answers})


def paid(text, tokens_in, tokens_out, cost, failure=None) -> ModelReply:
    return ModelReply("m", "m", None, text, tokens_in, 0, tokens_out, cost, 5, failure, failure and "a fixed sentence")


def _letter(column: str) -> str:
    """The letter the prompt gives the route through `column`."""
    from app.core.choosing import label

    (choice,) = choices_of(located(*TIED).tree, QUESTION)
    return label(next(i for i, p in enumerate(choice.alternatives) if column in p.id))


class Asked:
    def __init__(self, link_script, choose_script, generation_script) -> None:
        self.link = FakeModel(link_script)
        self.choose = FakeModel(choose_script)
        self.generation = FakeModel(generation_script)
        self.relocated: list[tuple[str, tuple[str, ...], frozenset[str]]] = []

    def orchestrator(self, chooser=True) -> Orchestrator:
        def relocate(question, tables, chosen):
            self.relocated.append((question, tables, chosen))
            return located(*tables, chosen=chosen)

        linker = Linker(
            prompt=lambda question: build_link_prompt(question, SNAPSHOT, GRAPH),
            shape=ReplyShape(LINK_REPLY_NAME, link_reply_schema(SNAPSHOT)),
            known=KNOWN,
            model=self.link,
            locate=lambda question, tables: located(*tables),
        )
        choosing = Chooser(
            prompt=lambda question, choices: build_choose_prompt(question, choices, GRAPH),
            model=self.choose,
            relocate=relocate,
        )  # fmt: skip
        return Orchestrator(SNAPSHOT, SCHEMA_REF, None, self.generation, Runs(), linker=linker,
                            chooser=choosing if chooser else None)  # fmt: skip


def ask(link_script, choose_script, generation_script):
    asked = Asked(link_script, choose_script, generation_script)
    return asked.orchestrator().answer(QUESTION), asked


# --------------------------------------------------------------------------
# The straight road
# --------------------------------------------------------------------------


def test_a_named_route_rebuilds_the_tree_and_the_prompt_is_the_rebuilt_trees() -> None:
    ship = _letter("cs_ship_addr_sk")
    trace, asked = ask([linked(*TIED)], [chose(**{"1": ship})], [reply(SQL)])
    assert trace.outcome == "answered", trace.message
    choosing = trace.choosing
    assert choosing.first.tree.attachments[1].rule == "alphabetical"
    assert trace.located.tree.attachments[1].rule == "model_choice"
    assert [j.fk_columns for j in trace.located.tree.joins] == [("cs_ship_addr_sk",)]
    assert asked.relocated == [(QUESTION, TIED, frozenset({trace.located.tree.attachments[1].path.id}))]
    assert choosing.named == (ship,) and [s.outcome for s in choosing.settled] == ["applied"]
    assert choosing.arose == () and choosing.fault is None
    assert "cs_ship_addr_sk = customer_address.ca_address_sk" in trace.prompt.user
    assert trace.conformance.outcome == "conforms"
    assert "choose" in trace.timings


def test_the_choosing_call_sends_the_choosing_prompt_and_its_shape() -> None:
    trace, asked = ask([linked(*TIED)], [chose(**{"1": None})], [reply(SQL)])
    [(messages, shape)] = asked.choose.calls
    choices = choices_of(located(*TIED).tree, QUESTION)
    assert messages[0].content == CHOOSE_RULES
    assert messages[1].content == build_choose_prompt(QUESTION, choices, GRAPH).user
    assert shape == ReplyShape(CHOOSE_REPLY_NAME, choose_reply_schema(choices))
    assert trace.choosing.prompt.system == CHOOSE_RULES


def test_none_named_leaves_the_tree_as_linking_located_it() -> None:
    trace, asked = ask([linked(*TIED)], [chose(**{"1": None})], [reply(SQL)])
    assert asked.relocated == []
    assert trace.located is trace.choosing.first
    assert [s.outcome for s in trace.choosing.settled] == ["left_open"]
    assert "arbitrary_choice" in trace.located.explanation.codes


def test_no_alphabetical_choice_no_call() -> None:
    trace, asked = ask([linked("store_sales", "store")], [], [reply(SQL)])
    assert asked.choose.calls == [] and asked.relocated == []
    assert trace.choosing.choices == () and trace.choosing.attempts == () and trace.choosing.prompt is None
    assert "choose" not in trace.timings


def test_a_decline_at_linking_or_at_locate_makes_no_choosing_call() -> None:
    trace, asked = ask([linked()], [], [])
    assert trace.outcome == "not_answerable" and asked.choose.calls == [] and trace.choosing is None
    asked = Asked([linked("store", "household_demographics")], [], [])

    def apart(question, tables):
        return located(*tables, max_joins=1)

    orchestrator = asked.orchestrator()
    orchestrator._linker = Linker(orchestrator._linker.prompt, orchestrator._linker.shape, KNOWN, asked.link, apart)
    trace = orchestrator.answer(QUESTION)
    assert trace.outcome == "not_answerable" and asked.choose.calls == [] and trace.choosing is None


def test_choosings_tokens_and_cost_are_the_questions() -> None:
    trace, _ = ask(
        [paid(linked(*TIED), 7900, 20, 0.0008)],
        [paid(chose(**{"1": None}), 300, 12, 0.00004)],
        [paid(reply(SQL), 2000, 80, 0.0002)],
    )
    assert trace.tokens_in == 7900 + 300 + 2000 and trace.tokens_out == 20 + 12 + 80
    assert trace.cost_usd == pytest.approx(0.0008 + 0.00004 + 0.0002)
    assert trace.choosing.attempts[0].reply.tokens_in == 300


# --------------------------------------------------------------------------
# Retries, one budget of two a question
# --------------------------------------------------------------------------


def test_a_malformed_choosing_reply_is_retried_with_its_fault() -> None:
    ship = _letter("cs_ship_addr_sk")
    trace, asked = ask([linked(*TIED)], ["{}", chose(**{"1": ship})], [reply(SQL)])
    assert [a.outcome for a in trace.choosing.attempts] == ["malformed", "accepted"]
    assert trace.choosing.attempts[0].fault == "the reply does not have exactly the field choices"
    second = asked.choose.calls[1][0]
    assert second[-1].content == link_retry_message(
        "The reply was not accepted: the reply does not have exactly the field choices."
    )
    assert trace.located.tree.attachments[1].rule == "model_choice"


def test_retries_spent_on_choosing_are_not_given_again_to_generation() -> None:
    trace, asked = ask([linked(*TIED)], ["{}", "{}", chose(**{"1": None})], ["not json", reply(SQL)])
    assert len(asked.choose.calls) == 3
    assert trace.outcome == "model_failed" and len(asked.generation.calls) == 1


def test_choosing_malformed_past_the_budget_keeps_the_v2_tree_and_goes_on() -> None:
    trace, asked = ask([linked(*TIED)], ["{}", "{}", "{}"], [reply(SQL)])
    assert len(asked.choose.calls) == 3
    assert trace.outcome == "answered"
    assert trace.located is trace.choosing.first and asked.relocated == []
    assert trace.choosing.named is None
    assert trace.choosing.fault == "the model's reply was not in the form asked for: the reply does not have exactly the field choices"
    assert [s.outcome for s in trace.choosing.settled] == ["left_open"]


def test_retries_spent_on_linking_leave_choosing_fewer() -> None:
    trace, asked = ask(["{}", linked(*TIED)], ["{}", "{}"], [reply(SQL)])
    assert len(asked.choose.calls) == 2 and trace.choosing.fault is not None
    assert trace.outcome == "answered" and len(asked.generation.calls) == 1


@pytest.mark.parametrize("failure", ["cut_off", "transport", "provider", "refused", "ceiling"])
def test_a_failed_choosing_call_is_not_retried_keeps_the_v2_tree_and_goes_on(failure) -> None:
    trace, asked = ask([linked(*TIED)], [paid(None, 0, 0, 0.0, failure)], [reply(SQL), reply(SQL)])
    assert len(asked.choose.calls) == 1
    assert [a.outcome for a in trace.choosing.attempts] == ["cut_off" if failure == "cut_off" else "model_failed"]
    assert trace.choosing.fault == failure and trace.choosing.named is None
    assert trace.located is trace.choosing.first
    assert trace.outcome == "answered"


# --------------------------------------------------------------------------
# Without a chooser, and the wiring
# --------------------------------------------------------------------------


def test_with_no_chooser_nothing_of_choosing_appears() -> None:
    asked = Asked([linked(*TIED)], [], [reply(SQL)])
    trace = asked.orchestrator(chooser=False).answer(QUESTION)
    assert trace.choosing is None and asked.choose.calls == []
    assert trace.located.tree.attachments[1].rule == "alphabetical"
    assert "choose" not in trace.timings


def test_a_chooser_needs_a_linker() -> None:
    chooser = Chooser(prompt=lambda q, c: None, model=FakeModel([]), relocate=lambda q, t, c: None)
    with pytest.raises(ValueError, match="linker"):
        Orchestrator(SNAPSHOT, SCHEMA_REF, lambda q: located(*TIED), FakeModel([]), Runs(), chooser=chooser)
