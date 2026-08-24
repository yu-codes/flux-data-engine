"""What a pipeline needs from the outside: storage, and somewhere to run."""

from __future__ import annotations

from typing import Any, Protocol

from .entities import Pipeline, PipelineRun, StepRun


class PipelineRepository(Protocol):
    def add(self, pipeline: Pipeline) -> Pipeline: ...
    def get(self, pipeline_id: str) -> Pipeline | None: ...
    def get_by_name(self, name: str) -> Pipeline | None: ...
    def list(self) -> list[Pipeline]: ...
    def update(self, pipeline: Pipeline) -> Pipeline: ...
    def delete(self, pipeline_id: str) -> None: ...

    def add_run(self, run: PipelineRun) -> PipelineRun: ...
    def get_run(self, run_id: str) -> PipelineRun | None: ...
    def list_runs(
        self, *, pipeline_id: str | None = ..., limit: int = ...
    ) -> list[PipelineRun]: ...
    def update_run(self, run: PipelineRun) -> PipelineRun: ...


class StepWorker(Protocol):
    """Runs one step of a pipeline somewhere else.

    "Somewhere else" is a thread with a database session of its own, which is
    what makes running independent steps at the same time safe rather than
    merely fast. The pipeline does not know how one is made; it is handed this
    by the composition root, the same way execution learned to run a pipeline.
    """

    def __call__(
        self,
        pipeline_id: str,
        step_name: str,
        order: int,
        source_version_id: str | None,
        source_table: Any,
        extra_inputs: dict[str, Any],
        depth: int,
    ) -> StepRun: ...
