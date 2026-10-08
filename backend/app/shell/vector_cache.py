"""CachedEmbedder: a vector once paid for is not paid for again (NFR-14).

Wraps any Embedder and is one itself. Each text's vector is kept in a file
named by the sha256 of the model and the text, in a directory git ignores.
So re-running the evaluation, or sweeping a parameter over it, embeds
nothing after the first time: it costs nothing and, the vectors being the
same bytes, it repeats exactly.

Impure (DD-01): it reads and writes files. It holds no key and knows no
provider; the embedder it wraps does.

Vectors are stored as 32-bit floats, which is what pgvector stores too.
"""

import hashlib
from array import array
from pathlib import Path

from app.shell.embedder import DIMENSIONS, Embedded, Embedder


class CachedEmbedder:
    def __init__(self, inner: Embedder, directory: str | Path) -> None:
        self._inner = inner
        self._directory = Path(directory)
        self.model = inner.model

    def embed(self, texts: list[str]) -> Embedded:
        """Tokens and cost are those of the texts that had to be embedded
        now. Texts found in the cache cost nothing."""
        found: dict[str, tuple[float, ...]] = {}
        for text in dict.fromkeys(texts):
            path = self._path(text)
            if path.exists():
                values = array("f")
                values.frombytes(path.read_bytes())
                if len(values) == DIMENSIONS:
                    found[text] = tuple(values)

        missing = [text for text in dict.fromkeys(texts) if text not in found]
        tokens, cost = 0, 0.0
        if missing:
            result = self._inner.embed(missing)
            tokens, cost = result.tokens, result.cost_usd
            self._directory.mkdir(parents=True, exist_ok=True)
            for text, vector in zip(missing, result.vectors):
                values = array("f", vector)
                self._path(text).write_bytes(values.tobytes())
                # What is returned is what a later run will read back.
                found[text] = tuple(values)
        return Embedded(tuple(found[text] for text in texts), tokens, cost)

    def _path(self, text: str) -> Path:
        digest = hashlib.sha256(f"{self.model}\n{text}".encode()).hexdigest()
        return self._directory / f"{digest}.f32"
