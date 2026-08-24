"""What a Job is.

Deliberately the same vocabulary as an Execution - pending, running, terminal,
cancellable, heartbeat - because they are the same idea at different
granularities and having two words for it is how they drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.shared.ids import new_id, utcnow


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass
class Job:
    """One piece of work that outlives the request that asked for it.

    `kind` is a string rather than an enum on purpose: this module must not
    know which kinds exist, or it would have to import every module that
    defines one and would end up above them all instead of below.
    """

    kind: str
    target_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    #  What the handler produced - ids, counts, whatever the caller needs to
    #  find the real output. Never the output itself: a job record is metadata.
    outcome: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    attempts: int = 0
    cancel_requested: bool = False
    heartbeat_at: datetime | None = None
    requested_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    id: str = field(default_factory=lambda: new_id("job"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    # -- lifecycle ---------------------------------------------------------
    def mark_running(self) -> None:
        self.status = JobStatus.RUNNING
        self.attempts += 1
        self.started_at = utcnow()
        self.heartbeat_at = self.started_at
        self.updated_at = self.started_at

    def beat(self) -> None:
        self.heartbeat_at = utcnow()
        self.updated_at = self.heartbeat_at

    def request_cancel(self) -> None:
        self.cancel_requested = True
        self.updated_at = utcnow()

    def mark_succeeded(self, outcome: dict[str, Any] | None = None) -> None:
        self.status = JobStatus.SUCCEEDED
        self.outcome = outcome or {}
        self.finished_at = utcnow()
        self.updated_at = self.finished_at

    def mark_failed(self, error: str) -> None:
        self.status = JobStatus.FAILED
        self.error = error
        self.finished_at = utcnow()
        self.updated_at = self.finished_at

    def mark_cancelled(self, reason: str | None = None) -> None:
        self.status = JobStatus.CANCELLED
        self.error = reason
        self.finished_at = utcnow()
        self.updated_at = self.finished_at

    # -- questions ---------------------------------------------------------
    @property
    def duration_seconds(self) -> float | None:
        if not self.started_at or not self.finished_at:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 3)

    def is_stale(self, *, now: datetime, after_seconds: int) -> bool:
        """Whether a RUNNING job has stopped reporting in."""
        if self.status is not JobStatus.RUNNING:
            return False
        last = self.heartbeat_at or self.started_at
        if last is None:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=now.tzinfo)
        return (now - last).total_seconds() > after_seconds
