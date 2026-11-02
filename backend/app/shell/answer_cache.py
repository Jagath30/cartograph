"""The answer cache in Redis (FR-35, DR-12, step 14). Impure (DD-01): what
is cached, under which key, and what a hit gives are
app.core.answer_cache's.

Each entry expires 24 hours after it is written. Redis's own memory
policy (allkeys-lru, render.yaml) drops the least recently used when it
is full.

FAIL OPEN. When Redis cannot be reached or answers with an error, there
is no cache: a lookup is a miss, a write is skipped, one line is logged
with the error's class, and the question is asked as before step 14.

THE CODE FINGERPRINT (the owner's ruling 8) is read here, once, when the
service is made at start: the .py files an answer depends on, their paths
sorted. Its first 12 characters are logged.
"""

import logging
from pathlib import Path

import redis

from app.core.answer_cache import TTL_SECONDS, Cached, code_fingerprint, read_entry

log = logging.getLogger("cartograph.cache")

TIMEOUT_SECONDS = 0.5

APP = Path(__file__).resolve().parents[1]
# What an answer depends on: every rule and prompt (app/core), the
# orchestrator, and the shell that opens the pipeline, scores the
# question, calls the model and runs the SQL.
FINGERPRINTED = ("core/**/*.py", "orchestrator.py", "shell/pipeline_session.py", "shell/retrieval_session.py",
                 "shell/semantic_index.py", "shell/model_client.py", "shell/query_executor.py")  # fmt: skip


def read_fingerprint(app: Path = APP) -> str:
    files = sorted({path for pattern in FINGERPRINTED for path in app.glob(pattern)})
    fingerprint = code_fingerprint([(path.relative_to(app).as_posix(), path.read_bytes()) for path in files])
    log.info("answer cache: code fingerprint %s, from %d files", fingerprint[:12], len(files))
    return fingerprint


class AnswerCache:
    def __init__(self, client) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> "AnswerCache":
        return cls(redis.Redis.from_url(url, socket_connect_timeout=TIMEOUT_SECONDS, socket_timeout=TIMEOUT_SECONDS))

    def get(self, key: str) -> Cached | None:
        try:
            raw = self._client.get(key)
        except redis.RedisError as error:
            _open(error)
            return None
        return read_entry(raw) if raw is not None else None

    def put(self, key: str, entry: bytes) -> None:
        try:
            self._client.set(key, entry, ex=TTL_SECONDS)
        except redis.RedisError as error:
            _open(error)


def _open(error: Exception) -> None:
    log.warning("answer cache not used: Redis did not answer (%s); the question is asked as before", type(error).__name__)
