"""The embedding model, behind one adapter (IR-14, FR-06, NFR-14).

Impure, and so in shell/ (DD-01). This is the only module in which the
embedding provider's name, address or key appears. Everything else is
handed an `Embedder` and asks it one thing: these texts, as vectors.

    OpenAIEmbedder   the real one. One HTTPS POST per batch.
    FakeEmbedder     deterministic and offline, for tests and for CI,
                     which never calls a paid API.

EVERY CALL IS LOGGED with its token count and cost (NFR-14), as one
structured line on the `cartograph.embedding` logger.

THE KEY NEVER LEAVES THIS MODULE. It is not logged, not put in an
exception, and not echoed from the provider's error text -- which is why a
failure reports the HTTP status and the provider's error code and never
its message: OpenAI's "incorrect API key" message quotes part of the key.

A MISSING KEY IS LOUD AND LOCAL. `make_embedder` raises EmbeddingKeyMissing
with a sentence saying what to set and where. Nothing else in the
application needs the key, so nothing else breaks without it.
"""

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings

log = logging.getLogger("cartograph.embedding")

DIMENSIONS = 1536
OPENAI_URL = "https://api.openai.com/v1/embeddings"
# Well inside the provider's limit of 2,048 inputs per request.
BATCH = 256


class EmbeddingKeyMissing(RuntimeError):
    """Something needed the embedding model and no key is configured."""


class EmbeddingFailed(RuntimeError):
    """The provider did not return usable vectors."""


@dataclass(frozen=True)
class Embedded:
    vectors: tuple[tuple[float, ...], ...]
    tokens: int
    cost_usd: float


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> Embedded:
        """One vector per text, in the order given."""


class FakeEmbedder:
    """Vectors made from the words of the text, with no network and no key.

    Each word is hashed to a few of the 1,536 positions, so two texts that
    share words point in similar directions and two that share none do
    not. That is enough for a test to tell a relevant element from an
    irrelevant one, and it is all this is for: it knows nothing of meaning.
    The same text always gives the same vector.
    """

    model = "fake-bag-of-words"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> Embedded:
        self.calls.append(list(texts))
        tokens = sum(len(_words(text)) for text in texts)
        return Embedded(tuple(self._vector(text) for text in texts), tokens, 0.0)

    def _vector(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * DIMENSIONS
        for word in _words(text):
            digest = hashlib.sha256(word.encode()).digest()
            for offset in range(0, 8, 2):
                vector[int.from_bytes(digest[offset : offset + 2], "big") % DIMENSIONS] += 1.0
        length = math.sqrt(sum(value * value for value in vector))
        if length == 0:
            # Text with no words in it still needs a direction: cosine
            # distance to a zero vector is undefined.
            vector[0], length = 1.0, 1.0
        return tuple(value / length for value in vector)


class MeteredEmbedder:
    """Wraps an Embedder and adds up what passed through it, so that a
    command can report what a whole run cost (NFR-14)."""

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner
        self.model = inner.model
        self.texts = 0
        self.tokens = 0
        self.cost_usd = 0.0
        # Time spent inside the wrapped embedder: for the real one, the
        # external call, which NFR-02 excludes and NFR-01 counts.
        self.seconds = 0.0

    def embed(self, texts: list[str]) -> Embedded:
        began = time.monotonic()
        result = self._inner.embed(texts)
        self.seconds += time.monotonic() - began
        self.texts += len(texts)
        self.tokens += result.tokens
        self.cost_usd += result.cost_usd
        return result


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str,
        price_per_million: float,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise EmbeddingKeyMissing(_NO_KEY)
        self._key = api_key
        self.model = model
        self._price = price_per_million
        self._client = client or httpx.Client(timeout=60)

    def embed(self, texts: list[str]) -> Embedded:
        vectors: list[tuple[float, ...]] = []
        tokens = 0
        for start in range(0, len(texts), BATCH):
            batch = texts[start : start + BATCH]
            began = time.monotonic()
            got, used = self._call(batch)
            cost = used * self._price / 1_000_000
            log.info(
                json.dumps(
                    {
                        "event": "embedding_call",
                        "model": self.model,
                        "inputs": len(batch),
                        "tokens": used,
                        "cost_usd": round(cost, 8),
                        "duration_ms": round((time.monotonic() - began) * 1000),
                    }
                )
            )
            vectors += got
            tokens += used
        return Embedded(tuple(vectors), tokens, tokens * self._price / 1_000_000)

    def _call(self, batch: list[str]) -> tuple[list[tuple[float, ...]], int]:
        try:
            response = self._client.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {self._key}"},
                json={"model": self.model, "input": batch, "encoding_format": "float"},
            )
        except httpx.HTTPError as error:
            # The class only. The exception's text can carry the request.
            raise EmbeddingFailed(f"the embedding request did not complete: {type(error).__name__}") from None

        if response.status_code != 200:
            raise EmbeddingFailed(
                f"the embedding provider answered {response.status_code} ({_error_code(response)})"
            )
        try:
            body = response.json()
            rows = sorted(body["data"], key=lambda row: row["index"])
            vectors = [tuple(float(value) for value in row["embedding"]) for row in rows]
            used = int(body["usage"]["total_tokens"])
        except (ValueError, KeyError, TypeError):
            raise EmbeddingFailed("the embedding provider's answer was not in the expected shape") from None

        if len(vectors) != len(batch):
            raise EmbeddingFailed(f"asked for {len(batch)} vectors and received {len(vectors)}")
        if any(len(vector) != DIMENSIONS for vector in vectors):
            raise EmbeddingFailed(f"a vector did not have {DIMENSIONS} dimensions")
        return vectors, used


_NO_KEY = (
    "OPENAI_API_KEY is not set, and this needs the embedding model. Put the key in .env "
    "(which git ignores) and recreate the backend container: docker compose up -d backend. "
    "Nothing else in Cartograph needs it."
)


def make_embedder(settings: Settings) -> OpenAIEmbedder:
    """The real embedder, or a clear refusal when no key is configured."""
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not key.strip():
        raise EmbeddingKeyMissing(_NO_KEY)
    return OpenAIEmbedder(key.strip(), settings.embedding_model, settings.embedding_price_per_million)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _error_code(response: httpx.Response) -> str:
    """The provider's short error code, never its message."""
    try:
        error = response.json()["error"]
        code = error.get("code") or error.get("type") or "no code"
    except (ValueError, KeyError, TypeError, AttributeError):
        return "no code"
    return code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,60}", code) else "no code"
