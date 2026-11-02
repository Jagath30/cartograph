"""Redis as the rate limits, the answer cache and the re-ingest use it, in
memory, with a clock a test moves (step 14). No network, and no package
beyond redis's own errors."""

import redis


class FakeRedis:
    def __init__(self) -> None:
        self.now = 0.0
        self.values: dict[str, bytes] = {}
        self.expires: dict[str, float] = {}

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _alive(self, key: str) -> bool:
        if key in self.expires and self.expires[key] <= self.now:
            del self.values[key], self.expires[key]
        return key in self.values

    def incr(self, key: str) -> int:
        value = int(self.values[key]) + 1 if self._alive(key) else 1
        self.values[key] = str(value).encode()
        return value

    def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        if not self._alive(key) or (nx and key in self.expires):
            return False
        self.expires[key] = self.now + seconds
        return True

    def ttl(self, key: str) -> int:
        if not self._alive(key):
            return -2
        if key not in self.expires:
            return -1
        return int(round(self.expires[key] - self.now))

    def get(self, key: str) -> bytes | None:
        return self.values[key] if self._alive(key) else None

    def set(self, key: str, value, nx: bool = False, ex: int | None = None) -> bool:
        if nx and self._alive(key):
            return False
        self.values[key] = value if isinstance(value, bytes) else str(value).encode()
        self.expires.pop(key, None)
        if ex is not None:
            self.expires[key] = self.now + ex
        return True

    def delete(self, key: str) -> int:
        alive = self._alive(key)
        self.values.pop(key, None)
        self.expires.pop(key, None)
        return int(alive)

    def keys_held(self) -> list[str]:
        return [key for key in list(self.values) if self._alive(key)]


class DownRedis:
    """Redis that cannot be reached: every call raises as redis-py does."""

    def __getattr__(self, name):
        def refused(*args, **kwargs):
            raise redis.ConnectionError("Error 111 connecting to redis:6379. Connection refused.")

        return refused
