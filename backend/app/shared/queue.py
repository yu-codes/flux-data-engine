"""One Redis connection per URL, shared by everything that queues work.

Both the execution dispatcher and the job dispatcher push onto the same list,
so they need the same client. Reaching into one module from the other to get it
would be a dependency between two peers for the sake of four lines of
connection setup - which is how a module graph acquires edges nobody meant.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache
def redis_client(redis_url: str):
    """Cached because a client is a connection pool, not a request."""
    import redis

    return redis.Redis.from_url(redis_url, decode_responses=True)
