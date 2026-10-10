"""The orchestrator writes the trace once (DD-02 rule 1, DD-04, FR-27,
NFR-13; the owner's ruling 5 at step 8).

A scripted model, a stand-in executor and a stand-in store: no database.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

import pytest
from tracing import CONTEXT, Runs, located, orchestrator, reply

from app.core.trace_document import TraceDocument
from app.orchestrator import Keep, TraceNotPersisted
from app.shell.model_client import FAILURES, ModelReply

ADDRESS = ("catalog_sales", "customer_address")
BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
WHEN = datetime(2026, 10, 10, 9, 30, tzinfo=UTC)


class Keeps:
    def __init__(self, refuse: Exception | None = None) -> None:
        self.saved: list[TraceDocument] = []
        self.calls = 0
        self.refuse = refuse

    def save(self, document: TraceDocument) -> None:
        self.calls += 1
        if self.refuse:
            raise self.refuse
        self.saved.append(document)


def keeping(script, store, executor=None, anchors=ADDRESS, **tree_arguments):
    keep = Keep(store, CONTEXT, user_id=42, now=lambda: WHEN, new_id=lambda: ID)
    return orchestrator(script, located(*anchors, **tree_arguments), executor, keep=keep)


def test_the_trace_is_assembled_and_written_once() -> None:
    store = Keeps()
    kept = keeping([reply(BILL)], store).answer_and_keep("Where are buyers billed?")
    assert store.calls == 1 and store.saved == [kept.document]
    assert (kept.document.query_id, kept.document.user_id, kept.document.created_at) == (ID, 42, WHEN)
    assert kept.document.outcome == "answered" and kept.document.question == "Where are buyers billed?"
    assert kept.document.narrative.result == "2 rows came back."
    # The rows are handed back beside the document, and are not in it.
    assert kept.trace.execution.rows == ((0,), (1,))


@pytest.mark.parametrize(
    ("script", "executor", "arguments", "outcome"),
    [
        ([reply(status="not_answerable")], None, {}, "not_answerable"),
        ([], None, {"anchors": ("income_band", "ship_mode"), "max_joins": 1}, "not_answerable"),
        ([reply("DELETE FROM catalog_sales")], None, {}, "validation_failed"),
        ([ModelReply("m", None, None, None, 0, 0, 0, 0.0, 0, "transport", FAILURES["transport"])], None, {}, "model_failed"),
        ([reply(BILL)], Runs(failure="database_error"), {}, "execution_failed"),
    ],
)
def test_every_outcome_is_written_a_failure_as_surely_as_an_answer(script, executor, arguments, outcome) -> None:
    """DD-04: a failure still produces a trace. There is no path through
    the pipeline that stores none."""
    store = Keeps()
    kept = keeping(script, store, executor, **arguments).answer_and_keep("A question?")
    assert kept.document.outcome == outcome and store.saved == [kept.document]


def test_answer_alone_writes_nothing() -> None:
    """The commands of step 7 and the development runs call `answer`, and
    are as they were."""
    store = Keeps()
    trace = keeping([reply(BILL)], store).answer("A question?")
    assert trace.outcome == "answered" and store.calls == 0


def test_an_orchestrator_given_no_store_refuses_to_pretend() -> None:
    with pytest.raises(RuntimeError, match="no store"):
        orchestrator([reply(BILL)], located(*ADDRESS)).answer_and_keep("A question?")


def test_a_refused_write_is_raised_not_swallowed_and_is_not_tried_again(caplog) -> None:
    """Ruling 5: an answer whose trace was lost is not returned; no retry."""
    store = Keeps(refuse=ConnectionError("the database went away"))
    with caplog.at_level(logging.ERROR, logger="cartograph.pipeline"):
        with pytest.raises(TraceNotPersisted) as raised:
            keeping([reply(BILL)], store).answer_and_keep("A question?")
    assert store.calls == 1 and raised.value.query_id == ID


def test_the_log_keeps_the_whole_trace_the_database_refused(caplog) -> None:
    """Ruling 5: the record survives in the log. One line, JSON, holding
    the query id, the outcome, the cost and the full document."""
    store = Keeps(refuse=ConnectionError("the database went away"))
    with caplog.at_level(logging.ERROR, logger="cartograph.pipeline"):
        with pytest.raises(TraceNotPersisted):
            keeping([reply(BILL)], store).answer_and_keep("Where are buyers billed?")
    (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    entry = json.loads(record.getMessage())
    assert entry["event"] == "trace_not_persisted" and entry["query_id"] == str(ID)
    assert entry["outcome"] == "answered" and entry["cost_usd"] == 0.0 and entry["error"] == "ConnectionError"
    logged = TraceDocument.model_validate(entry["trace"])
    assert logged.question == "Where are buyers billed?" and logged.execution.sql == BILL
    assert logged.narrative.result == "2 rows came back."


def test_the_log_entry_for_a_refused_write_holds_no_rows_and_none_of_the_errors_own_words(caplog) -> None:
    """A driver's error text can carry a connection string. Its class is
    logged and its words are not; and the rows are in no trace (DR-15)."""
    store = Keeps(refuse=ConnectionError("postgresql://cartograph:hunter2@postgres:5432/cartograph_app refused"))
    with caplog.at_level(logging.ERROR, logger="cartograph.pipeline"):
        with pytest.raises(TraceNotPersisted) as raised:
            keeping([reply(BILL)], store).answer_and_keep("A question?")
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "hunter2" not in text and "postgresql://" not in text
    assert "hunter2" not in str(raised.value)


def test_a_write_that_succeeds_logs_its_stage_like_any_other(caplog) -> None:
    """NFR-22."""
    with caplog.at_level(logging.INFO, logger="cartograph.pipeline"):
        keeping([reply(BILL)], Keeps()).answer_and_keep("A question?")
    stages = [json.loads(r.getMessage()) for r in caplog.records]
    persisted = [entry for entry in stages if entry.get("stage") == "persist"]
    assert len(persisted) == 1 and persisted[0]["outcome"] == "written" and persisted[0]["query_id"] == str(ID)
    assert isinstance(persisted[0]["duration_ms"], int)
