"""Server-sent events for one job.

Transport, not policy: the framing, the cadence and the connection lifetime all
belong to how the answer travels, not to what a job is. The route below is
therefore three lines, and this is testable without an HTTP client.

Server-sent events rather than websockets because the traffic is one-way, it is
plain HTTP so it needs no new infrastructure, and the browser reconnects on its
own. Without any of it, a queued job finished with nothing to tell the page
that asked for it - which is why queue mode looked like nothing was happening.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

#  How often a watched job is re-read. Short enough that a finished job is
#  reported promptly; long enough that many open tabs are not many queries a
#  second.
POLL_SECONDS = 1.0
#  A stream is closed after this even if the job is still going, so a forgotten
#  tab cannot hold a connection open indefinitely. The client reconnects.
STREAM_TIMEOUT_SECONDS = 600


def sse(event: str, payload: dict[str, Any]) -> str:
    """One server-sent event, framed."""
    return f"event: {event}\ndata: {json.dumps(payload, sort_keys=True, default=str)}\n\n"


async def watch(
    read: Callable[[], tuple[dict[str, Any], bool]],
    *,
    poll_seconds: float = POLL_SECONDS,
    timeout_seconds: float = STREAM_TIMEOUT_SECONDS,
) -> AsyncIterator[str]:
    """Emit a job's state whenever it changes, until it finishes.

    `read` returns the current snapshot and whether the job has reached a
    terminal state. Passing it in keeps this free of sessions and containers,
    so the cadence and the framing can be tested without a database.

    Unchanged states are not re-sent: a job that takes ten minutes should cost
    a handful of events, not six hundred identical ones.
    """
    #  A wall-clock deadline, not an accumulated sleep count: the read itself
    #  takes time, and adding up the intervals would never expire at all if the
    #  interval were zero.
    deadline = time.monotonic() + timeout_seconds
    last: str | None = None
    while time.monotonic() < deadline:
        snapshot, terminal = read()
        message = sse("status", snapshot)
        if message != last:
            last = message
            yield message
        if terminal:
            return
        await asyncio.sleep(poll_seconds)
    #  Say why it ended, so a client reconnects instead of concluding the job
    #  disappeared.
    yield sse("timeout", {"reason": "stream expired"})
