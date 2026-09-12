"""Reachability probes for the services Cartograph depends on.

These open real connections, so they belong in shell/ rather than core/
(DD-01). They return an outcome instead of raising, so the caller decides
what a failure means (DD-04).
"""

import psycopg
import redis.asyncio as aioredis


async def check_postgres(dsn: str) -> tuple[bool, str]:
    try:
        async with await psycopg.AsyncConnection.connect(dsn, connect_timeout=3) as conn:
            async with conn.cursor() as cur:
                await cur.execute("select 1")
                await cur.fetchone()
        return True, "ok"
    except Exception as exc:
        # The exception class, never str(exc): a psycopg connection error
        # includes the DSN, and the DSN includes the password (NFR-09).
        return False, type(exc).__name__


async def check_redis(url: str) -> tuple[bool, str]:
    client = aioredis.from_url(url, socket_connect_timeout=3)
    try:
        await client.ping()
        return True, "ok"
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        await client.aclose()
