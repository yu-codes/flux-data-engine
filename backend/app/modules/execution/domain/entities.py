"""Execution domain.

Execution is the top-level concept, not "training job" or "prediction job".
Training and prediction are two of its kinds, alongside simulation,
optimisation, calculation, evaluation and transformation. Every kind produces
a Result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.modules.model.domain.plugin import ExecutionKind
from app.shared.ids import new_id, utcnow

__all__ = ["ExecutionKind", "ExecutionStatus", "Execution"]


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        )


class RunnableKind(str, Enum):
    """What an execution can run.

    A Model and a Pipeline both fit the platform's own formula - inputs,
    parameters, a versioned definition, an output - and for a long time only
    one of them could be executed, scheduled, compared or served. Everything
    that made a Model runnable had to be built twice, once for each, or built
    once and denied to the other.

    Naming the kind here is what lets the rest of the platform stop caring
    which it has.
    """

    MODEL = "model"
    PIPELINE = "pipeline"


@dataclass
class Execution:
    """One run of one runnable over one input."""

    #  What ran. Absent when the execution carries its own definition rather
    #  than naming something from the library - a pipeline step, for instance,
    #  whose configuration has no life outside the pipeline that contains it.
    target_id: str | None
    kind: ExecutionKind
    target_type: RunnableKind = RunnableKind.MODEL
    model_version_id: str | None = None
    #  The definition, when it did not come from the library. Stored on the
    #  execution for exactly the reason a version snapshot is: what ran has to
    #  be recoverable from the record, not reconstructed from something that
    #  may since have changed.
    definition_snapshot: dict[str, Any] = field(default_factory=dict)
    #  Input is either a dataset version reference or an inline payload.
    dataset_version_id: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    runtime: str = "python"
    status: ExecutionStatus = ExecutionStatus.PENDING
    result_id: str | None = None
    produced_model_version_id: str | None = None
    experiment_id: str | None = None
    logs: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    #  Set when somebody asks a running execution to stop. The runner checks it
    #  rather than being killed, so the execution ends at a consistent point
    #  and records that it was cancelled rather than that it failed.
    cancel_requested: bool = False
    #  How many times this has been started. Counted so that an execution
    #  which never finishes is eventually given up on rather than retried by
    #  every recovery sweep from now on.
    attempts: int = 0
    #  When the worker holding this execution last said it was alive. A RUNNING
    #  row whose heartbeat has gone stale belonged to a worker that died.
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    id: str = field(default_factory=lambda: new_id("exec"))
    created_at: datetime = field(default_factory=utcnow)

    # -- lifecycle ---------------------------------------------------------
    def mark_running(self) -> None:
        self.status = ExecutionStatus.RUNNING
        self.attempts += 1
        self.started_at = utcnow()
        self.heartbeat_at = self.started_at

    def exhausted(self, max_attempts: int) -> bool:
        """Whether this has been tried as many times as it is going to be."""
        return self.attempts >= max_attempts

    def beat(self) -> None:
        """Say the worker holding this is still alive."""
        self.heartbeat_at = utcnow()

    def request_cancel(self) -> None:
        """Ask a running execution to stop at its next checkpoint."""
        self.cancel_requested = True

    def is_stale(self, *, now: datetime, after_seconds: int) -> bool:
        """Whether a RUNNING execution has stopped reporting in.

        A missing heartbeat on a row that has been running is treated as stale
        too: it can only mean the row predates heartbeats, and leaving it
        RUNNING for ever is the worse answer.
        """
        if self.status is not ExecutionStatus.RUNNING:
            return False
        last = self.heartbeat_at or self.started_at
        if last is None:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=now.tzinfo)
        return (now - last).total_seconds() > after_seconds

    def mark_succeeded(self, *, result_id: str | None, metrics: dict[str, Any]) -> None:
        self.status = ExecutionStatus.SUCCEEDED
        self.result_id = result_id
        self.metrics = metrics
        self.finished_at = utcnow()

    def mark_failed(self, error: str) -> None:
        self.status = ExecutionStatus.FAILED
        self.error = error
        self.finished_at = utcnow()

    def mark_cancelled(self, reason: str | None = None) -> None:
        self.status = ExecutionStatus.CANCELLED
        self.error = reason
        self.finished_at = utcnow()

    @property
    def model_id(self) -> str | None:
        """The model this ran, when it ran a model at all.

        Read-only on purpose. Callers asking "which model" get an honest
        answer - `None` for a pipeline - and callers that mean "what ran" have
        to say `target_id`, which is the distinction this change exists to
        make.
        """
        return self.target_id if self.target_type is RunnableKind.MODEL else None

    @property
    def pipeline_id(self) -> str | None:
        """The pipeline this ran, when it ran one."""
        return self.target_id if self.target_type is RunnableKind.PIPELINE else None

    @property
    def duration_seconds(self) -> float | None:
        if not self.started_at or not self.finished_at:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 3)
