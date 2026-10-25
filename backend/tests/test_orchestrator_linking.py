"""The orchestrator's link stage (step 10b; the owner's rulings at stops A
and B), with scripted models and no database.

With no linker the orchestrator is exactly as it was: tests/test_orchestrator.py
runs unchanged. With one, a linking call names the tables first; an empty
list declines with no generation call; the two models share one budget of
two retries a question (NFR-15); a cut-off reply is not retried; linking's
tokens and cost are the question's.
"""

import pytest
from tracing import GRAPH, SCHEMA_REF, SNAPSHOT, Runs, located, reply

from app.core.linking import LINK_REPLY_NAME, build_link_prompt, link_reply_schema
from app.orchestrator import NOT_ANSWERABLE, NOT_ANSWERABLE_NAMED, Linker, Orchestrator
from app.shell.model_client import FakeModel, ModelReply, ReplyShape

QUESTION = "Which stores sold the most?"
GOOD = "SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk GROUP BY s.s_store_name"
KNOWN = frozenset(table.name for table in SNAPSHOT.tables)


def linked(*tables: str) -> str:
    import json

    return json.dumps({"tables": list(tables)})


def paid(text: str | None, tokens_in: int, tokens_out: int, cost: float, failure: str | None = None) -> ModelReply:
    return ModelReply("m", "m", None, text, tokens_in, 0, tokens_out, cost, 5, failure, failure and "a fixed sentence")


class Linking:
    """A linker over a scripted model; its locate builds the real tree from
    the tables it is handed, and keeps what it was handed."""

    def __init__(self, script) -> None:
        self.model = FakeModel(script)
        self.handed: list[tuple[str, tuple[str, ...]]] = []

    def linker(self) -> Linker:
        def locate(question, tables):
            self.handed.append((question, tables))
            return located(*tables)

        return Linker(
            prompt=lambda question: build_link_prompt(question, SNAPSHOT, GRAPH),
            shape=ReplyShape(LINK_REPLY_NAME, link_reply_schema(SNAPSHOT)),
            known=KNOWN,
            model=self.model,
            locate=locate,
        )


def ask(link_script, generation_script, executor=None):
    linking = Linking(link_script)
    generation = FakeModel(generation_script)

    def never(question):
        raise AssertionError("retrieval's locate was called in linked mode")

    orchestrator = Orchestrator(SNAPSHOT, SCHEMA_REF, never, generation, executor or Runs(), linker=linking.linker())
    return orchestrator.answer(QUESTION), linking, generation


# --------------------------------------------------------------------------
# The straight road
# --------------------------------------------------------------------------


def test_the_linked_tables_are_located_and_the_question_answered() -> None:
    trace, linking, generation = ask([linked("store_sales", "store")], [reply(GOOD)])
    assert trace.outcome == "answered" and trace.code is None
    assert linking.handed == [(QUESTION, ("store_sales", "store"))]
    assert trace.linking.tables == ("store_sales", "store")
    assert [attempt.outcome for attempt in trace.linking.attempts] == ["accepted"]
    assert trace.located.tree.tables == ("store_sales", "store")
    assert trace.conformance.outcome == "conforms"
    assert len(generation.calls) == 1 and "link" in trace.timings


def test_the_linking_call_sends_the_linking_prompt_and_shape() -> None:
    trace, linking, _ = ask([linked("store_sales", "store")], [reply(GOOD)])
    [(messages, shape)] = linking.model.calls
    prompt = build_link_prompt(QUESTION, SNAPSHOT, GRAPH)
    assert [(m.role, m.content) for m in messages] == [("system", prompt.system), ("user", prompt.user)]
    assert shape == ReplyShape(LINK_REPLY_NAME, link_reply_schema(SNAPSHOT))
    assert trace.linking.prompt == prompt


def test_linkings_tokens_and_cost_are_the_questions() -> None:
    linking_reply = paid(linked("store_sales", "store"), 7900, 12, 0.0008)
    generation_reply = paid(reply(GOOD), 2000, 60, 0.0002)
    trace, _, _ = ask([linking_reply], [generation_reply])
    assert (trace.tokens_in, trace.tokens_out) == (9900, 72)
    assert trace.cost_usd == pytest.approx(0.001)
    assert trace.linking.attempts[0].reply is linking_reply


def test_with_no_linker_nothing_of_linking_appears() -> None:
    generation = FakeModel([reply(GOOD)])
    where = located("store_sales", "store")
    trace = Orchestrator(SNAPSHOT, SCHEMA_REF, lambda q: where, generation, Runs()).answer(QUESTION)
    assert trace.linking is None and "link" not in trace.timings and trace.outcome == "answered"


# --------------------------------------------------------------------------
# The decline at linking
# --------------------------------------------------------------------------


def test_no_tables_is_the_decline_at_linking_with_no_generation_call() -> None:
    trace, linking, generation = ask([linked()], [])
    assert trace.outcome == "not_answerable" and trace.code == "question_not_answerable"
    assert trace.message == NOT_ANSWERABLE["linking"]
    assert NOT_ANSWERABLE["linking"] == (
        "This cannot be answered from this schema: shown every table, the model named none that holds the answer."
    )
    assert generation.calls == [] and linking.handed == [] and trace.located is None
    assert [attempt.outcome for attempt in trace.linking.attempts] == ["none"]
    assert trace.linking.tables == ()


def test_linked_tables_that_cannot_be_joined_decline_as_in_v1() -> None:
    linking = Linking([linked("store", "reason")])

    def locate(question, tables):
        where = located(*tables, max_joins=1)
        assert where.declined
        return where

    base = linking.linker()
    linker = Linker(base.prompt, base.shape, base.known, base.model, locate)
    generation = FakeModel([])
    trace = Orchestrator(SNAPSHOT, SCHEMA_REF, None, generation, Runs(), linker=linker).answer(QUESTION)
    assert trace.outcome == "not_answerable" and trace.message == NOT_ANSWERABLE_NAMED["anchors_not_connected"]
    assert generation.calls == []


# --------------------------------------------------------------------------
# Retries: one budget of two a question, shared
# --------------------------------------------------------------------------


def test_a_malformed_linking_reply_is_retried_with_its_fault() -> None:
    trace, linking, _ = ask(['{"tables": ["store_sales", "stores"]}', linked("store_sales", "store")], [reply(GOOD)])
    assert trace.outcome == "answered"
    assert [a.outcome for a in trace.linking.attempts] == ["malformed", "accepted"]
    retried = linking.model.calls[1][0]
    assert [m.role for m in retried] == ["system", "user", "assistant", "user"]
    assert retried[3].content == (
        "The reply was not accepted: it names a table not given: stores.\n\nReply again in the same JSON form."
    )


def test_retries_spent_on_linking_are_not_given_again_to_generation() -> None:
    trace, linking, generation = ask(["no", "no", linked("store_sales", "store")], ["not json"])
    assert trace.outcome == "model_failed"
    assert len(linking.model.calls) == 3 and len(generation.calls) == 1


def test_one_retry_left_after_linking_is_generations_only_retry() -> None:
    trace, _, generation = ask(["no", linked("store_sales", "store")], ["not json", "not json either"])
    assert trace.outcome == "model_failed" and len(generation.calls) == 2

    trace, _, generation = ask(["no", linked("store_sales", "store")], ["not json", reply(GOOD)])
    assert trace.outcome == "answered" and len(generation.calls) == 2


def test_three_malformed_linking_replies_end_the_question() -> None:
    trace, linking, generation = ask(["no", "no", "no"], [])
    assert trace.outcome == "model_failed"
    assert trace.message == "the model's reply was not in the form asked for: the reply is not JSON"
    assert len(linking.model.calls) == 3 and generation.calls == []


def test_a_cut_off_linking_reply_is_not_retried() -> None:
    trace, linking, generation = ask([paid(None, 7900, 400, 0.001, "cut_off")], [])
    assert trace.outcome == "model_failed"
    assert [a.outcome for a in trace.linking.attempts] == ["cut_off"]
    assert len(linking.model.calls) == 1 and generation.calls == []
    assert trace.tokens_out == 400


@pytest.mark.parametrize("failure", ["transport", "provider", "refused", "shape", "ceiling"])
def test_a_failed_linking_call_ends_the_question_and_is_not_retried_here(failure) -> None:
    trace, linking, generation = ask([paid(None, 0, 0, 0.0, failure)], [])
    assert trace.outcome == "model_failed" and trace.message == "a fixed sentence"
    assert [a.outcome for a in trace.linking.attempts] == ["model_failed"]
    assert trace.linking.attempts[0].reply.failure == failure
    assert len(linking.model.calls) == 1 and generation.calls == []


# --------------------------------------------------------------------------
# Not persisted until adopted (ruling 7)
# --------------------------------------------------------------------------


def test_a_linked_answer_is_never_kept() -> None:
    from app.orchestrator import Keep

    linking = Linking([])
    keep = Keep(store=None, context=None, user_id=1)
    orchestrator = Orchestrator(SNAPSHOT, SCHEMA_REF, None, FakeModel([]), Runs(), keep=keep, linker=linking.linker())
    with pytest.raises(RuntimeError, match="not kept"):
        orchestrator.answer_and_keep(QUESTION)
    assert linking.model.calls == []
