"""The embedding adapter (IR-14, FR-06, NFR-09, NFR-14).

Nothing here reaches the network except the one test at the bottom, which
is skipped unless a key is present AND RUN_PAID_TESTS=1. The real adapter
is driven through httpx's MockTransport: the request it would send is
inspected, and the provider's answers are written by hand.
"""

import json
import logging
import math
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.shell.embedder import (
    BATCH,
    DIMENSIONS,
    EmbeddingFailed,
    EmbeddingKeyMissing,
    FakeEmbedder,
    OpenAIEmbedder,
    make_embedder,
)

KEY = "placeholder-secret-for-tests-only"
MODEL = "text-embedding-3-small"


def _settings(**values) -> Settings:
    """Settings that owe nothing to this machine's environment. The key is
    given explicitly, None included: left out, it would be read from the
    container's own OPENAI_API_KEY, and a test of "no key" would be run
    with the real one."""
    values.setdefault("openai_api_key", None)
    return Settings(
        app_database_url="postgresql://x/app", warehouse_database_url="postgresql://x/wh",
        redis_url="redis://x", **values,
    )  # fmt: skip


def _cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _vector(first: float) -> list[float]:
    return [first] + [0.0] * (DIMENSIONS - 1)


def _provider(seen: list, tokens_per_input: int = 3, shuffle: bool = False):
    """A stand-in for the provider: answers each input with a vector whose
    first number is the input's position in its batch."""

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        inputs = json.loads(request.content)["input"]
        data = [{"index": index, "embedding": _vector(float(index))} for index in range(len(inputs))]
        if shuffle:
            data.reverse()
        return httpx.Response(200, json={"data": data, "usage": {"total_tokens": tokens_per_input * len(inputs)}})

    return httpx.Client(transport=httpx.MockTransport(handle))


def _embedder(client) -> OpenAIEmbedder:
    return OpenAIEmbedder(KEY, MODEL, price_per_million=0.02, client=client)


# --------------------------------------------------------------------------
# The fake
# --------------------------------------------------------------------------


def test_the_fake_is_deterministic_and_the_right_shape() -> None:
    fake = FakeEmbedder()
    first = fake.embed(["store sales — net paid", "customer address — state"])
    again = FakeEmbedder().embed(["store sales — net paid", "customer address — state"])

    assert first.vectors == again.vectors
    assert [len(vector) for vector in first.vectors] == [DIMENSIONS, DIMENSIONS]
    assert all(math.isclose(_cosine(vector, vector), 1.0) for vector in first.vectors)
    assert first.cost_usd == 0.0 and first.tokens == 7
    assert fake.calls == [["store sales — net paid", "customer address — state"]]


def test_the_fake_puts_texts_that_share_words_closer_than_texts_that_share_none() -> None:
    (question, related, unrelated) = FakeEmbedder().embed(
        ["net paid by store", "store sales — net paid", "customer address — state"]
    ).vectors
    assert _cosine(question, related) > 0.5 > _cosine(question, unrelated)


def test_the_fake_gives_wordless_text_a_direction_too() -> None:
    (vector,) = FakeEmbedder().embed(["—"]).vectors
    assert math.isclose(_cosine(vector, vector), 1.0)


# --------------------------------------------------------------------------
# The real adapter, against a hand-written provider
# --------------------------------------------------------------------------


def test_one_request_carries_the_model_the_texts_and_the_key_in_its_header_only() -> None:
    seen: list[httpx.Request] = []
    result = _embedder(_provider(seen)).embed(["first", "second"])

    (request,) = seen
    assert str(request.url) == "https://api.openai.com/v1/embeddings"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {"model": MODEL, "input": ["first", "second"], "encoding_format": "float"}
    assert KEY not in request.content.decode() and KEY not in str(request.url)
    assert [vector[0] for vector in result.vectors] == [0.0, 1.0]


def test_vectors_come_back_in_the_order_asked_whatever_order_the_provider_used() -> None:
    result = _embedder(_provider([], shuffle=True)).embed(["a", "b", "c"])
    assert [vector[0] for vector in result.vectors] == [0.0, 1.0, 2.0]


def test_tokens_and_cost_are_counted() -> None:
    result = _embedder(_provider([], tokens_per_input=500)).embed(["a"] * 4)
    assert result.tokens == 2000
    assert result.cost_usd == pytest.approx(2000 * 0.02 / 1_000_000)


def test_many_texts_go_in_batches_and_come_back_as_one_result() -> None:
    seen: list[httpx.Request] = []
    result = _embedder(_provider(seen, tokens_per_input=1)).embed(["t"] * (BATCH + 5))

    assert [len(json.loads(request.content)["input"]) for request in seen] == [BATCH, 5]
    assert len(result.vectors) == BATCH + 5
    assert result.tokens == BATCH + 5


def test_every_call_logs_its_tokens_and_cost_and_never_the_key(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="cartograph.embedding"):
        _embedder(_provider([], tokens_per_input=10)).embed(["t"] * (BATCH + 1))

    entries = [json.loads(record.getMessage()) for record in caplog.records]
    assert [(entry["event"], entry["inputs"], entry["tokens"]) for entry in entries] == [
        ("embedding_call", BATCH, BATCH * 10),
        ("embedding_call", 1, 10),
    ]
    assert entries[0]["cost_usd"] == pytest.approx(BATCH * 10 * 0.02 / 1_000_000)
    assert entries[0]["model"] == MODEL and "duration_ms" in entries[0]
    assert KEY not in caplog.text and "Bearer" not in caplog.text


# --------------------------------------------------------------------------
# Failures: clear, and silent about the key
# --------------------------------------------------------------------------


def _failing(status: int, body) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))


def test_a_refused_key_reports_the_status_and_code_and_not_the_providers_message() -> None:
    """The provider's own message for a bad key quotes part of the key."""
    body = {"error": {"message": f"Incorrect API key provided: {KEY[:12]}...", "code": "invalid_api_key"}}
    with pytest.raises(EmbeddingFailed) as raised:
        _embedder(_failing(401, body)).embed(["a"])

    assert str(raised.value) == "the embedding provider answered 401 (invalid_api_key)"
    assert KEY[:12] not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is None


def test_an_error_code_that_is_not_a_plain_code_is_not_repeated() -> None:
    body = {"error": {"code": f"leaked {KEY}"}}
    with pytest.raises(EmbeddingFailed, match=r"answered 500 \(no code\)"):
        _embedder(_failing(500, body)).embed(["a"])
    with pytest.raises(EmbeddingFailed, match=r"answered 502 \(no code\)"):
        _embedder(_failing(502, "not an object")).embed(["a"])


def test_a_request_that_never_completes_names_the_kind_of_failure_only() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"could not reach host with header Bearer {KEY}")

    with pytest.raises(EmbeddingFailed) as raised:
        _embedder(httpx.Client(transport=httpx.MockTransport(refuse))).embed(["a"])
    assert str(raised.value) == "the embedding request did not complete: ConnectError"
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"data": [], "usage": {"total_tokens": 1}}, "asked for 1 vectors and received 0"),
        ({"data": [{"index": 0, "embedding": [0.1, 0.2]}], "usage": {"total_tokens": 1}}, "did not have 1536"),
        ({"data": [{"index": 0, "embedding": _vector(0.0)}]}, "not in the expected shape"),
        ({"unexpected": True}, "not in the expected shape"),
    ],
)
def test_an_answer_of_the_wrong_shape_is_refused(body, message) -> None:
    with pytest.raises(EmbeddingFailed, match=message):
        _embedder(_failing(200, body)).embed(["a"])


# --------------------------------------------------------------------------
# No key: loud where it is needed, harmless everywhere else (criterion 7)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_a_key_the_embedder_refuses_with_a_sentence_saying_what_to_do(value) -> None:
    settings = _settings(openai_api_key=value)
    with pytest.raises(EmbeddingKeyMissing) as raised:
        make_embedder(settings)
    message = str(raised.value)
    assert "OPENAI_API_KEY is not set" in message and ".env" in message and "docker compose up -d backend" in message


def test_without_a_key_the_application_still_starts_and_answers() -> None:
    client = TestClient(create_app(_settings()))
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_with_a_key_the_real_embedder_is_made_with_the_configured_model() -> None:
    embedder = make_embedder(_settings(openai_api_key=KEY))
    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder.model == "text-embedding-3-small"


def test_printing_the_settings_never_shows_the_key() -> None:
    settings = _settings(openai_api_key=KEY)
    for shown in (repr(settings), str(settings), repr(settings.model_dump()), settings.model_dump_json()):
        assert KEY not in shown


# --------------------------------------------------------------------------
# The one test that costs money
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY") or os.environ.get("RUN_PAID_TESTS") != "1",
    reason="calls the real API: needs OPENAI_API_KEY and RUN_PAID_TESTS=1",
)
def test_paid_the_real_provider_returns_vectors_of_the_stored_dimension() -> None:
    embedder = make_embedder(Settings())
    result = embedder.embed(["store sales — net paid", "customer address — state"])
    assert [len(vector) for vector in result.vectors] == [DIMENSIONS, DIMENSIONS]
    assert result.tokens > 0 and 0 < result.cost_usd < 0.0001
