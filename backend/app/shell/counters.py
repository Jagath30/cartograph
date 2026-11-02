"""Counters in Redis, with an expiry: what the rate limits are counted in
(FR-36, step 14). Impure (DD-01); what each limit is, is app.core.limits'.

FAIL OPEN. When Redis cannot be reached, or answers with an error, a
limit is not applied: the request goes on as it would have before step
14, and one line is logged, naming the error's class and nothing else. A
demo whose cache is down still answers.

A WINDOW STARTS at the first count: INCR, then EXPIRE with NX, so a later
count never moves the end. The window's end is the key's TTL.
"""

import logging
from dataclasses import dataclass

import redis

from app.core.limits import Limit, retry_after

log = logging.getLogger("cartograph.limits")

# A request waits at most this long on Redis before going on without it.
TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True)
class Reached:
    """A limit reached: which, and the seconds until it lifts."""

    limit: Limit
    retry_after: int


class Counters:
    """What a limit needs from Redis. `client` is a redis.Redis, or
    anything with its incr, expire, ttl, get, delete and set."""

    def __init__(self, client) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> "Counters":
        return cls(redis.Redis.from_url(url, socket_connect_timeout=TIMEOUT_SECONDS, socket_timeout=TIMEOUT_SECONDS))

    def count(self, limit: Limit, subject: str) -> Reached | None:
        """One more, counted. Reached when this one is past the limit."""
        key = limit.key(subject)
        try:
            seen = self._client.incr(key)
            self._client.expire(key, limit.window_seconds, nx=True)
            if seen <= limit.count:
                return None
            return Reached(limit, retry_after(self._client.ttl(key), limit))
        except redis.RedisError as error:
            _open(error)
            return None

    def reached(self, limit: Limit, subject: str) -> Reached | None:
        """Whether the limit is reached already, counting nothing."""
        key = limit.key(subject)
        try:
            seen = self._client.get(key)
            if seen is None or int(seen) < limit.count:
                return None
            return Reached(limit, retry_after(self._client.ttl(key), limit))
        except redis.RedisError as error:
            _open(error)
            return None

    def clear(self, limit: Limit, subject: str) -> None:
        try:
            self._client.delete(limit.key(subject))
        except redis.RedisError as error:
            _open(error)

    def claim(self, key: str, seconds: int) -> int | None:
        """The key taken for `seconds`, if no one holds it: None. Held, the
        seconds left. Redis down raises: a caller that must not run without
        Redis (the re-ingest) refuses then, and never fails open."""
        if self._client.set(key, "1", nx=True, ex=seconds):
            return None
        return max(1, self._client.ttl(key))


def _open(error: Exception) -> None:
    log.warning("rate limit not applied: Redis did not answer (%s); the request goes on", type(error).__name__)
