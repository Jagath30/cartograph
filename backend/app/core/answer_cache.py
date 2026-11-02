"""The answer cache (FR-35, DR-12, NFR-03, NFR-25, item 116, step 14).
Pure: the key, what may be cached, the entry, and the answer a hit gives.
Redis is app.shell.answer_cache's.

THE KEY is the sha256 of everything that can change an answer:

  question       trimmed, every run of whitespace one space, case kept
  snapshot       the stored snapshot's hash (the schema, every readable
                 name, every note: the overlay but its preferences)
  preferences    the hash of the preferences in force, which the snapshot
                 hash leaves out (item 70)
  pipeline       what the process was started with: the mode, the models
                 and their settings, the calibration's hash, and the code
                 fingerprint, a sha256 of the source the prompts and every
                 rule live in (the owner's ruling 8). Change any one and
                 the key changes, so an entry is never served across it.

WHAT IS CACHED: an answer (`answered`) and a decline (`not_answerable`),
never a failure. The rows travel with the entry, in Redis only, never in
the application store (DR-15). An entry over 256 KB is not cached.

AN ENTRY HOLDS NOTHING OF WHO ASKED. The document is stored without its
query_id, user_id, created_at and question; the hit puts the asker's own
in. Nothing of another user reaches the asker: no user id, query id,
email or time asked.

A HIT is a new trace at version 4: the cached document, under a new query
id, the asker's user id and question, now, costing 0 with no tokens, its
own duration, and `served_from_cache` true. The stages inside are the
first computation's, as it ran: they hold no identity. A stored trace is
never altered.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.trace_document import TRACE_VERSION, TraceDocument

TTL_SECONDS = 24 * 60 * 60
MAX_ENTRY_BYTES = 256 * 1024
CACHEABLE = frozenset({"answered", "not_answerable"})
# Left out of an entry, and put back from the asker on a hit.
WHO_AND_WHEN = ("query_id", "user_id", "created_at", "question")

def normalise(question: str) -> str:
    """Trimmed, each run of whitespace one space, case kept."""
    return " ".join(question.split())


def cache_key(question: str, snapshot_hash: str, preferences_hash: str, pipeline: dict[str, Any]) -> str:
    content = {
        "question": normalise(question),
        "snapshot": snapshot_hash,
        "preferences": preferences_hash,
        "pipeline": pipeline,
    }
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return f"cartograph:answer:{digest}"


@dataclass(frozen=True)
class Cached:
    """What a hit gives back: the document as cached, and the rows."""

    document: dict
    rows: list[list] | None


def entry(document: TraceDocument, rows: list[list] | None) -> bytes | None:
    """The bytes to cache, or None when this answer is not cached: a
    failure, or over the size limit."""
    if document.outcome not in CACHEABLE:
        return None
    body = document.model_dump(mode="json")
    for field in WHO_AND_WHEN:
        body.pop(field)
    encoded = json.dumps({"document": body, "rows": rows}, ensure_ascii=False, separators=(",", ":")).encode()
    return encoded if len(encoded) <= MAX_ENTRY_BYTES else None


def read_entry(raw: bytes) -> Cached:
    held = json.loads(raw)
    return Cached(held["document"], held["rows"])


def served(cached: Cached, query_id: UUID, user_id: int, question: str, created_at: datetime, duration_ms: int) -> TraceDocument:
    """The trace a hit stores and returns: version 4, the asker's, now,
    at no cost."""
    body = dict(cached.document)
    body.update(
        trace_version=TRACE_VERSION, query_id=str(query_id), user_id=user_id, question=question,
        created_at=created_at.isoformat(), cost_usd=0.0, tokens_in=0, tokens_out=0, duration_ms=duration_ms,
        served_from_cache=True,
    )  # fmt: skip
    return TraceDocument.model_validate(body)


def pipeline_identity(
    mode: str, sql_model: str, reasoning_effort: str, temperature: float, max_output_tokens: int,
    embedding_model: str, calibration_sha256: str, code_fingerprint: str,
) -> dict[str, Any]:  # fmt: skip
    """What the process was started with that can change an answer and is
    not in the snapshot's or the preferences' hash."""
    return {
        "mode": mode, "sql_model": sql_model, "reasoning_effort": reasoning_effort, "temperature": temperature,
        "max_output_tokens": max_output_tokens, "embedding_model": embedding_model,
        "calibration": calibration_sha256, "code": code_fingerprint,
    }  # fmt: skip


def code_fingerprint(sources: list[tuple[str, bytes]]) -> str:
    """The sha256 of the given .py files, each its path and its bytes, the
    paths sorted (the owner's ruling 8)."""
    digest = hashlib.sha256()
    for path, content in sorted(sources):
        digest.update(path.encode() + b"\0" + hashlib.sha256(content).hexdigest().encode() + b"\n")
    return digest.hexdigest()
