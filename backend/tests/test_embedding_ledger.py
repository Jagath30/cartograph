"""Embedding spend on the ledger (item 89).

Every embedding call is written to the same ledger file as model calls,
one line each with `kind: "embedding"`, so that a step's spend is read
from the file and is exact, not bounded. Lines written before kinds
existed carry none and are read as model calls; none is rewritten.
"""

import json

import pytest

from app.shell.embedder import Embedded, EmbeddingFailed, FakeEmbedder
from app.shell.embedding_ledger import LedgeredEmbedder
from app.shell.model_client import FakeModel, LedgeredModel, Message, ModelReply, ReplyShape, SpendLedger
from app.shell.vector_cache import CachedEmbedder

ASK = (Message("user", "x"),)
SHAPE = ReplyShape("s", {"type": "object"})


class _Priced:
    """The fake embedder, charging a fixed price per text."""

    def __init__(self, cost_per_text: float = 0.000001, tokens_per_text: int = 3) -> None:
        self._fake = FakeEmbedder()
        self.model = "fake-embedding"
        self._cost = cost_per_text
        self._tokens = tokens_per_text
        self.asked: list[list[str]] = []

    def embed(self, texts: list[str]) -> Embedded:
        self.asked.append(list(texts))
        vectors = self._fake.embed(texts).vectors
        return Embedded(vectors, self._tokens * len(texts), self._cost * len(texts))


class _Failing:
    model = "fake-embedding"

    def embed(self, texts: list[str]) -> Embedded:
        raise EmbeddingFailed("the embedding provider answered 500 (other)")


def _lines(ledger: SpendLedger) -> list[dict]:
    return [json.loads(line) for line in ledger.path.read_text().splitlines()]


def test_each_embedding_call_is_one_line_of_its_own_kind(tmp_path) -> None:
    ledger = SpendLedger(tmp_path / "deep" / "ledger.jsonl")
    embedder = LedgeredEmbedder(_Priced(), ledger, "step10 test")

    embedder.embed(["one", "two"])

    [line] = _lines(ledger)
    assert line["kind"] == "embedding" and line["purpose"] == "step10 test"
    assert line["model"] == "fake-embedding" and line["inputs"] == 2 and line["tokens"] == 6
    assert line["cost_usd"] == pytest.approx(0.000002) and line["outcome"] == "ok"
    assert line["at"]


def test_what_the_embedder_returned_is_passed_on_unchanged(tmp_path) -> None:
    inner = _Priced()
    got = LedgeredEmbedder(inner, SpendLedger(tmp_path / "l.jsonl"), "p").embed(["a"])
    assert got == Embedded(FakeEmbedder().embed(["a"]).vectors, 3, 0.000001)
    assert inner.asked == [["a"]]


def test_the_total_counts_both_kinds_and_calls_count_each_kind_apart(tmp_path) -> None:
    ledger = SpendLedger(tmp_path / "l.jsonl")
    model = LedgeredModel(FakeModel([ModelReply("m", "m", None, "{}", 10, 0, 5, 0.25, 1)]), ledger, "p", 1.0)
    model.complete(ASK, SHAPE)
    LedgeredEmbedder(_Priced(cost_per_text=0.125), ledger, "p").embed(["a", "b"])

    assert ledger.total() == pytest.approx(0.50)
    assert ledger.calls() == 1
    assert ledger.calls("model") == 1
    assert ledger.calls("embedding") == 1
    assert [line["kind"] for line in _lines(ledger)] == ["model", "embedding"]


def test_lines_written_before_kinds_existed_are_read_as_model_calls(tmp_path) -> None:
    path = tmp_path / "l.jsonl"
    old = {"at": "2026-10-09T10:00:00+00:00", "purpose": "dev run 1", "model": "gpt-6-luna",
           "returned_model": "gpt-6-luna", "tokens_in": 1, "tokens_cached": 0, "tokens_out": 1,
           "cost_usd": 0.5, "outcome": "ok"}  # fmt: skip
    path.write_text(json.dumps(old) + "\n")
    ledger = SpendLedger(path)
    LedgeredEmbedder(_Priced(cost_per_text=0.25), ledger, "p").embed(["a"])

    assert ledger.calls("model") == 1 and ledger.calls("embedding") == 1
    assert ledger.total() == pytest.approx(0.75)
    # Nothing already written is rewritten.
    assert path.read_text().splitlines()[0] == json.dumps(old)


def test_the_ceiling_counts_embedding_spend_too(tmp_path) -> None:
    ledger = SpendLedger(tmp_path / "l.jsonl")
    LedgeredEmbedder(_Priced(cost_per_text=0.5), ledger, "p").embed(["a"])
    fake = FakeModel([])
    stopped = LedgeredModel(fake, ledger, "p", ceiling_usd=0.5).complete(ASK, SHAPE)
    assert stopped.failure == "ceiling" and fake.calls == []


def test_a_failed_call_is_written_at_no_cost_and_still_raised(tmp_path) -> None:
    ledger = SpendLedger(tmp_path / "l.jsonl")
    with pytest.raises(EmbeddingFailed):
        LedgeredEmbedder(_Failing(), ledger, "p").embed(["a", "b"])
    [line] = _lines(ledger)
    assert line["kind"] == "embedding" and line["outcome"] == "failed"
    assert line["cost_usd"] == 0.0 and line["tokens"] == 0 and line["inputs"] == 2


def test_under_the_cache_a_vector_already_paid_for_writes_nothing(tmp_path) -> None:
    ledger = SpendLedger(tmp_path / "l.jsonl")
    inner = _Priced()
    cached = CachedEmbedder(LedgeredEmbedder(inner, ledger, "p"), tmp_path / "cache")

    cached.embed(["a", "b"])
    cached.embed(["a", "b"])
    cached.embed(["a", "c"])

    assert [line["inputs"] for line in _lines(ledger)] == [2, 1]
    assert inner.asked == [["a", "b"], ["c"]]


def test_an_empty_ledger_has_no_calls_of_either_kind(tmp_path) -> None:
    ledger = SpendLedger(tmp_path / "none.jsonl")
    assert ledger.calls() == 0 and ledger.calls("embedding") == 0 and ledger.total() == 0.0


def test_retrieval_writes_its_embeddings_to_the_ledger_under_its_purpose(tmp_path, monkeypatch) -> None:
    """The wiring: what `open_retrieval` pays for reaches the ledger. Needs
    both databases, for the stored snapshot and the live schema; the
    provider is replaced, and so is the ledger's file."""
    import os

    if not (os.environ.get("APP_DATABASE_URL") and os.environ.get("WAREHOUSE_DATABASE_URL")):
        pytest.skip("needs the application database and the warehouse")
    from app.config import get_settings
    from app.shell import retrieval_session
    from app.shell.schema_ingestor import SchemaIngestor

    ledger_path = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(retrieval_session, "LEDGER", ledger_path)
    monkeypatch.setattr(retrieval_session, "make_embedder", lambda settings: _Priced())
    settings = get_settings()
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()

    opened = retrieval_session.open_retrieval(snapshot, 0.5, 0.5, 5, "test", "step10 wiring")
    opened.paid.embed(["a question never asked"])

    [line] = _lines(SpendLedger(ledger_path))
    assert line["kind"] == "embedding" and line["purpose"] == "step10 wiring" and line["inputs"] == 1
    assert opened.paid.texts == 1


def test_the_total_can_be_taken_over_purposes_that_begin_alike(tmp_path) -> None:
    ledger = SpendLedger(tmp_path / "l.jsonl")
    LedgeredEmbedder(_Priced(cost_per_text=0.25), ledger, "step10 run 1").embed(["a"])
    LedgeredEmbedder(_Priced(cost_per_text=0.125), ledger, "step10 pilot").embed(["a"])
    LedgeredEmbedder(_Priced(cost_per_text=0.5), ledger, "api").embed(["a"])
    assert ledger.total("step10") == pytest.approx(0.375)
    assert ledger.total("step10 run") == pytest.approx(0.25)
    assert ledger.total() == pytest.approx(0.875)
    assert ledger.total("nothing") == 0.0
