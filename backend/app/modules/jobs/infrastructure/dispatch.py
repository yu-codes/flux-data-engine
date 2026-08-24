"""Job dispatch: run now, or hand off to a worker.

The same shape as execution dispatch, onto the same Redis list. One queue, two
message shapes: the worker looks at which key is present. A second queue would
mean a second blocking pop, a second idle timeout, and two places to look when
work goes missing.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.shared.queue import redis_client

logger = logging.getLogger(__name__)


class RedisJobDispatcher:
    """Push the job id once the transaction that created it has committed.

    Without the deferral a worker can pop an id whose row is not yet visible -
    the same bug the execution dispatcher already avoids, and worth avoiding in
    the same way rather than a different one.
    """

    runs_inline = False
    mode = "queue"

    def __init__(self, session: Session, *, redis_url: str, queue_name: str):
        self.session = session
        self.redis_url = redis_url
        self.queue_name = queue_name

    def enqueue(self, job_id: str) -> None:
        session = self.session

        @event.listens_for(session, "after_commit", once=True)
        def _push(_session) -> None:  # noqa: ANN001 - SQLAlchemy event signature
            try:
                redis_client(self.redis_url).rpush(
                    self.queue_name, json.dumps({"job_id": job_id})
                )
                logger.info("queued job %s", job_id)
            except Exception:
                #  The job stays PENDING and the recovery sweep finds it, so a
                #  queue blip never loses work.
                logger.exception("could not queue job %s", job_id)


def build_job_dispatcher(session: Session, settings: Settings):
    from ..domain.ports import RunJobsInline

    if settings.uses_queue:
        return RedisJobDispatcher(
            session, redis_url=settings.redis_url, queue_name=settings.queue_name
        )
    return RunJobsInline()
