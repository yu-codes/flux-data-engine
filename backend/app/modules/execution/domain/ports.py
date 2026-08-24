"""Persistence port for the execution module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .entities import Execution


class ExecutionRepository(Protocol):
    def add(self, execution: Execution) -> Execution: ...
    def get(self, execution_id: str) -> Execution | None: ...

    def claim(self, execution_id: str) -> Execution | None:
        """Take ownership of a pending execution, or return None.

        One statement, so that exactly one caller can win. Reading a row,
        deciding it is not running yet and then writing "running" is three
        steps with two gaps in them, and both gaps are wide enough for a
        second worker - which is how the same execution gets run twice, two
        Results get written and nothing anywhere says so.
        """
        ...
    def update(self, execution: Execution) -> Execution: ...
    def list(
        self,
        *,
        model_id: str | None = ...,
        target_id: str | None = ...,
        target_type: str | None = ...,
        kind: str | None = ...,
        status: str | None = ...,
        experiment_id: str | None = ...,
        dataset_version_id: str | None = ...,
        limit: int = ...,
    ) -> list[Execution]: ...
    def delete(self, execution_id: str) -> None: ...


@runtime_checkable
class RunnableRunner(Protocol):
    """Runs one kind of runnable and reports what happened.

    Injected rather than imported. A pipeline lives above execution in the
    dependency stack, so the only way for an execution to run one without
    turning the stack upside down is for the composition root to hand the
    capability down.

    The outcome is the same shape a plugin returns, so everything after the
    call - persisting the Result, materialising a Dataset, recording metrics -
    is the path every execution already takes.
    """

    def __call__(self, execution: Execution) -> Any: ...


@runtime_checkable
class ExecutionDispatcher(Protocol):
    """Decides where an execution actually runs.

    `runs_inline` tells the service whether to execute immediately or to hand
    the id off and return a pending execution.
    """

    runs_inline: bool
    mode: str

    def enqueue(self, execution_id: str) -> None: ...


@dataclass(frozen=True)
class RunInline:
    """Default dispatch policy: run in the caller's own transaction.

    A pure policy object, not infrastructure - which is why it can live in the
    domain and be the service's default.
    """

    runs_inline: bool = True
    mode: str = "inline"

    def enqueue(self, execution_id: str) -> None:
        """No hand-off: the service runs the execution itself."""
