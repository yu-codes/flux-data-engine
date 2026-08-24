"""Running work that outlives the request that asked for it."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from app.shared.errors import NotFoundError, UnsupportedError, ValidationError
from app.shared.ids import utcnow

from ..domain.entities import Job, JobStatus
from ..domain.ports import JobDispatcher, JobRepository, RunJobsInline

logger = logging.getLogger(__name__)

#  A handler is given the job and returns whatever the caller needs to find the
#  real output - ids and counts, not the output itself.
JobHandler = Callable[[Job], dict[str, Any]]


class JobService:
    """Submit, run, inspect and cancel long-running work.

    Handlers are injected rather than imported. That is the whole reason this
    module can sit underneath pipelines, experiments and reports instead of on
    top of them: it runs work it knows nothing about.
    """

    def __init__(
        self,
        repository: JobRepository,
        handlers: dict[str, JobHandler] | None = None,
        dispatcher: JobDispatcher | None = None,
    ):
        self.repository = repository
        self.handlers: dict[str, JobHandler] = handlers or {}
        self.dispatcher = dispatcher or RunJobsInline()

    # -- reads -------------------------------------------------------------
    def get(self, job_id: str) -> Job:
        job = self.repository.get(job_id)
        if not job:
            raise NotFoundError(f"job '{job_id}' not found")
        return job

    def list(self, **filters) -> list[Job]:
        return self.repository.list(**filters)

    def kinds(self) -> list[str]:
        return sorted(self.handlers)

    # -- writes ------------------------------------------------------------
    def submit(
        self,
        *,
        kind: str,
        target_id: str,
        parameters: dict[str, Any] | None = None,
        requested_by: str | None = None,
        force_inline: bool = False,
    ) -> Job:
        """Record the work, then either do it or hand it to a worker."""
        if kind not in self.handlers:
            raise UnsupportedError(
                f"no handler for job kind '{kind}'",
                details={"supported": self.kinds()},
            )
        job = self.repository.add(
            Job(
                kind=kind,
                target_id=target_id,
                parameters=parameters or {},
                requested_by=requested_by,
            )
        )
        if force_inline or self.dispatcher.runs_inline:
            return self.run(job.id)
        self.dispatcher.enqueue(job.id)
        return job

    def run(self, job_id: str) -> Job:
        """Execute a job's handler and record what happened.

        A handler that raises is recorded as a failed job rather than
        propagating: a background worker has nowhere to propagate to, and a job
        that vanished without a trace is worse than one that says it failed.
        """
        job = self.get(job_id)
        if job.status.is_terminal:
            return job
        if job.cancel_requested:
            job.mark_cancelled("cancelled before it started")
            return self.repository.update(job)

        handler = self.handlers.get(job.kind)
        if handler is None:
            job.mark_failed(f"no handler for job kind '{job.kind}'")
            return self.repository.update(job)

        #  Claimed in one statement: the queue can hand the same id to two
        #  workers, and the loser has to learn it lost from the database.
        claimed = self.repository.claim(job.id)
        if claimed is None:
            logger.info("job %s is already claimed by another worker", job.id)
            return self.get(job.id)
        job = claimed
        try:
            outcome = handler(job) or {}
        except Exception as exc:
            logger.exception("job %s (%s) failed", job.id, job.kind)
            job.error = f"{type(exc).__name__}: {exc}"
            job.outcome = {"traceback": traceback.format_exc(limit=8)}
            job.mark_failed(job.error)
            return self.repository.update(job)

        if self._cancel_requested(job.id):
            job.mark_cancelled("cancelled while running")
            return self.repository.update(job)

        job.mark_succeeded(outcome)
        return self.repository.update(job)

    def cancel(self, job_id: str) -> Job:
        """Stop a job, or ask it to."""
        job = self.get(job_id)
        if job.status.is_terminal:
            raise ValidationError(
                f"job is already {job.status.value} and cannot be cancelled"
            )
        if job.status is JobStatus.RUNNING:
            job.request_cancel()
            return self.repository.update(job)
        job.mark_cancelled("cancelled before it started")
        return self.repository.update(job)

    def beat(self, job_id: str) -> None:
        job = self.repository.get(job_id)
        if job and job.status is JobStatus.RUNNING:
            job.beat()
            self.repository.update(job)

    def reclaim_stale(self, *, after_seconds: int, limit: int = 50) -> list[Job]:
        """Fail RUNNING jobs whose worker stopped reporting in."""
        now = utcnow()
        reclaimed = []
        for job in self.repository.list(status=JobStatus.RUNNING.value, limit=limit):
            if not job.is_stale(now=now, after_seconds=after_seconds):
                continue
            job.mark_failed("the worker running this job stopped")
            reclaimed.append(self.repository.update(job))
        return reclaimed

    # -- internals ---------------------------------------------------------
    def _cancel_requested(self, job_id: str) -> bool:
        """Re-read: the request arrives from another transaction."""
        current = self.repository.get(job_id)
        return bool(current and current.cancel_requested)
