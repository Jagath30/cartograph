"""The answer cache (FR-35, DR-12, NFR-03, NFR-25, item 116, step 14) and
trace version 4.

What the brief asks of it, each a test below: a different snapshot hash,
preference, model, mode or code misses; a failure is never cached; a hit
holds nothing of the first asker; over the budget a hit is served and a
miss is 503; a hit costs 0 and leaves the day's count alone; versions 1
to 3 read back byte for byte; Redis down, asking still works; and a
cached answer can be run again.
"""

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from api_client import ADA, Ticking, client
from fake_redis import DownRedis, FakeRedis
from memory_stores import Memory
from tracing import CONTEXT, SCHEMA_REF, SNAPSHOT, Runs, located, reply

from app.core import answer_cache as core
from app.core.budget import Allowance
from app.core.trace_document import Context, TraceDocument, preferences_hash
from app.orchestrator import Keep, Orchestrator
from app.shell.accounts import SignedIn
from app.shell.answer_cache import AnswerCache, read_fingerprint
from app.shell.model_client import ModelReply
from app.shell.query_service import QueryService

STORED = Path(__file__).resolve().parent / "stored_traces"
BILL = "SELECT ca.ca_state FROM catalog_sales cs JOIN customer_address ca ON cs.cs_bill_addr_sk = ca.ca_address_sk"
ADDRESS = ("catalog_sales", "customer_address")
QUESTION = "Where are catalogue buyers billed?"
IDENTITY = core.pipeline_identity("chosen", "gpt-6-luna", "none", 0.0, 800, "text-embedding-3-small", "cal", "code")
NOON = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
BOB = SignedIn(2, "bob@example.com")


class Paid:
    """The scripted model, each reply costing what a real one might, and
    counting how often it was asked."""

    model = "fake-scripted"

    def __init__(self, script) -> None:
        self._script = list(script)
        self.calls = 0

    def complete(self, messages, shape) -> ModelReply:
        self.calls += 1
        if not self._script:
            raise AssertionError("the model was asked more often than its script allows")
        return ModelReply(self.model, self.model, None, self._script.pop(0), 900, 0, 60, 0.00012, 400)


class Scripted:
    """The pipeline as the service opens it: the real orchestrator over a
    paid, counted, scripted model."""

    def __init__(self, replies, schema_ref=SCHEMA_REF, context=CONTEXT, executor=None) -> None:
        self.model = Paid(replies)
        self.schema_ref = schema_ref
        self.context = context
        self.executor = executor
        self.traces: Memory | None = None

    @property
    def calls(self) -> int:
        return self.model.calls

    def __call__(self) -> Orchestrator:
        keep = Keep(self.traces, self.context, user_id=1, now=Ticking())
        return Orchestrator(SNAPSHOT, self.schema_ref, lambda asked: located(*ADDRESS), self.model,
                            self.executor or Runs(), keep=keep)  # fmt: skip


def made(replies=None, redis=None, identity=IDENTITY, schema_ref=SCHEMA_REF, context=CONTEXT, allowance=None,
         traces=None, executor=None, rerun=None):  # fmt: skip
    pipeline = Scripted(replies if replies is not None else [reply(BILL)], schema_ref, context, executor)
    traces = traces if traces is not None else Memory()
    pipeline.traces = traces
    cache = AnswerCache(redis if redis is not None else FakeRedis())
    service = QueryService(pipeline, traces, None, rerun, allowance, cache, identity)
    return service, pipeline, traces


def body(asked) -> dict:
    return json.loads(asked.document.model_dump_json())


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------


def key(question=QUESTION, snapshot="abc123", preferences="p1", identity=IDENTITY) -> str:
    return core.cache_key(question, snapshot, preferences, identity)


def test_whitespace_is_trimmed_and_collapsed_and_case_kept() -> None:
    assert core.normalise("  Where are\tcatalogue \n buyers   billed? ") == "Where are catalogue buyers billed?"
    assert key("  Where are   catalogue buyers billed?\n") == key()
    assert key("where are catalogue buyers billed?") != key()


@pytest.mark.parametrize(
    "changed",
    [
        {"snapshot": "def456"},
        {"preferences": "p2"},
        {"identity": IDENTITY | {"sql_model": "gpt-5.6-luna"}},
        {"identity": IDENTITY | {"embedding_model": "text-embedding-3-large"}},
        {"identity": IDENTITY | {"mode": "linked"}},
        {"identity": IDENTITY | {"temperature": 0.5}},
        {"identity": IDENTITY | {"reasoning_effort": "low"}},
        {"identity": IDENTITY | {"max_output_tokens": 801}},
        {"identity": IDENTITY | {"calibration": "other"}},
        {"identity": IDENTITY | {"code": "other"}},
        {"question": "Where are catalogue buyers shipped?"},
    ],
)
def test_change_any_part_and_the_key_changes(changed) -> None:
    assert key(**changed) != key()


def test_the_key_names_no_question_text() -> None:
    assert "catalogue" not in key() and key().startswith("cartograph:answer:")


def test_the_code_fingerprint_is_of_py_files_with_their_paths_sorted() -> None:
    one = core.code_fingerprint([("b.py", b"2"), ("a.py", b"1")])
    assert one == core.code_fingerprint([("a.py", b"1"), ("b.py", b"2")])
    assert one != core.code_fingerprint([("a.py", b"1"), ("b.py", b"3")])
    assert one != core.code_fingerprint([("a.py", b"1"), ("c.py", b"2")])


def test_the_real_fingerprint_reads_the_core_and_logs_its_first_12(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="cartograph.cache"):
        fingerprint = read_fingerprint()
    assert len(fingerprint) == 64
    assert f"code fingerprint {fingerprint[:12]}," in caplog.text and fingerprint not in caplog.text


# --------------------------------------------------------------------------
# A hit, and what misses
# --------------------------------------------------------------------------


def test_asked_twice_the_second_is_a_hit_and_calls_no_model() -> None:
    service, pipeline, traces = made()
    first = service.answer(QUESTION, ADA.user_id, NOON)
    calls = pipeline.calls
    second = service.answer(f"  {QUESTION} ", ADA.user_id, NOON)
    assert calls >= 1 and pipeline.calls == calls
    assert first.document.served_from_cache is None and second.document.served_from_cache is True
    assert second.document.trace_version == 4 and second.document.outcome == "answered"
    assert second.rows == first.rows and second.rows
    assert second.document.query_id != first.document.query_id
    assert len(traces.bodies) == 2


def test_a_hit_costs_nothing_and_says_so_in_its_trace() -> None:
    service, _, _ = made()
    first = service.answer(QUESTION, ADA.user_id, NOON)
    second = service.answer(QUESTION, ADA.user_id, NOON)
    assert first.document.cost_usd > 0 or first.document.tokens_in > 0
    assert (second.document.cost_usd, second.document.tokens_in, second.document.tokens_out) == (0.0, 0, 0)


def test_a_different_snapshot_hash_misses() -> None:
    redis = FakeRedis()
    made(redis=redis)[0].answer(QUESTION, ADA.user_id, NOON)
    other, pipeline, _ = made(redis=redis, schema_ref=(8, "def456"))
    assert other.answer(QUESTION, ADA.user_id, NOON).document.served_from_cache is None and pipeline.calls >= 1


def test_a_different_preference_misses() -> None:
    redis = FakeRedis()
    made(redis=redis)[0].answer(QUESTION, ADA.user_id, NOON)
    preferred = Context(SNAPSHOT, CONTEXT.graph, "another-preferences-hash", CONTEXT.subgraph_bound,
                        CONTEXT.max_joins, CONTEXT.model, CONTEXT.temperature)  # fmt: skip
    assert preferred.preferences_hash != preferences_hash(SNAPSHOT)
    other, pipeline, _ = made(redis=redis, context=preferred)
    assert other.answer(QUESTION, ADA.user_id, NOON).document.served_from_cache is None and pipeline.calls >= 1


@pytest.mark.parametrize("change", [{"sql_model": "gpt-5.6-luna"}, {"mode": "linked"}, {"code": "changed"}])
def test_a_different_model_mode_or_code_misses(change) -> None:
    redis = FakeRedis()
    made(redis=redis)[0].answer(QUESTION, ADA.user_id, NOON)
    other, pipeline, _ = made(redis=redis, identity=IDENTITY | change)
    assert other.answer(QUESTION, ADA.user_id, NOON).document.served_from_cache is None and pipeline.calls >= 1


def test_a_decline_is_cached() -> None:
    service, pipeline, _ = made([reply(status="not_answerable")])
    service.answer(QUESTION, ADA.user_id, NOON)
    again = service.answer(QUESTION, ADA.user_id, NOON)
    assert again.document.outcome == "not_answerable" and again.document.served_from_cache is True
    assert again.rows is None


@pytest.mark.parametrize(
    "replies, executor, outcome",
    [
        ([reply("DROP TABLE store")] * 3, None, "validation_failed"),
        ([reply(status="nonsense")] * 3, None, "model_failed"),
        ([reply(BILL)], Runs(failure="canceling statement due to statement timeout"), "execution_failed"),
    ],
)
def test_a_failure_is_never_cached(replies, executor, outcome) -> None:
    redis = FakeRedis()
    service, _, _ = made(replies + replies, redis=redis, executor=executor)
    first = service.answer(QUESTION, ADA.user_id, NOON)
    assert first.document.outcome == outcome
    assert redis.keys_held() == []
    assert service.answer(QUESTION, ADA.user_id, NOON).document.served_from_cache is None


def test_an_entry_over_256_kb_is_not_cached() -> None:
    service, _, _ = made()
    kept = service.answer(QUESTION, ADA.user_id, NOON)
    assert core.entry(kept.document, kept.rows) is not None
    assert core.entry(kept.document, [["x" * 1000]] * 300) is None


def test_an_entry_lives_24_hours() -> None:
    redis = FakeRedis()
    service, pipeline, _ = made([reply(BILL), reply(BILL)], redis=redis)
    service.answer(QUESTION, ADA.user_id, NOON)
    (held,) = redis.keys_held()
    assert redis.ttl(held) == 24 * 60 * 60
    redis.advance(24 * 60 * 60)
    calls = pipeline.calls
    assert service.answer(QUESTION, ADA.user_id, NOON).document.served_from_cache is None
    assert pipeline.calls > calls


# --------------------------------------------------------------------------
# Nothing of the first asker
# --------------------------------------------------------------------------


def test_a_hit_holds_nothing_of_the_first_asker() -> None:
    redis = FakeRedis()
    service, _, _ = made(redis=redis)
    first = service.answer(QUESTION, BOB.user_id, datetime(2026, 10, 24, 8, 30, tzinfo=UTC))
    (held,) = redis.keys_held()
    raw = redis.get(held).decode()
    # The question itself is in the stored prompt: it is the asker's own
    # question, the same up to whitespace, and names no one.
    for told in (str(first.document.query_id), "2026-10-24", BOB.email, f'"user_id":{BOB.user_id}'):
        assert told not in raw
    assert not {"user_id", "query_id", "created_at", "question"} & set(json.loads(raw)["document"])

    second = service.answer(QUESTION, ADA.user_id, NOON)
    served = body(second)
    assert served["user_id"] == ADA.user_id and served["query_id"] != str(first.document.query_id)
    assert served["created_at"] == NOON.isoformat().replace("+00:00", "Z")
    dumped = json.dumps(served)
    assert str(first.document.query_id) not in dumped and "2026-10-24" not in dumped and BOB.email not in dumped


def test_a_hit_keeps_the_askers_own_wording() -> None:
    service, _, _ = made()
    service.answer(QUESTION, BOB.user_id, NOON)
    asked = f"{QUESTION}   "
    assert service.answer(asked, ADA.user_id, NOON).document.question == asked


# --------------------------------------------------------------------------
# The allowance
# --------------------------------------------------------------------------


def spend(traces: Memory, dollars: float) -> None:
    from tracing import document

    old = document([reply(BILL)], *ADDRESS).model_copy(
        update={"query_id": uuid.uuid4(), "user_id": 99, "created_at": NOON, "cost_usd": dollars}
    )
    traces.save(old)


DEMO = Allowance(monthly_usd=2.00, daily_questions=30, question_bound_usd=0.03)


def test_over_the_budget_a_hit_is_served_and_a_miss_is_503() -> None:
    traces = Memory()
    service, _, _ = made([reply(BILL)], traces=traces, allowance=DEMO)
    service.answer(QUESTION, ADA.user_id, NOON)
    spend(traces, 2.00)
    web = client(service)
    web.app.dependency_overrides[__import__("app.auth", fromlist=["now"]).now] = lambda: NOON
    hit = web.post("/api/v1/queries", json={"question": QUESTION})
    assert hit.status_code == 200 and hit.json()["trace"]["served_from_cache"] is True
    miss = web.post("/api/v1/queries", json={"question": "Which states spend the most?"})
    assert miss.status_code == 503 and miss.json()["code"] == "budget_spent"


def test_a_hit_does_not_count_towards_the_day_and_is_served_past_it() -> None:
    traces = Memory()
    one = Allowance(monthly_usd=2.00, daily_questions=1, question_bound_usd=0.03)
    service, _, _ = made([reply(BILL)], traces=traces, allowance=one)
    service.answer(QUESTION, ADA.user_id, NOON)
    assert traces.asked_since(ADA.user_id, NOON.replace(hour=0)) == 1
    for _ in range(3):
        assert service.answer(QUESTION, ADA.user_id, NOON).document.served_from_cache is True
    assert traces.asked_since(ADA.user_id, NOON.replace(hour=0)) == 1
    assert len(traces.bodies) == 4


def test_a_hit_adds_nothing_to_the_months_spend() -> None:
    traces = Memory()
    service, _, _ = made(traces=traces)
    service.answer(QUESTION, ADA.user_id, NOON)
    spent = traces.spent_since(NOON.replace(day=1))
    service.answer(QUESTION, ADA.user_id, NOON)
    assert traces.spent_since(NOON.replace(day=1)) == spent


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------


def test_the_api_returns_a_hit_with_its_rows_and_it_is_in_history() -> None:
    service, _, _ = made()
    web = client(service)
    first = web.post("/api/v1/queries", json={"question": QUESTION}).json()
    second = web.post("/api/v1/queries", json={"question": QUESTION}).json()
    assert second["trace"]["served_from_cache"] is True and "served_from_cache" not in first["trace"]
    assert second["rows"] == first["rows"] and second["row_count"] == first["row_count"]
    listed = [row["query_id"] for row in web.get("/api/v1/queries").json()["queries"]]
    assert set(listed) == {first["query_id"], second["query_id"]}
    read = web.get(f"/api/v1/queries/{second['query_id']}").json()
    assert read["trace"] == second["trace"] and read["rows"] is None


def test_a_cached_answer_can_be_run_again() -> None:
    again = Runs()
    service, _, _ = made(rerun=again)
    service._snapshots = __import__("api_client").Snapshots()
    web = client(service)
    web.post("/api/v1/queries", json={"question": QUESTION})
    hit = web.post("/api/v1/queries", json={"question": QUESTION}).json()
    rerun = web.post(f"/api/v1/queries/{hit['query_id']}/rerun")
    assert rerun.status_code == 200 and rerun.json()["status"] == "answered"
    assert len(again.handed) == 1


def test_a_local_hit_takes_well_under_500_ms() -> None:
    service, _, _ = made()
    service.answer(QUESTION, ADA.user_id, NOON)
    began = time.monotonic()
    hit = service.answer(QUESTION, ADA.user_id, NOON)
    assert hit.document.served_from_cache is True and time.monotonic() - began < 0.5


# --------------------------------------------------------------------------
# Redis down
# --------------------------------------------------------------------------


def test_redis_down_asking_still_works_and_a_line_is_logged(caplog) -> None:
    service, pipeline, traces = made([reply(BILL), reply(BILL)], redis=DownRedis())
    with caplog.at_level(logging.WARNING, logger="cartograph.cache"):
        first = service.answer(QUESTION, ADA.user_id, NOON)
        second = service.answer(QUESTION, ADA.user_id, NOON)
    assert first.document.outcome == second.document.outcome == "answered"
    assert second.document.served_from_cache is None and len(traces.bodies) == 2
    assert "answer cache not used: Redis did not answer (ConnectionError)" in caplog.text


def test_a_cache_needs_the_pipelines_identity() -> None:
    with pytest.raises(ValueError):
        QueryService(lambda: None, Memory(), None, cache=AnswerCache(FakeRedis()))


# --------------------------------------------------------------------------
# Trace version 4, and versions 1 to 3 as they were
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored, served",
    [("q3_v1_stored.json", "q3_v1_served.json"), ("q3_v2_stored.json", "q3_v2_stored.json"),
     ("q3_v3_stored.json", "q3_v3_stored.json")],
)  # fmt: skip
def test_versions_1_to_3_read_back_byte_for_byte(stored, served) -> None:
    """q3_v3_stored.json was written by the build before version 4."""
    read = TraceDocument.model_validate_json((STORED / stored).read_text())
    assert read.trace_version in (1, 2, 3) and read.served_from_cache is None
    assert read.model_dump_json() == (STORED / served).read_text()


def test_a_computed_version_4_answer_is_a_version_3_document_with_another_number() -> None:
    """The version 3 fixture's own script, through this build: the same
    document but for the number and the wall-clock timings."""
    from tracing import linked_document

    sql = ("SELECT ca.ca_state, COUNT(*) AS customers FROM catalog_sales cs JOIN customer_address ca "
           "ON cs.cs_bill_addr_sk = ca.ca_address_sk GROUP BY ca.ca_state")  # fmt: skip
    made_now = linked_document([json.dumps({"tables": list(ADDRESS)})], [reply(sql)],
                               question="Where are our catalog customers located?",
                               choose_script=[json.dumps({"choices": {"1": None}})])  # fmt: skip
    computed = json.loads(made_now.model_dump_json())
    v3 = json.loads((STORED / "q3_v3_stored.json").read_text())
    assert (computed.pop("trace_version"), v3.pop("trace_version")) == (4, 3)
    for timed in ("timings", "duration_ms"):
        computed.pop(timed), v3.pop(timed)
    assert "served_from_cache" not in computed and computed == v3


def test_a_stored_version_3_answer_goes_through_the_store_and_back_unchanged(scratch_database) -> None:
    from app.shell.trace_store import TraceStore

    store = TraceStore(scratch_database)
    query_id = uuid.uuid4()
    written = TraceDocument.model_validate_json((STORED / "q3_v3_stored.json").read_text())
    store.save(written.model_copy(update={"user_id": store.local_user_id(), "query_id": query_id}))
    expected = json.loads((STORED / "q3_v3_stored.json").read_text())
    expected.update(user_id=store.local_user_id(), query_id=str(query_id))
    assert json.loads(store.load(query_id).model_dump_json()) == expected


def test_the_store_marks_a_hit_and_the_day_does_not_count_it(scratch_database) -> None:
    from app.shell.trace_store import TraceStore

    store = TraceStore(scratch_database)
    user = store.local_user_id()
    service, _, _ = made(traces=store)
    # A miss is kept at Ticking's time, 25 October; the hit at noon that day.
    since = datetime(2026, 10, 25, tzinfo=UTC)
    before = store.asked_since(user, since)
    service.answer(QUESTION, user, NOON)
    hit = service.answer(QUESTION, user, NOON)
    assert hit.document.served_from_cache is True
    assert store.asked_since(user, since) == before + 1
    assert store.load(hit.document.query_id).served_from_cache is True
