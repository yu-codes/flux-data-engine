"""Persistence and dispatch contracts for jobs."""

from __future__ import annotations

from typing import Protocol

from .entities import Job


class JobRepository(Protocol):
    def add(self, job: Job) -> Job: ...

    def get(self, job_id: str) -> Job | None: ...

    def update(self, job: Job) -> Job: ...

    def claim(self, job_id: str) -> Job | None:
        """Take ownership of a pending job, or return None.

        One statement, so exactly one caller can win. See the same method on
        `ExecutionRepository`: the queue can hand the same id to two workers,
        and read-check-write leaves two gaps for both of them to walk through.
        """
        ...

    def list(
        self,
        *,
        kind: str | None = None,
        target_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Job]: ...


class JobDispatcher(Protocol):
    """How a job gets from "recorded" to "running".

    The same shape as the execution dispatcher, and for the same reason: the
    service reads identically whether the work happens here or on a worker, and
    the only visible difference is the status the caller gets back.
    """

    runs_inline: bool
    mode: str

    def enqueue(self, job_id: str) -> None: ...


class RunJobsInline:
    """Default policy: run the job in the caller's own transaction."""

    runs_inline: bool = True
    mode: str = "inline"

    def enqueue(self, job_id: str) -> None:
        """Nothing to do: the service runs it directly."""
