"""The model adapter (IR-13, FR-16, NFR-09, NFR-12, NFR-14).

Nothing here reaches the network except the one test at the bottom, which
is skipped unless a key is present AND RUN_PAID_TESTS=1. The real adapter
is driven through httpx's MockTransport: the request it would send is
inspected, and the provider's answers are written by hand.
"""

import json
import logging
import os

import httpx
import pytest

from app.config import MODEL_PRICES, ModelPrice, Settings
from app.shell.model_client import (
    FAILURES,
    FakeModel,
    LedgeredModel,
    Message,
    ModelKeyMissing,
    ModelReply,
    OpenAIModel,
    ReplyShape,
    SpendLedger,
    cost_usd,
    make_model,
)

KEY = "placeholder-secret-for-tests-only"
MODEL = "gpt-6-luna"
SHAPE = ReplyShape("probe", {"type": "object", "properties": {"ok": {"type": "boolean"}},
                             "required": ["ok"], "additionalProperties": False})  # fmt: skip
ASK = (Message("system", "Reply in JSON."), Message("user", "Say ok."))


def _settings(**values) -> Settings:
    """As in test_embedder: the key is always given explicitly, so the
    container's real one is never read."""
    values.setdefault("openai_api_key", None)
    return Settings(
        app_database_url="postgresql://x/app", warehouse_database_url="postgresql://x/wh",
        redis_url="redis://x", **values,
    )  # fmt: skip


def _answer(**changes) -> dict:
    body = {
        "model": "gpt-6-luna-as-returned",
        "system_fingerprint": "fp_1",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "prompt_tokens_details": {"cached_tokens": 400}},
    }
    body.update(changes)
    return body


def _client(seen: list, status: int = 200, body=None) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=_answer() if body is None else body)

    return httpx.Client(transport=httpx.MockTransport(handle))


def _model(client, max_output_tokens: int = 800) -> OpenAIModel:
    return OpenAIModel(KEY, MODEL, "none", 0.0, max_output_tokens, client=client)


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------


def test_the_request_carries_the_parameters_the_method_depends_on() -> None:
    seen: list[httpx.Request] = []
    _model(_client(seen)).complete(ASK, SHAPE)

    (request,) = seen
    sent = json.loads(request.content)
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert sent["model"] == MODEL
    assert sent["reasoning_effort"] == "none" and sent["temperature"] == 0.0
    assert sent["max_completion_tokens"] == 800
    assert sent["messages"] == [
        {"role": "system", "content": "Reply in JSON."},
        {"role": "user", "content": "Say ok."},
    ]
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "probe", "strict": True, "schema": SHAPE.schema},
    }
    assert KEY not in request.content.decode() and KEY not in str(request.url)


def test_a_call_cannot_be_made_without_a_maximum_output_length() -> None:
    with pytest.raises(ValueError, match="maximum output length"):
        _model(_client([]), max_output_tokens=0)


def test_a_model_with_no_recorded_price_cannot_be_called() -> None:
    with pytest.raises(ValueError, match="no price is recorded"):
        OpenAIModel(KEY, "a-model-nobody-priced", "none", 0.0, 800, client=_client([]))


# --------------------------------------------------------------------------
# The answer, its tokens and its cost
# --------------------------------------------------------------------------


def test_the_reply_records_the_text_the_model_that_answered_and_the_cost() -> None:
    reply = _model(_client([])).complete(ASK, SHAPE)

    assert reply.ok and reply.failure is None
    assert reply.text == '{"ok": true}'
    assert reply.requested_model == MODEL
    assert reply.returned_model == "gpt-6-luna-as-returned" and reply.fingerprint == "fp_1"
    assert (reply.tokens_in, reply.tokens_cached, reply.tokens_out) == (1000, 400, 200)
    # 600 fresh at $0.10, 400 cached at $0.01, 200 out at $0.50, per million.
    assert reply.cost_usd == pytest.approx((600 * 0.10 + 400 * 0.01 + 200 * 0.50) / 1_000_000)
    assert reply.cost_usd == pytest.approx(0.000164)


def test_cost_is_worked_from_the_three_prices_separately() -> None:
    price = ModelPrice(input=1.0, cached_input=10.0, output=100.0, as_of="2000-01-01")
    assert cost_usd(price, 1_000_000, 0, 0) == pytest.approx(1.0)
    assert cost_usd(price, 1_000_000, 1_000_000, 0) == pytest.approx(10.0)
    assert cost_usd(price, 0, 0, 1_000_000) == pytest.approx(100.0)


def test_every_price_in_the_table_says_when_it_was_read() -> None:
    assert "gpt-6-luna" in MODEL_PRICES
    for price in MODEL_PRICES.values():
        year, month, day = price.as_of.split("-")
        assert len(year) == 4 and 1 <= int(month) <= 12 and 1 <= int(day) <= 31
        assert 0 < price.cached_input < price.input < price.output


def test_an_answer_without_a_fingerprint_or_cached_count_is_still_an_answer() -> None:
    body = _answer(usage={"prompt_tokens": 10, "completion_tokens": 5})
    del body["system_fingerprint"]
    reply = _model(_client([], body=body)).complete(ASK, SHAPE)
    assert reply.ok and reply.fingerprint is None and reply.tokens_cached == 0


def test_every_call_logs_its_tokens_and_cost_and_never_the_key(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="cartograph.model"):
        _model(_client([])).complete(ASK, SHAPE)
        _model(_client([], status=429, body={"error": {"code": "insufficient_quota"}})).complete(ASK, SHAPE)

    first, second = (json.loads(record.message) for record in caplog.records)
    assert first["event"] == "model_call" and first["outcome"] == "ok"
    assert (first["tokens_in"], first["tokens_cached"], first["tokens_out"]) == (1000, 400, 200)
    assert first["cost_usd"] == pytest.approx(0.000164)
    assert first["model"] == MODEL and first["returned_model"] == "gpt-6-luna-as-returned"
    assert second["outcome"] == "provider" and second["status"] == 429 and second["code"] == "insufficient_quota"
    assert KEY not in caplog.text and "Bearer" not in caplog.text


# --------------------------------------------------------------------------
# Failures come back as outcomes, in fixed words (DD-04, NFR-12)
# --------------------------------------------------------------------------


def _everything_said(reply: ModelReply) -> str:
    return json.dumps([reply.message, reply.code, reply.text, reply.returned_model, reply.fingerprint])


def test_a_refused_key_is_an_outcome_that_repeats_nothing_of_the_providers_message() -> None:
    body = {"error": {"message": f"Incorrect API key provided: {KEY[:12]}...", "code": "invalid_api_key"}}
    reply = _model(_client([], status=401, body=body)).complete(ASK, SHAPE)

    assert not reply.ok and reply.failure == "provider"
    assert reply.status == 401 and reply.code == "invalid_api_key"
    assert reply.message == "the model provider answered with an error: 401 (invalid_api_key)"
    assert KEY[:12] not in _everything_said(reply) and "Incorrect" not in _everything_said(reply)
    assert reply.cost_usd == 0.0


def test_a_rate_limit_and_exhausted_credit_are_told_apart() -> None:
    limited = _model(_client([], status=429, body={"error": {"code": "rate_limit_exceeded"}})).complete(ASK, SHAPE)
    spent = _model(_client([], status=429, body={"error": {"type": "insufficient_quota"}})).complete(ASK, SHAPE)
    assert (limited.status, limited.code) == (429, "rate_limit_exceeded")
    assert (spent.status, spent.code) == (429, "insufficient_quota")


@pytest.mark.parametrize("body", [{"error": {"code": f"leaked {KEY}"}}, {"error": {"code": "unlisted_code"}}, "text"])
def test_an_error_code_that_is_not_in_the_table_is_recorded_as_other(body) -> None:
    reply = _model(_client([], status=500, body=body)).complete(ASK, SHAPE)
    assert reply.code == "other"
    assert reply.message == "the model provider answered with an error: 500 (other)"


def test_a_request_that_never_completes_is_an_outcome_with_nothing_of_the_exception() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timed out with header Bearer {KEY}")

    reply = _model(httpx.Client(transport=httpx.MockTransport(refuse))).complete(ASK, SHAPE)
    assert reply.failure == "transport" and reply.message == FAILURES["transport"]
    assert KEY not in _everything_said(reply) and reply.status is None


def test_a_reply_cut_off_by_the_output_limit_is_its_own_cause_and_still_costs() -> None:
    body = _answer(choices=[{"finish_reason": "length", "message": {"content": '{"status": "sql", "sql": "SELECT'}}])
    reply = _model(_client([], body=body)).complete(ASK, SHAPE)

    assert reply.failure == "cut_off" and reply.message == FAILURES["cut_off"]
    assert reply.text == '{"status": "sql", "sql": "SELECT'
    assert reply.tokens_out == 200 and reply.cost_usd == pytest.approx(0.000164)


@pytest.mark.parametrize(
    "choice",
    [
        {"finish_reason": "stop", "message": {"content": None, "refusal": "I cannot help with that."}},
        {"finish_reason": "content_filter", "message": {"content": ""}},
    ],
)
def test_a_refusal_is_an_outcome_and_its_text_is_not_kept(choice) -> None:
    reply = _model(_client([], body=_answer(choices=[choice]))).complete(ASK, SHAPE)
    assert reply.failure == "refused" and reply.text is None
    assert "cannot help" not in _everything_said(reply)
    assert reply.cost_usd > 0


@pytest.mark.parametrize(
    "body",
    [
        {"unexpected": True},
        _answer(choices=[]),
        _answer(usage={"completion_tokens": 1}),
        _answer(model=None),
        _answer(choices=[{"finish_reason": "stop", "message": {"content": "   "}}]),
        _answer(choices=[{"finish_reason": "stop", "message": {"content": None}}]),
        "not an object",
    ],
)
def test_an_answer_of_the_wrong_shape_is_an_outcome(body) -> None:
    reply = _model(_client([], body=body)).complete(ASK, SHAPE)
    assert reply.failure == "shape" and reply.message == FAILURES["shape"] and reply.text is None


# --------------------------------------------------------------------------
# No key
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_a_key_the_model_refuses_with_a_sentence_saying_what_to_do(value) -> None:
    with pytest.raises(ModelKeyMissing) as raised:
        make_model(_settings(openai_api_key=value))
    message = str(raised.value)
    assert "OPENAI_API_KEY is not set" in message and ".env" in message and "docker compose up -d backend" in message


def test_with_a_key_the_real_model_is_made_from_the_settings_and_is_not_called() -> None:
    model = make_model(_settings(openai_api_key=KEY))
    assert isinstance(model, OpenAIModel) and model.model == "gpt-6-luna"


# --------------------------------------------------------------------------
# The fake, and the ledger
# --------------------------------------------------------------------------


def test_the_fake_answers_from_its_script_in_order_and_keeps_what_it_was_asked() -> None:
    cut = ModelReply("fake-scripted", None, None, None, 0, 0, 0, 0.0, 0, "cut_off", FAILURES["cut_off"])
    fake = FakeModel(["first", cut])

    assert fake.complete(ASK, SHAPE).text == "first"
    assert fake.complete(ASK, SHAPE).failure == "cut_off"
    assert [call[0] for call in fake.calls] == [ASK, ASK]
    with pytest.raises(AssertionError, match="more often"):
        fake.complete(ASK, SHAPE)


def test_the_ledger_adds_up_every_call_and_the_ceiling_stops_the_next_one(tmp_path) -> None:
    paid = ModelReply("m", "m", None, "{}", 10, 0, 5, 0.30, 12)
    fake = FakeModel([paid, paid, paid])
    ledger = SpendLedger(tmp_path / "deep" / "ledger.jsonl")
    model = LedgeredModel(fake, ledger, "test", ceiling_usd=0.50)

    assert ledger.total() == 0.0 and ledger.calls() == 0
    assert model.complete(ASK, SHAPE).ok
    assert model.complete(ASK, SHAPE).ok
    assert ledger.total() == pytest.approx(0.60) and ledger.calls() == 2

    stopped = model.complete(ASK, SHAPE)
    assert stopped.failure == "ceiling" and stopped.cost_usd == 0.0
    assert len(fake.calls) == 2 and ledger.calls() == 2
    lines = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert [line["purpose"] for line in lines] == ["test", "test"]
    assert lines[0]["tokens_in"] == 10 and lines[0]["outcome"] == "ok"


# --------------------------------------------------------------------------
# The one test that costs money
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY") or os.environ.get("RUN_PAID_TESTS") != "1",
    reason="calls the real API: needs OPENAI_API_KEY and RUN_PAID_TESTS=1",
)
def test_paid_the_real_model_accepts_the_parameters_and_answers_in_the_shape() -> None:
    reply = make_model(Settings()).complete(ASK, SHAPE)
    assert reply.ok, reply.message
    assert json.loads(reply.text) == {"ok": True}
    assert reply.returned_model and 0 < reply.cost_usd < 0.001
