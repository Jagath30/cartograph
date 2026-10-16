"""LedgeredEmbedder: every embedding call on the spend ledger (item 89).

Wraps the real embedder and writes one line per call to the same ledger as
model calls, `kind: "embedding"`, so that what a step spent is read from
the file and is exact. It sits under the vector cache wherever there is
one, so a vector already paid for writes nothing.

A call that fails is written at no cost and the failure is raised on, as
the embedder raised it.
"""

from app.shell.embedder import Embedded, Embedder, EmbeddingFailed
from app.shell.model_client import SpendLedger


class LedgeredEmbedder:
    def __init__(self, inner: Embedder, ledger: SpendLedger, purpose: str) -> None:
        self._inner = inner
        self.model = inner.model
        self._ledger = ledger
        self._purpose = purpose

    def embed(self, texts: list[str]) -> Embedded:
        try:
            result = self._inner.embed(texts)
        except EmbeddingFailed:
            self._ledger.record_embedding(self._purpose, self.model, len(texts), 0, 0.0, "failed")
            raise
        self._ledger.record_embedding(self._purpose, self.model, len(texts), result.tokens, result.cost_usd, "ok")
        return result
