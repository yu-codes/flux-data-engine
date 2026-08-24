"""Execution dispatch: run now, or hand off to a worker.

Both backends satisfy the same port, so `ExecutionService.submit()` reads the
same either way. The only visible difference is the status the caller gets
back: `succeeded`/`failed` inline, `pending` when queued.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.shared.queue import redis_client

logger = logging.getLogger(__name__)


class InlineDispatcher:
    """Runs the execution in the caller's own request."""

    runs_inline = True
    mode = "inline"

    def enqueue(self, execution_id: str) -> None:  # pragma: no cover - trivial
        """Nothing to do: the service runs it directly."""


class RedisQueueDispatcher:
    """Pushes the execution id onto a Redis list for the worker to pick up.

    The push is deferred until the surrounding transaction commits. Without
    that, a worker could pop an id whose row is not yet visible to it.
    """

    runs_inline = False
    mode = "queue"

    def __init__(self, session: Session, *, redis_url: str, queue_name: str):
        self.session = session
        self.redis_url = redis_url
        self.queue_name = queue_name

    def enqueue(self, execution_id: str) -> None:
        session = self.session

        @event.listens_for(session, "after_commit", once=True)
        def _push(_session) -> None:  # noqa: ANN001 - SQLAlchemy event signature
            try:
                client = redis_client(self.redis_url)
                client.rpush(
                    self.queue_name,
                    json.dumps({"execution_id": execution_id}),
                )
                logger.info("queued execution %s", execution_id)
            except Exception:
                #  The execution stays PENDING and is picked up by the worker's
                #  recovery sweep, so a queue blip never loses work.
                logger.exception("could not queue execution %s", execution_id)




def build_dispatcher(session: Session, settings: Settings):
    if settings.uses_queue:
        return RedisQueueDispatcher(
            session, redis_url=settings.redis_url, queue_name=settings.queue_name
        )
    return InlineDispatcher()
