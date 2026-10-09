"""The orchestrator, with a scripted model and no database (DD-04, DD-13,
DD-15 as amended, NFR-12, NFR-15, FR-42, rule 3 of step 7).

The schema and graph are TPC-DS from the committed files. The tree is
built by the real JoinTree from anchors named in the test; the model is
the scripted fake; the executor is a stand-in that keeps what it was
handed. Every statement the "model" writes here was written by hand.
"""

import json
import logging
from dataclasses import dataclass

import pytest
from core.tpcds_files import OVERLAY, ddl_snapshot

from app.core.explainer import explain_tree
from app.core.graph_builder import build_graph
from app.core.join_tree import build_tree
from app.core.overlay import apply_overlay, parse_overlay
from app.core.sql_validator import ValidatedSql
from app.orchestrator import MAX_RETRIES, Orchestrator, sha256
from app.shell.model_client import FAILURES, FakeModel, ModelReply
from app.shell.query_executor import Execution

SNAPSHOT = apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))
GRAPH = build_graph(SNAPSHOT)
QUESTION = "Which stores sold the most?"

GOOD = "SELECT s.s_store_name, SUM(ss.ss_net_paid) FROM store_sales ss JOIN store s ON ss.ss_store_sk = s.s_store_sk GROUP BY s.s_store_name"
OTHER_KEY = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_ship_addr_sk = ca.ca_address_sk"


@dataclass
class Located:
    tree: object
    explanation: object
    declined: bool = False
    decline_reason: str | None = None
    retrieval: object = None


def located(*anchors: str) -> Located:
    scores = {anchor: 1.0 - position / 10 for position, anchor in enumerate(anchors)}
    tree = build_tree(GRAPH, anchors, scores)
    return Located(tree, explain_tree(tree, GRAPH), tree.decline_reason == "anchors_not_connected", tree.decline_reason)


class Runs:
    """Stands where the executor would and keeps what it was handed."""

    def __init__(self, failure: str | None = None) -> None:
        self.handed: list = []
        self.failure = failure

    def run(self, validated) -> Execution:
        self.handed.append(validated)
        if self.failure:
            return Execution(executed=validated.text, failure=self.failure, sqlstate="22012", message="division by zero")
        return Execution(executed=validated.text, columns=("a",), rows=((1,), (2,)))


def reply(sql: str = "", status: str = "sql") -> str:
    return json.dumps({"status": status, "sql": sql})


def ask(script, *anchors, executor=None, question=QUESTION, retries=MAX_RETRIES):
    model = FakeModel(script)
    executor = executor or Runs()
    where = located(*(anchors or ("store_sales", "store")))
    orchestrator = Orchestrator(SNAPSHOT, (7, "abc123"), lambda asked: where, model, executor, retries)
    return orchestrator.answer(question), model, executor


# --------------------------------------------------------------------------
# The straight road
# --------------------------------------------------------------------------


def test_a_question_is_answered_and_the_trace_holds_every_section() -> None:
    trace, model, executor = ask([reply(GOOD)])

    assert trace.outcome == "answered" and trace.code is None and trace.trace_version == 1
    assert trace.question == QUESTION and trace.schema_ref == (7, "abc123")
    assert trace.located.tree.tables == ("store_sales", "store")
    assert trace.prompt.tables == ("store_sales", "store") and trace.prompt.has_join_section
    assert [attempt.outcome for attempt in trace.attempts] == ["accepted"]
    assert trace.attempts[0].sql == GOOD
    assert (trace.validation.syntax, trace.validation.read_only, trace.validation.references) == ("pass",) * 3
    assert trace.conformance.outcome == "conforms" and trace.diverged is False
    assert trace.execution.rows == ((1,), (2,)) and trace.sql == GOOD
    assert set(trace.timings) == {"locate", "prompt", "generate", "validate", "conform", "execute"}
    assert len(model.calls) == 1


def test_what_the_model_is_sent_is_the_prompt_and_the_reply_shape() -> None:
    trace, model, _ = ask([reply(GOOD)])
    (messages, shape), = model.calls
    assert [message.role for message in messages] == ["system", "user"]
    assert messages[0].content == trace.prompt.system and messages[1].content == trace.prompt.user
    assert messages[1].content.endswith(f"Question: {QUESTION}")
    assert "store_sales.ss_store_sk = store.s_store_sk" in messages[1].content
    assert shape.name == "sql_reply" and set(shape.schema["properties"]) == {"status", "sql"}
    assert trace.attempts[0].messages == messages


def test_the_prompt_holds_the_whole_subgraph_not_only_the_tree() -> None:
    """Ruling 3 of step 7: the tree's tables, then tables on tied
    alternatives within the bound. item and date_dim tie on which fact
    table bridges them."""
    where = located("item", "date_dim")
    assert len(where.tree.subgraph) > len(where.tree.tables)
    trace, _, _ = ask([reply("SELECT i.i_category FROM item i")], "item", "date_dim")
    assert trace.prompt.tables == where.tree.subgraph
    for table in where.tree.subgraph:
        assert f"CREATE TABLE {table} (" in trace.prompt.user


def test_the_sql_that_runs_is_the_very_text_the_model_wrote_and_validation_sealed() -> None:
    """Rule 3. Odd spacing and a trailing semicolon survive to the executor."""
    odd = GOOD.replace("SELECT", "select  ").replace(" FROM", "\n\tFROM") + " ;  "
    trace, _, executor = ask([reply(odd)])

    (handed,) = executor.handed
    assert isinstance(handed, ValidatedSql) and handed is trace.validation.validated
    assert handed.text == odd and trace.execution.executed is handed.text
    assert trace.validated_sha256 == trace.executed_sha256 == sha256(odd)


# --------------------------------------------------------------------------
# Not answerable (FR-42, IR-05)
# --------------------------------------------------------------------------


def test_when_the_model_says_not_answerable_nothing_is_validated_or_run() -> None:
    trace, model, executor = ask([reply(status="not_answerable")])
    assert trace.outcome == "not_answerable" and trace.code == "question_not_answerable"
    assert trace.message.startswith("This cannot be answered from the tables retrieved")
    assert "schema" not in trace.message
    assert [attempt.outcome for attempt in trace.attempts] == ["not_answerable"]
    assert trace.validation is None and trace.execution is None and executor.handed == []
    assert trace.prompt is not None and len(model.calls) == 1


def test_no_anchors_is_not_answerable_without_a_model_call() -> None:
    model, executor = FakeModel([]), Runs()
    nowhere = located()
    assert nowhere.tree.tables == () and nowhere.tree.decline_reason == "no_anchors"
    trace = Orchestrator(SNAPSHOT, (7, "abc"), lambda asked: nowhere, model, executor).answer(QUESTION)

    assert trace.outcome == "not_answerable" and trace.code == "question_not_answerable"
    assert "from the tables retrieved" in trace.message and "no table matched" in trace.message
    assert model.calls == [] and executor.handed == [] and trace.prompt is None and trace.attempts == ()
    assert trace.cost_usd == 0 and set(trace.timings) == {"locate"}


def test_anchors_that_cannot_be_joined_are_not_answerable_without_a_model_call() -> None:
    model, executor = FakeModel([]), Runs()
    apart = located("income_band", "inventory")
    assert apart.declined
    trace = Orchestrator(SNAPSHOT, (7, "abc"), lambda asked: apart, model, executor).answer(QUESTION)
    assert trace.outcome == "not_answerable" and "could not be joined" in trace.message
    assert model.calls == [] and executor.handed == []


# --------------------------------------------------------------------------
# Conformance is flagged, never retried, never a reason not to run (DD-13)
# --------------------------------------------------------------------------


def test_a_divergence_runs_is_flagged_and_is_not_retried() -> None:
    trace, model, executor = ask([reply(OTHER_KEY)], "catalog_sales", "customer_address")

    selected = {frozenset(pair) for edge in trace.selected_edges for pair in edge}
    assert selected == {frozenset({("catalog_sales", "cs_bill_addr_sk"), ("customer_address", "ca_address_sk")})}
    assert trace.outcome == "answered" and trace.conformance.outcome == "diverged" and trace.diverged is True
    assert [(e.left, e.right) for e in trace.actual_edges] == [
        (("catalog_sales", "cs_ship_addr_sk"), ("customer_address", "ca_address_sk"))
    ]
    assert len(model.calls) == 1 and len(executor.handed) == 1 and trace.execution.ok


def test_incomplete_and_not_checked_also_run_without_a_retry() -> None:
    single = "SELECT s.s_store_name FROM store s"
    trace, model, _ = ask([reply(single)])
    assert (trace.outcome, trace.conformance.outcome, trace.diverged, len(model.calls)) == ("answered", "incomplete", False, 1)

    hidden = "SELECT s.s_store_name FROM store s WHERE s.s_store_sk IN (SELECT ss.ss_store_sk FROM store_sales ss)"
    trace, model, _ = ask([reply(hidden)])
    assert (trace.outcome, trace.conformance.outcome, trace.diverged, len(model.calls)) == ("answered", "not_checked", False, 1)


def test_the_actual_edges_are_what_the_executed_sql_joins_not_what_was_selected() -> None:
    through_customer = """SELECT ca.ca_state FROM store_sales ss JOIN customer c ON ss.ss_customer_sk = c.c_customer_sk
                          JOIN customer_address ca ON c.c_current_addr_sk = ca.ca_address_sk"""
    trace, _, _ = ask([reply(through_customer)], "store_sales", "customer_address")
    assert {(e.left[1], e.right[1]) for e in trace.actual_edges} == {
        ("c_customer_sk", "ss_customer_sk"), ("c_current_addr_sk", "ca_address_sk"),
    }  # fmt: skip
    assert trace.diverged is True and trace.attempts[0].tables_outside_prompt == ("customer",)


def test_a_table_the_prompt_did_not_show_is_recorded_on_the_attempt() -> None:
    sql = "SELECT i.i_category FROM store_sales ss JOIN item i ON ss.ss_item_sk = i.i_item_sk"
    trace, _, _ = ask([reply(sql)])
    assert "item" not in trace.prompt.tables
    assert trace.attempts[0].tables_outside_prompt == ("item",) and trace.diverged is True


# --------------------------------------------------------------------------
# Retries (DD-15 as amended, NFR-15)
# --------------------------------------------------------------------------

UNKNOWN_COLUMN = "SELECT s.s_name FROM store s"


def test_a_fixable_fault_is_sent_back_and_the_second_attempt_is_used() -> None:
    trace, model, executor = ask([reply(UNKNOWN_COLUMN), reply(GOOD)])

    assert trace.outcome == "answered"
    assert [attempt.outcome for attempt in trace.attempts] == ["refused_retryable", "accepted"]
    assert trace.attempts[0].sql == UNKNOWN_COLUMN and "s.s_name" in trace.attempts[0].findings[0]
    second, _ = model.calls[1]
    assert [message.role for message in second] == ["system", "user", "assistant", "user"]
    assert second[2].content == reply(UNKNOWN_COLUMN)
    assert second[3].content.startswith("The SQL was not accepted:\n- There is no column s.s_name")
    assert second[3].content.endswith("Reply again in the same JSON form, with a corrected query.")
    assert [handed.text for handed in executor.handed] == [GOOD]
    assert trace.sql == GOOD and trace.validated_sha256 == sha256(GOOD)


def test_retries_are_capped_at_two_and_then_nothing_runs() -> None:
    assert MAX_RETRIES == 2
    trace, model, executor = ask([reply(UNKNOWN_COLUMN)] * 3)
    assert trace.outcome == "validation_failed" and trace.code == "validation_failure"
    assert [attempt.outcome for attempt in trace.attempts] == ["refused_retryable"] * 3
    assert len(model.calls) == 3 and executor.handed == [] and trace.execution is None
    assert trace.conformance is None and trace.diverged is None
    with pytest.raises(ValueError, match="capped at 2"):
        Orchestrator(SNAPSHOT, (1, "x"), lambda q: None, FakeModel([]), Runs(), max_retries=3)


def test_the_cap_counts_every_kind_of_retry_together() -> None:
    trace, model, _ = ask(["not json", reply(UNKNOWN_COLUMN), reply(GOOD)])
    assert [attempt.outcome for attempt in trace.attempts] == ["malformed", "refused_retryable", "accepted"]
    assert trace.outcome == "answered" and len(model.calls) == 3
    trace, model, _ = ask(["not json", reply(UNKNOWN_COLUMN), "not json"])
    assert trace.outcome == "model_failed" and len(model.calls) == 3


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE store",
        "DELETE FROM store",
        "SELECT s.s_store_name FROM store s; DROP TABLE store",
        "WITH gone AS (DELETE FROM store RETURNING *) SELECT * FROM gone",
        "SELECT pg_sleep(60)",
        "SELECT * FROM store FOR UPDATE",
    ],
)
def test_a_write_ends_the_question_with_no_retry_and_nothing_runs(sql) -> None:
    """DD-15: not a slip to be coached out of. A good second reply is
    scripted, and must never be asked for."""
    trace, model, executor = ask([reply(sql), reply(GOOD)])
    assert trace.outcome == "validation_failed" and trace.code == "validation_failure"
    assert [attempt.outcome for attempt in trace.attempts] == ["refused_terminal"]
    assert trace.validation.read_only == "fail" and trace.attempts[0].sql == sql
    assert len(model.calls) == 1 and executor.handed == [] and trace.execution is None
    assert "not asked again" in trace.message


def test_a_malformed_reply_is_retried_and_a_self_contradicting_one_is_malformed() -> None:
    contradiction = json.dumps({"status": "not_answerable", "sql": GOOD})
    trace, model, _ = ask([contradiction, reply(GOOD)])
    assert [attempt.outcome for attempt in trace.attempts] == ["malformed", "accepted"]
    assert "SQL was given all the same" in trace.attempts[0].findings[0]
    assert model.calls[1][0][3].content.startswith("The reply was not accepted: status is not_answerable")


# --------------------------------------------------------------------------
# Model failures (NFR-12)
# --------------------------------------------------------------------------


def failed(kind: str, **known) -> ModelReply:
    fields = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    fields.update(known)
    return ModelReply("m", None, None, None, fields["tokens_in"], 0, fields["tokens_out"], fields["cost_usd"], 5, kind, FAILURES[kind])


def test_a_reply_cut_off_by_the_output_cap_is_its_own_cause_and_is_not_retried() -> None:
    trace, model, executor = ask([failed("cut_off", tokens_in=900, tokens_out=800, cost_usd=0.0005), reply(GOOD)])
    assert trace.outcome == "model_failed" and trace.code == "model_failure"
    assert [attempt.outcome for attempt in trace.attempts] == ["cut_off"]
    assert trace.message == FAILURES["cut_off"] and len(model.calls) == 1 and executor.handed == []
    assert trace.cost_usd == 0.0005 and (trace.tokens_in, trace.tokens_out) == (900, 800)


@pytest.mark.parametrize("kind", ["transport", "provider", "shape", "refused", "ceiling"])
def test_a_provider_or_network_failure_is_a_clear_outcome_and_never_an_answer(kind) -> None:
    trace, model, executor = ask([failed(kind), reply(GOOD)])
    assert trace.outcome == "model_failed" and trace.message == FAILURES[kind]
    assert [attempt.outcome for attempt in trace.attempts] == ["model_failed"]
    assert len(model.calls) == 1 and executor.handed == [] and trace.execution is None and trace.sql is None


# --------------------------------------------------------------------------
# Execution failures, cost, logs
# --------------------------------------------------------------------------


def test_an_execution_error_is_reported_with_everything_before_it_and_is_not_retried() -> None:
    trace, model, executor = ask([reply(GOOD), reply(GOOD)], executor=Runs(failure="database_error"))
    assert trace.outcome == "execution_failed" and trace.code == "execution_failure"
    assert trace.message == "division by zero" and len(model.calls) == 1 and len(executor.handed) == 1
    assert trace.conformance.outcome == "conforms" and trace.validation.passed and trace.prompt is not None


def test_tokens_and_cost_are_summed_over_every_attempt() -> None:
    def paid(text: str) -> ModelReply:
        return ModelReply("m", "m-returned", None, text, 1000, 0, 100, 0.00015, 9)

    trace, _, _ = ask([paid(reply(UNKNOWN_COLUMN)), paid(reply(GOOD))])
    assert (trace.tokens_in, trace.tokens_out) == (2000, 200) and trace.cost_usd == pytest.approx(0.0003)
    assert [attempt.reply.returned_model for attempt in trace.attempts] == ["m-returned", "m-returned"]


def test_every_stage_logs_its_duration_and_outcome(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="cartograph.pipeline"):
        ask([reply(UNKNOWN_COLUMN), reply(OTHER_KEY)], "catalog_sales", "customer_address")
    lines = [json.loads(record.message) for record in caplog.records]
    assert [(line["stage"], line["outcome"]) for line in lines] == [
        ("locate", "located"), ("prompt", "built"), ("generate", "replied"), ("validate", "retryable"),
        ("generate", "replied"), ("validate", "passed"), ("conform", "diverged"), ("execute", "ran"),
    ]  # fmt: skip
    assert all(line["event"] == "stage" and line["duration_ms"] >= 0 for line in lines)
    assert {line["question"] for line in lines} == {sha256(QUESTION)[:12]} and lines[0]["schema"] == "abc123"
