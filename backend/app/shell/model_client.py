"""The model that writes SQL, behind one adapter (IR-13, FR-16, NFR-12,
NFR-14).

Impure, and so in shell/ (DD-01). This and the embedding adapter are the
only modules in which the provider's name, address or key appears.
Everything else is handed a `ModelClient` and asks it one thing: these
messages, answered as JSON of this shape.

    OpenAIModel    the real one. One HTTPS POST per call, never retried
                   here: how often to ask again is the orchestrator's
                   (DD-15).
    FakeModel      scripted and offline, for tests and for CI, which
                   never calls a paid API.
    LedgeredModel  wraps either, writes every call to a ledger file and
                   refuses to call once the ledger reaches a ceiling.

A CALL RETURNS AN OUTCOME AND DOES NOT RAISE (DD-04). Whatever happens --
the answer, a refused key, a timeout, a body of the wrong shape, an answer
cut off by the output cap -- comes back as a `ModelReply` with `failure`
set or not, so the orchestrator can put it in the trace. The one thing
raised is ModelKeyMissing, when the adapter is made with no key.

NOTHING FROM A PROVIDER'S ERROR BODY IS REPEATED except its error code,
and that only when it is in the embedder's fixed table; otherwise "other".
OpenAI's message for a refused key quotes part of the key. A failure's
text is one of the fixed sentences in this file and nothing else.

EVERY CALL IS LOGGED with its tokens and cost (NFR-14), one structured
line on the `cartograph.model` logger, failures included. The cost comes
from the dated table in app.config; a model that is not in the table
cannot be called.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import httpx

from app.config import MODEL_PRICES, ModelPrice, Settings
from app.shell.embedder import provider_error_code

log = logging.getLogger("cartograph.model")

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

Failure = Literal["transport", "provider", "shape", "cut_off", "refused", "ceiling"]

# The whole of what a failure ever says. `provider` has the status and the
# code from the fixed table put after it.
FAILURES: dict[str, str] = {
    "transport": "the model request did not complete",
    "provider": "the model provider answered with an error",
    "shape": "the model provider's answer was not in the expected shape",
    "cut_off": "the model's reply was cut off by the output limit",
    "refused": "the model declined to reply",
    "ceiling": "the spending ceiling for model calls has been reached; no call was made",
}


class ModelKeyMissing(RuntimeError):
    """Something needed the SQL model and no key is configured."""


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ReplyShape:
    """The JSON the model must answer with: a name and a JSON Schema. The
    adapter only passes it on; what the fields mean is the core's."""

    name: str
    schema: dict


@dataclass(frozen=True)
class ModelReply:
    requested_model: str
    # What the provider says answered. There is no dated snapshot of the
    # model, so this is the only record of which one it was.
    returned_model: str | None
    fingerprint: str | None
    # The model's text exactly as received, when there is any.
    text: str | None
    tokens_in: int
    tokens_cached: int
    tokens_out: int
    cost_usd: float
    duration_ms: int
    failure: Failure | None = None
    message: str | None = None
    status: int | None = None
    code: str | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


class ModelClient(Protocol):
    model: str

    def complete(self, messages: tuple[Message, ...], shape: ReplyShape) -> ModelReply:
        """One call. Never raises for anything the provider or the network
        does."""


def cost_usd(price: ModelPrice, tokens_in: int, tokens_cached: int, tokens_out: int) -> float:
    fresh = tokens_in - tokens_cached
    return (fresh * price.input + tokens_cached * price.cached_input + tokens_out * price.output) / 1_000_000


class FakeModel:
    """Answers with what it was told to, in order, and keeps what it was
    asked. A script entry is the reply's text, or a whole ModelReply for a
    failure."""

    model = "fake-scripted"

    def __init__(self, script: list[str | ModelReply]) -> None:
        self._script = list(script)
        self.calls: list[tuple[tuple[Message, ...], ReplyShape]] = []

    def complete(self, messages: tuple[Message, ...], shape: ReplyShape) -> ModelReply:
        self.calls.append((messages, shape))
        if not self._script:
            raise AssertionError("FakeModel was asked more often than its script allows")
        entry = self._script.pop(0)
        if isinstance(entry, ModelReply):
            return entry
        return ModelReply(self.model, self.model, None, entry, 0, 0, 0, 0.0, 0)


class OpenAIModel:
    def __init__(
        self,
        api_key: str,
        model: str,
        reasoning_effort: str,
        temperature: float,
        max_output_tokens: int,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ModelKeyMissing(_NO_KEY)
        if model not in MODEL_PRICES:
            raise ValueError(
                f"no price is recorded for the model {model!r} in app.config.MODEL_PRICES, "
                "so its calls could not be costed (NFR-14). Add the price with the date it was read."
            )
        if max_output_tokens <= 0:
            raise ValueError("every model call sets a maximum output length")
        self._key = api_key
        self.model = model
        self._price = MODEL_PRICES[model]
        self._effort = reasoning_effort
        self._temperature = temperature
        self._max_output = max_output_tokens
        self._client = client or httpx.Client(timeout=60)

    def complete(self, messages: tuple[Message, ...], shape: ReplyShape) -> ModelReply:
        began = time.monotonic()
        reply = self._call(messages, shape)
        reply = _timed(reply, began)
        log.info(
            json.dumps(
                {
                    "event": "model_call",
                    "model": reply.requested_model,
                    "returned_model": reply.returned_model,
                    "tokens_in": reply.tokens_in,
                    "tokens_cached": reply.tokens_cached,
                    "tokens_out": reply.tokens_out,
                    "cost_usd": round(reply.cost_usd, 8),
                    "duration_ms": reply.duration_ms,
                    "outcome": reply.failure or "ok",
                    "status": reply.status,
                    "code": reply.code,
                }
            )
        )
        return reply

    def _failed(self, failure: Failure, **known) -> ModelReply:
        message = FAILURES[failure]
        if failure == "provider":
            message = f"{message}: {known['status']} ({known['code']})"
        fields = {"returned_model": None, "fingerprint": None, "text": None,
                  "tokens_in": 0, "tokens_cached": 0, "tokens_out": 0, "cost_usd": 0.0}  # fmt: skip
        fields.update(known)
        return ModelReply(requested_model=self.model, duration_ms=0, failure=failure, message=message, **fields)

    def _call(self, messages: tuple[Message, ...], shape: ReplyShape) -> ModelReply:
        try:
            response = self._client.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "model": self.model,
                    "messages": [asdict(message) for message in messages],
                    "reasoning_effort": self._effort,
                    "temperature": self._temperature,
                    "max_completion_tokens": self._max_output,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": shape.name, "strict": True, "schema": shape.schema},
                    },
                },
            )
        except httpx.HTTPError:
            # Nothing of the exception is kept: its text can carry the request.
            return self._failed("transport")

        if response.status_code != 200:
            return self._failed("provider", status=response.status_code, code=provider_error_code(response))
        try:
            body = response.json()
            usage = body["usage"]
            tokens_in = int(usage["prompt_tokens"])
            tokens_out = int(usage["completion_tokens"])
            tokens_cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            choice = body["choices"][0]
            finish = choice["finish_reason"]
            text = choice["message"].get("content")
            refusal = choice["message"].get("refusal")
            returned = body["model"]
            fingerprint = body.get("system_fingerprint")
            if not isinstance(returned, str) or not (fingerprint is None or isinstance(fingerprint, str)):
                raise TypeError
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            return self._failed("shape", status=200)

        known = {
            "status": 200, "returned_model": returned, "fingerprint": fingerprint,
            "tokens_in": tokens_in, "tokens_cached": tokens_cached, "tokens_out": tokens_out,
            "cost_usd": cost_usd(self._price, tokens_in, tokens_cached, tokens_out),
        }  # fmt: skip
        # A paid-for call that gave nothing usable still cost what it cost.
        if finish == "length":
            return self._failed("cut_off", text=text if isinstance(text, str) else None, **known)
        if refusal or finish == "content_filter":
            return self._failed("refused", **known)
        if not isinstance(text, str) or not text.strip():
            return self._failed("shape", **known)
        return ModelReply(requested_model=self.model, text=text, duration_ms=0, **known)


def _timed(reply: ModelReply, began: float) -> ModelReply:
    fields = asdict(reply)
    fields["duration_ms"] = round((time.monotonic() - began) * 1000)
    return ModelReply(**fields)


class SpendLedger:
    """Every model call a run made, one JSON line each, in a file git
    ignores. It is how a running total survives between commands."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def total(self) -> float:
        if not self.path.exists():
            return 0.0
        return sum(json.loads(line)["cost_usd"] for line in self.path.read_text().splitlines() if line.strip())

    def calls(self) -> int:
        if not self.path.exists():
            return 0
        return sum(1 for line in self.path.read_text().splitlines() if line.strip())

    def record(self, purpose: str, reply: ModelReply) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "purpose": purpose,
            "model": reply.requested_model,
            "returned_model": reply.returned_model,
            "tokens_in": reply.tokens_in,
            "tokens_cached": reply.tokens_cached,
            "tokens_out": reply.tokens_out,
            "cost_usd": reply.cost_usd,
            "outcome": reply.failure or "ok",
        }
        with self.path.open("a") as ledger:
            ledger.write(json.dumps(line) + "\n")


class LedgeredModel:
    def __init__(self, inner: ModelClient, ledger: SpendLedger, purpose: str, ceiling_usd: float) -> None:
        self._inner = inner
        self.model = inner.model
        self._ledger = ledger
        self._purpose = purpose
        self._ceiling = ceiling_usd

    def complete(self, messages: tuple[Message, ...], shape: ReplyShape) -> ModelReply:
        if self._ledger.total() >= self._ceiling:
            return ModelReply(self.model, None, None, None, 0, 0, 0, 0.0, 0, "ceiling", FAILURES["ceiling"])
        reply = self._inner.complete(messages, shape)
        self._ledger.record(self._purpose, reply)
        return reply


_NO_KEY = (
    "OPENAI_API_KEY is not set, and this needs the model that writes SQL. Put the key in .env "
    "(which git ignores) and recreate the backend container: docker compose up -d backend. "
    "Retrieval and path finding do not need it for a question whose vectors are cached."
)


def make_model(settings: Settings) -> OpenAIModel:
    """The real model, or a clear refusal when no key is configured."""
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not key.strip():
        raise ModelKeyMissing(_NO_KEY)
    return OpenAIModel(
        key.strip(),
        settings.sql_model,
        settings.sql_model_reasoning_effort,
        settings.sql_model_temperature,
        settings.sql_model_max_output_tokens,
    )
