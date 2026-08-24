"""Pipeline service: build a graph of model executions and run it.

Running a pipeline creates one ordinary Execution per step. Nothing about the
Execution or Result abstractions changes — a pipeline is a way of wiring them
together, not a parallel universe.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import monotonic
from typing import Any

from app.modules.data.application.services import DatasetService
from app.modules.execution.application.services import (
    ExecutionService,
    InvokeOutcome,
    invoke_response,
)
from app.modules.model.domain.entities import ModelDefinition
from app.modules.results.application.services import ResultService
from app.shared.errors import (
    ConflictError,
    ExecutionError,
    FluxError,
    NotFoundError,
    ValidationError,
)
from app.shared.ids import utcnow
from app.shared.tabular import Table

from ..domain.entities import (
    Pipeline,
    PipelineRun,
    PipelineStatus,
    PipelineStep,
    RunStatus,
    StepRun,
    validate_steps,
    waves,
)
from ..domain.ports import PipelineRepository, StepWorker

logger = logging.getLogger(__name__)

#  How far a pipeline may nest inside another. Deep enough for the reason
#  nesting exists - a shared preparation, used by a pipeline that is itself
#  used - and shallow enough that a mistake stops rather than runs all night.
MAX_NESTING_DEPTH = 5


def _copy_step_run(source: StepRun, target: StepRun) -> None:
    """Move what a worker recorded onto the run's own record of that step."""
    for field_name in (
        "status",
        "execution_id",
        "pipeline_run_id",
        "result_id",
        "dataset_id",
        "dataset_version_id",
        "row_count",
        "metrics",
        "error",
        "duration_seconds",
    ):
        setattr(target, field_name, getattr(source, field_name))


def _snapshot(pipeline) -> dict:
    """What a pipeline was, at the moment a run started.

    Only the parts that decide what the run does. The name is in there because
    a run that says "twelve steps over Sales" is more use to a reader than one
    that says "pipe_9f2a"; the timestamps and the status are not, because they
    describe the pipeline rather than the work.
    """
    return {
        "name": pipeline.name,
        "input_dataset_id": pipeline.input_dataset_id,
        "steps": [step.to_dict() for step in pipeline.steps],
    }


class PipelineService:
    def __init__(
        self,
        *,
        repository: PipelineRepository,
        datasets: DatasetService,
        executions: ExecutionService,
        results: ResultService,
        worker: StepWorker | None = None,
        max_parallel: int = 1,
    ):
        self.repository = repository
        self.datasets = datasets
        self.executions = executions
        self.results = results
        #  How independent steps are run at the same time, when they are.
        #  Injected rather than built here: running a step in another thread
        #  needs a session of its own, and how a session is made is the
        #  composition root's business rather than a pipeline's.
        self.worker = worker
        self.max_parallel = max(1, max_parallel)

    # -- reads -------------------------------------------------------------
    def get(self, pipeline_id: str) -> Pipeline:
        pipeline = self.repository.get(pipeline_id)
        if not pipeline:
            raise NotFoundError(f"pipeline '{pipeline_id}' not found")
        return pipeline

    def list(self) -> list[Pipeline]:
        return self.repository.list()

    def get_run(self, run_id: str) -> PipelineRun:
        run = self.repository.get_run(run_id)
        if not run:
            raise NotFoundError(f"pipeline run '{run_id}' not found")
        return run

    def list_runs(self, pipeline_id: str | None = None, limit: int = 50):
        return self.repository.list_runs(pipeline_id=pipeline_id, limit=limit)

    def graph(self, pipeline_id: str) -> dict[str, Any]:
        """Nodes and edges for rendering, with the dataset as the root node."""
        pipeline = self.get(pipeline_id)
        dataset = self.datasets.get(pipeline.input_dataset_id)
        nodes = [
            {"id": "__input__", "label": dataset.name, "type": "dataset"},
            *[
                {
                    "id": step.name,
                    "label": step.name,
                    "type": "pipeline" if step.runs_pipeline else "model",
                    "model_id": step.model_id,
                    "pipeline_id": step.pipeline_id,
                    "model_name": (
                        self._nested_name(step.pipeline_id)
                        if step.runs_pipeline
                        else self._model_name(step.model_id)
                    ),
                    "kind": step.kind,
                }
                for step in pipeline.steps
            ],
        ]
        #  One edge per input, so a merging step shows both of its parents.
        edges = [
            {"from": source, "to": step.name}
            for step in pipeline.steps
            for source in (step.upstream or ["__input__"])
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "terminal_steps": [s.name for s in pipeline.terminal_steps],
        }

    # -- writes ------------------------------------------------------------
    def create(
        self,
        *,
        name: str,
        input_dataset_id: str,
        steps: list[dict],
        description: str = "",
        tags: list[str] | None = None,
    ) -> Pipeline:
        if self.repository.get_by_name(name):
            raise ConflictError(f"a pipeline named '{name}' already exists")
        self.datasets.get(input_dataset_id)

        parsed = [PipelineStep.from_dict(step) for step in steps]
        validate_steps(parsed)
        self._check_models(parsed)
        self._check_nesting(parsed, None)

        return self.repository.add(
            Pipeline(
                name=name,
                input_dataset_id=input_dataset_id,
                steps=parsed,
                description=description,
                tags=tags or [],
                status=PipelineStatus.READY,
            )
        )

    def update(self, pipeline_id: str, changes: dict[str, Any]) -> Pipeline:
        pipeline = self.get(pipeline_id)
        if changes.get("description") is not None:
            pipeline.description = changes["description"]
        if changes.get("tags") is not None:
            pipeline.tags = list(changes["tags"])
        if changes.get("input_dataset_id"):
            self.datasets.get(changes["input_dataset_id"])
            pipeline.input_dataset_id = changes["input_dataset_id"]
        if changes.get("steps") is not None:
            parsed = [PipelineStep.from_dict(step) for step in changes["steps"]]
            validate_steps(parsed)
            self._check_models(parsed)
            self._check_nesting(parsed, pipeline.id)
            pipeline.steps = parsed
        if changes.get("status"):
            pipeline.status = PipelineStatus(changes["status"])
        pipeline.updated_at = utcnow()
        return self.repository.update(pipeline)

    def delete(self, pipeline_id: str) -> None:
        self.repository.delete(self.get(pipeline_id).id)

    # -- running -----------------------------------------------------------
    def run(
        self,
        pipeline_id: str,
        *,
        dataset_version_id: str | None = None,
        triggered_by: str | None = None,
        execution_id: str | None = None,
        input_table: Table | None = None,
        depth: int = 0,
    ) -> PipelineRun:
        """Execute every step in dependency order, threading datasets through.

        A pipeline run is one unit of work: each step's output dataset is the
        next step's input, so the steps must complete in order. They therefore
        run in-process even when the deployment dispatches single executions to
        a worker - the run as a whole is what would be queued or scheduled.
        """
        pipeline = self.get(pipeline_id)
        ordered = validate_steps(pipeline.steps)
        if depth > MAX_NESTING_DEPTH:
            #  Saving a pipeline refuses a cycle, but two pipelines can be
            #  edited into one between saves. A run that would never end is
            #  worse than a run that says why it stopped.
            raise ValidationError(
                f"pipelines are nested more than {MAX_NESTING_DEPTH} deep, "
                f"starting at '{pipeline.name}'"
            )

        version_id = (
            None
            if input_table is not None
            else dataset_version_id
            or self.datasets.current_version(pipeline.input_dataset_id).id
        )

        run = self.repository.add_run(
            PipelineRun(
                pipeline_id=pipeline.id,
                status=RunStatus.RUNNING,
                input_dataset_version_id=version_id,
                #  Taken now, not read back from the pipeline later: the point
                #  is to survive the pipeline being edited afterwards.
                definition_snapshot=_snapshot(pipeline),
                triggered_by=triggered_by,
                execution_id=execution_id,
                started_at=utcnow(),
                step_runs=[
                    StepRun(step_name=s.name, model_id=s.model_id, order=index)
                    for index, s in enumerate(ordered)
                ],
            )
        )

        #  Each step's output table, so downstream steps can read it without a
        #  round trip through a Dataset that only exists to carry it.
        produced: dict[str, Table] = {}
        by_name = {step_run.step_name: step_run for step_run in run.step_runs}

        #  Wave by wave rather than step by step: everything in a wave has had
        #  its inputs produced already, so nothing in it is waiting for
        #  anything else in it. A pipeline that fans out into six independent
        #  branches used to spend its whole wall clock running them one at a
        #  time for no reason but the shape of this loop.
        for wave in waves(ordered):
            prepared = []
            for step in wave:
                step_run = by_name[step.name]
                source_table, source_version, extra, missing = self._step_inputs(
                    step, produced, version_id
                )
                if missing:
                    step_run.status = RunStatus.CANCELLED
                    step_run.error = missing
                    continue
                #  The first step reads the table a caller brought, when one
                #  was; every later step reads its upstream step as before.
                if (
                    source_table is None
                    and not step.input_from
                    and input_table is not None
                ):
                    source_table, source_version = input_table, None
                prepared.append((step, step_run, source_version, source_table, extra))

            for step, step_run in self._run_wave(pipeline, prepared, depth):
                if not step_run.result_id:
                    continue
                try:
                    result = self.results.get(step_run.result_id)
                    table = self.results.read_table(result)
                except FluxError:
                    table = None
                if table is not None:
                    produced[step.name] = table

        return self._finish(pipeline, run)

    def invoke(
        self,
        pipeline_id: str,
        *,
        dataset_version_id: str | None = None,
        dataset_id: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        depth: int = 0,
    ) -> dict[str, Any]:
        """Run a pipeline and return its output. Nothing is recorded.

        The online verb, as `POST /models/{id}/invoke` is for a model: same
        steps, same providers, same order, but no PipelineRun, no Executions,
        no Results and no datasets, because a caller wanting an answer in
        fifty milliseconds is not asking for an audit trail. `POST /pipelines/
        {id}/run` remains the recorded verb, and is what a scheduled or
        reviewable run should still use.

        `rows` is what makes this useful for serving: a pipeline built over a
        stored dataset can be applied to data the caller brings, which is the
        difference between a pipeline being a batch job and being a callable
        transformation.
        """
        outcome, metrics, logs = self._invoke_steps(
            pipeline_id,
            dataset_version_id=dataset_version_id,
            dataset_id=dataset_id,
            rows=rows,
            parameters=parameters,
            depth=depth,
        )
        return invoke_response(
            #  The last step's answer is the pipeline's answer: the terminal
            #  step is what the pipeline was built to produce.
            outcome,
            target_id=pipeline_id,
            target_type="pipeline",
            metrics=metrics,
            logs=logs,
            duration_seconds=metrics["duration_seconds"],
        )

    def _invoke_steps(
        self,
        pipeline_id: str,
        *,
        dataset_version_id: str | None = None,
        dataset_id: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        input_table: Table | None = None,
        parameters: dict[str, Any] | None = None,
        depth: int = 0,
    ) -> tuple[InvokeOutcome, dict[str, Any], list[str]]:
        """Invoking, in the shape a nested pipeline can call recursively.

        The whole payload rather than the response's first thousand rows, so a
        pipeline nested inside another passes its output on entire instead of
        a truncated copy of it.
        """
        pipeline = self.get(pipeline_id)
        ordered = validate_steps(pipeline.steps)
        if not ordered:
            raise ValidationError("this pipeline has no steps to run")
        if depth > MAX_NESTING_DEPTH:
            raise ValidationError(
                f"pipelines are nested more than {MAX_NESTING_DEPTH} deep, "
                f"starting at '{pipeline.name}'"
            )

        if rows is not None:
            input_table = Table.from_rows(rows)
        version_id: str | None = None
        if input_table is None:
            version_id = dataset_version_id or self.datasets.current_version(
                dataset_id or pipeline.input_dataset_id
            ).id

        produced: dict[str, Table] = {}
        logs: list[str] = []
        metrics: dict[str, Any] = {"steps": len(ordered), "succeeded_steps": 0}
        started = monotonic()
        outcome = None

        for step in ordered:
            source_table, source_version, extra, missing = self._step_inputs(
                step, produced, version_id
            )
            if missing:
                raise ExecutionError(f"step '{step.name}': {missing}")
            #  The first step reads the rows the caller brought, when they
            #  brought any; every later one reads its upstream step.
            if source_table is None and not step.input_from and input_table is not None:
                source_table, source_version = input_table, None

            try:
                if step.runs_pipeline:
                    #  A nested pipeline is invoked the way this one was, so
                    #  nesting behaves the same whether a run is recorded or
                    #  not - the difference a caller cares about least, and
                    #  the one most likely to diverge if written twice.
                    outcome, nested_metrics, nested_logs = self._invoke_steps(
                        step.pipeline_id,
                        dataset_version_id=source_version,
                        input_table=source_table,
                        parameters=parameters,
                        depth=depth + 1,
                    )
                    logs.extend(f"  {line}" for line in nested_logs)
                    metrics.setdefault("nested", {})[step.name] = nested_metrics
                else:
                    outcome = self.executions.invoke_once(
                        model_id=step.model_id if step.runs_library_model else None,
                        definition=(
                            None
                            if step.runs_library_model
                            else self._definition_for(step)
                        ),
                        kind=step.kind,
                        dataset_version_id=source_version,
                        input_table=source_table,
                        extra_inputs=extra,
                        parameters={**step.parameters, **(parameters or {})},
                    )
            except FluxError as exc:
                raise ExecutionError(f"step '{step.name}': {exc}") from exc

            table = outcome.payload.table
            if table is not None:
                produced[step.name] = table
            elif self._feeds_another_step(pipeline, step):
                raise ExecutionError(
                    f"step '{step.name}' did not produce a table, so no later "
                    f"step can read from it"
                )
            metrics["succeeded_steps"] += 1
            logs.append(f"step '{step.name}': succeeded")
            logs.extend(f"  {line}" for line in outcome.logs)

        metrics["duration_seconds"] = round(monotonic() - started, 4)
        return outcome, metrics, logs

    def _run_wave(
        self,
        pipeline: Pipeline,
        prepared: list[tuple[PipelineStep, StepRun, str | None, Table | None, dict]],
        depth: int,
    ) -> list[tuple[PipelineStep, StepRun]]:
        """Run one wave of steps, together when that is possible.

        Together means in threads, each with a database session of its own: a
        SQLAlchemy Session belongs to one thread, so sharing this one would
        trade a slow pipeline for a corrupted one. With no worker injected, or
        a deployment that asked for one step at a time, or one step to run,
        this is the sequential loop it has always been.
        """
        runnable = [entry for entry in prepared if entry[1].status is RunStatus.PENDING]
        #  A nested pipeline writes a run of its own through this session, so
        #  it stays in this thread whatever the rest of the wave does.
        parallel = [entry for entry in runnable if not entry[0].runs_pipeline]
        sequential = [entry for entry in runnable if entry[0].runs_pipeline]

        done: list[tuple[PipelineStep, StepRun]] = []
        if self.worker is not None and self.max_parallel > 1 and len(parallel) > 1:
            done.extend(self._run_in_parallel(pipeline, parallel, depth))
        else:
            sequential = runnable

        for step, step_run, source_version, source_table, extra in sequential:
            self._run_one(
                pipeline, step, step_run, source_version, source_table, extra, depth
            )
            done.append((step, step_run))
        return done

    def _run_in_parallel(
        self,
        pipeline: Pipeline,
        entries: list[tuple[PipelineStep, StepRun, str | None, Table | None, dict]],
        depth: int,
    ) -> list[tuple[PipelineStep, StepRun]]:
        done: list[tuple[PipelineStep, StepRun]] = []
        with ThreadPoolExecutor(
            max_workers=min(self.max_parallel, len(entries)),
            thread_name_prefix="flux-step",
        ) as pool:
            futures = {
                pool.submit(
                    self.worker,
                    pipeline.id,
                    step.name,
                    step_run.order,
                    source_version,
                    source_table,
                    extra,
                    depth,
                ): (step, step_run)
                for step, step_run, source_version, source_table, extra in entries
            }
            for future in as_completed(futures):
                step, step_run = futures[future]
                try:
                    finished = future.result()
                except Exception as exc:
                    logger.exception("pipeline step %s failed", step.name)
                    step_run.status = RunStatus.FAILED
                    step_run.error = f"{type(exc).__name__}: {exc}"
                else:
                    #  The worker ran in a session of its own, so what comes
                    #  back is a record to copy rather than this same object.
                    _copy_step_run(finished, step_run)
                done.append((step, step_run))
        return done

    def _run_one(
        self,
        pipeline: Pipeline,
        step: PipelineStep,
        step_run: StepRun,
        source_version: str | None,
        source_table: Table | None,
        extra: dict[str, Table],
        depth: int,
    ) -> None:
        try:
            self._run_step(
                pipeline,
                step,
                step_run,
                source_version,
                source_table,
                extra,
                depth=depth,
            )
        except Exception as exc:
            logger.exception("pipeline step %s failed", step.name)
            step_run.status = RunStatus.FAILED
            step_run.error = f"{type(exc).__name__}: {exc}"

    def run_step_standalone(
        self,
        pipeline_id: str,
        step_name: str,
        order: int,
        source_version_id: str | None,
        source_table: Table | None,
        extra_inputs: dict[str, Table],
        depth: int,
    ) -> StepRun:
        """Run one step of a pipeline and hand back what happened to it.

        The entry point a parallel worker calls in its own session. It writes
        the execution, the result and any dataset the step produces, exactly as
        the sequential path does; what it does not touch is the PipelineRun,
        which stays the parent thread's to write.
        """
        pipeline = self.get(pipeline_id)
        step = pipeline.step(step_name)
        if step is None:
            raise NotFoundError(
                f"pipeline '{pipeline_id}' has no step named '{step_name}'"
            )
        step_run = StepRun(step_name=step.name, model_id=step.model_id, order=order)
        self._run_one(
            pipeline,
            step,
            step_run,
            source_version_id,
            source_table,
            extra_inputs,
            depth,
        )
        return step_run

    def _step_inputs(
        self,
        step: PipelineStep,
        produced: dict[str, Table],
        version_id: str | None,
    ) -> tuple[Table | None, str | None, dict[str, Table], str | None]:
        """What a step reads, and why it cannot run when it reads nothing.

        Shared by the recorded run and by invoking, so the two cannot disagree
        about which table a step is given - a disagreement that would show up
        as one of them quietly computing the wrong answer.
        """
        if step.input_from:
            source_table = produced.get(step.input_from)
            source_version = None
            if source_table is None:
                return None, None, {}, (
                    f"upstream step '{step.input_from}' produced no table"
                )
        else:
            source_table, source_version = None, version_id

        #  A merging step reads several inputs by name: earlier steps, and
        #  datasets that are not part of this chain at all. Missing one is
        #  reported as the missing name rather than as an empty join.
        extra = {name: produced.get(other) for name, other in step.inputs.items()}
        for name, dataset_id in step.input_datasets.items():
            try:
                version = self.datasets.current_version(dataset_id)
                extra[name] = self.datasets.read_table(version.id)
            except FluxError:
                extra[name] = None
        absent = [name for name, table in extra.items() if table is None]
        if absent:
            return None, None, {}, (
                f"step '{step.name}' is missing input(s) "
                f"{absent}: those upstream steps produced no table"
            )
        return source_table, source_version, extra, None

    def _run_step(
        self,
        pipeline: Pipeline,
        step: PipelineStep,
        step_run: StepRun,
        source_version_id: str | None,
        source_table: Table | None = None,
        extra_inputs: dict[str, Table] | None = None,
        depth: int = 0,
    ) -> None:
        step_run.status = RunStatus.RUNNING
        if step.runs_pipeline:
            self._run_nested(step, step_run, source_version_id, source_table, depth)
            return
        execution = self.executions.submit(
            model_id=step.model_id,
            definition=None if step.runs_library_model else self._definition_for(step),
            kind=step.kind,
            dataset_version_id=source_version_id,
            input_table=source_table,
            extra_inputs=extra_inputs,
            parameters=step.parameters,
            #  This step's output is the next step's input, so the chain cannot
            #  continue until it finishes. Stated per call rather than by
            #  changing how the execution service behaves from now on.
            force_inline=True,
            #  A step whose output nothing else reads is what the pipeline was
            #  built to produce, so it becomes a Dataset. An intermediate is
            #  working state and stays a checkpoint: publishing every one of
            #  them is what made a twelve-step pipeline add twelve datasets to
            #  a list nobody wanted them in.
            materialise_datasets=step.materialise
            or not self._feeds_another_step(pipeline, step),
            context={
                "pipeline_id": pipeline.id,
                "pipeline_name": pipeline.name,
                "step": step.name,
            },
        )
        step_run.execution_id = execution.id
        step_run.metrics = execution.metrics
        step_run.duration_seconds = execution.duration_seconds

        if execution.status.value != "succeeded":
            step_run.status = RunStatus.FAILED
            step_run.error = execution.error or f"execution {execution.status.value}"
            return

        result = self.results.for_execution(execution.id)
        if result:
            step_run.result_id = result.id
            step_run.dataset_id = result.dataset_id
            step_run.dataset_version_id = result.dataset_version_id
            step_run.row_count = result.row_count

        if self._feeds_another_step(pipeline, step) and not step_run.result_id:
            #  The chain can only continue through something tabular, so say so
            #  plainly rather than failing later with a confusing "no input".
            step_run.status = RunStatus.FAILED
            step_run.error = (
                f"step '{step.name}' did not produce a table, so no later step "
                f"can read from it"
            )
            return

        step_run.status = RunStatus.SUCCEEDED

    def _run_nested(
        self,
        step: PipelineStep,
        step_run: StepRun,
        source_version_id: str | None,
        source_table: Table | None,
        depth: int,
    ) -> None:
        """Run another pipeline as one step of this one.

        The nested run is an ordinary recorded run: it has its own row, its own
        step runs and its own outputs, and the step points at it. Flattening it
        into the parent would be tidier to read and would lose the thing that
        makes nesting worth having - that the shared pipeline is one pipeline,
        run and reviewed the same way wherever it is used.
        """
        nested = self.run(
            step.pipeline_id,
            dataset_version_id=source_version_id,
            input_table=source_table,
            triggered_by=f"pipeline step '{step.name}'",
            depth=depth + 1,
        )
        step_run.pipeline_run_id = nested.id
        step_run.duration_seconds = nested.duration_seconds
        step_run.metrics = {
            "steps": len(nested.step_runs),
            "succeeded_steps": nested.succeeded_steps,
        }

        if nested.status is not RunStatus.SUCCEEDED:
            step_run.status = RunStatus.FAILED
            step_run.error = (
                nested.error or f"the nested pipeline run {nested.id} did not finish"
            )
            return

        #  What the nested pipeline produced is what this step produced, so the
        #  parent's next step reads it exactly as it would read a model's
        #  output. Its last finished step is the one that carries it.
        produced_by = next(
            (
                inner
                for inner in reversed(nested.step_runs)
                if inner.status is RunStatus.SUCCEEDED and inner.result_id
            ),
            None,
        )
        if produced_by is not None:
            step_run.result_id = produced_by.result_id
            step_run.dataset_id = produced_by.dataset_id
            step_run.dataset_version_id = produced_by.dataset_version_id
            step_run.row_count = produced_by.row_count
        step_run.status = RunStatus.SUCCEEDED

    def _finish(self, pipeline: Pipeline, run: PipelineRun) -> PipelineRun:
        failed = [s for s in run.step_runs if s.status is RunStatus.FAILED]
        run.status = RunStatus.FAILED if failed else RunStatus.SUCCEEDED
        run.error = failed[0].error if failed else None
        run.finished_at = utcnow()
        run.output_dataset_ids = [
            step_run.dataset_id
            for step in pipeline.terminal_steps
            for step_run in run.step_runs
            if step_run.step_name == step.name and step_run.dataset_id
        ]
        self.repository.update_run(run)
        self._name_deliverables(pipeline, run, set(run.output_dataset_ids))

        pipeline.last_run_id = run.id
        pipeline.last_run_status = run.status.value
        pipeline.updated_at = utcnow()
        self.repository.update(pipeline)
        return run

    def _name_deliverables(
        self, pipeline: Pipeline, run: PipelineRun, deliverables: set[str]
    ) -> None:
        """Name a pipeline's output after the pipeline, not after its last step.

        A result dataset is named for whatever produced it, which is right for
        a standalone run and wrong for a pipeline: a twelve-step chain produced
        "… · trim narrative columns result", naming an implementation detail of
        the final stage. What a reader is looking for is the pipeline's output.

        This is the only naming pass left. Its sibling used to walk back over
        every dataset the run had created and demote the ones nobody wanted -
        which was necessary only because the run created them in the first
        place. Intermediates are checkpoints now, so there is nothing to
        demote.
        """
        terminal = {step.name for step in pipeline.terminal_steps}
        multiple = len(terminal) > 1
        for step_run in run.step_runs:
            if step_run.dataset_id not in deliverables:
                continue
            if step_run.step_name not in terminal:
                continue
            try:
                dataset = self.datasets.get(step_run.dataset_id)
            except NotFoundError:
                continue
            #  Only rename what this method named; a dataset somebody titled
            #  themselves is theirs to keep.
            if not dataset.name.endswith(" result"):
                continue
            wanted = (
                f"{pipeline.name} · {step_run.step_name}"
                if multiple
                else f"{pipeline.name} output"
            )
            if dataset.name != wanted and not self.datasets.datasets.get_by_name(wanted):
                dataset.name = wanted
                #  Replace, not fall back: the auto-written "materialised from
                #  execution exec_…" is the id of a run nobody is looking for.
                #  The execution stays on the dataset's lineage either way.
                dataset.description = f"Produced by the '{pipeline.name}' pipeline."
                self.datasets.datasets.update(dataset)

    # -- internals ---------------------------------------------------------
    def _definition_for(self, step: PipelineStep) -> ModelDefinition:
        """Turn a step into the definition that will run it.

        Built fresh each time rather than stored: the step *is* the definition,
        so there is nothing to keep in sync. A provider that rejects it says so
        when the pipeline is saved, not when it is run.
        """
        plugin = self.executions.registry.get(step.provider)
        descriptor = plugin.describe()
        definition = ModelDefinition(
            name=step.name,
            slug=step.name,
            provider=step.provider,
            type=descriptor.model_type,
            runtime=descriptor.runtime,
            description=step.description,
            configuration=step.configuration,
            input_contract=descriptor.input_contract,
            parameter_contract=descriptor.parameter_contract,
            output_contract=descriptor.output_contract,
        )
        validation = plugin.validate(definition)
        if not validation.valid:
            raise ValidationError(
                f"step '{step.name}' is not a valid {step.provider} configuration",
                details=validation.to_dict(),
            )
        return definition

    @staticmethod
    def _feeds_another_step(pipeline: Pipeline, step: PipelineStep) -> bool:
        """Whether anything downstream reads this step's output."""
        return any(step.name in other.upstream for other in pipeline.steps)

    def _check_models(self, steps: list[PipelineStep]) -> None:
        """Every step must be runnable before the pipeline is saved.

        A step that names a library model is checked against that model; an
        inline step is checked by building its definition and asking the
        provider. Either way the answer arrives while somebody is editing,
        which is the only time it is useful.
        """
        for step in steps:
            if step.runs_pipeline:
                #  Nothing to ask a provider: what a nested step runs is a
                #  pipeline, and a pipeline was checked when it was saved.
                continue
            if step.runs_library_model:
                model = self.executions.models.get(step.model_id)
                #  Resolve the id now so the stored pipeline never points at a
                #  slug that could later resolve differently.
                step.model_id = model.id
                supported = [
                    k.value for k in self.executions.models.supported_kinds(model.id)
                ]
                label = f"model '{model.name}'"
            else:
                definition = self._definition_for(step)
                descriptor = self.executions.registry.get(step.provider).describe()
                supported = [k.value for k in descriptor.supported_kinds]
                label = f"provider '{definition.provider}'"

            if step.kind and step.kind not in supported:
                raise ValidationError(
                    f"step '{step.name}': {label} does not support "
                    f"'{step.kind}' executions",
                    details={"supported": supported},
                )

    def _check_nesting(self, steps: list[PipelineStep], pipeline_id: str | None) -> None:
        """Every nested pipeline must exist, and none may lead back here.

        Checked over the whole reachable graph rather than one level down: A
        nesting B nesting A is the same loop as A nesting A, and only the
        second one is obvious while editing.
        """
        seen: set[str] = set()
        frontier = [step.pipeline_id for step in steps if step.runs_pipeline]
        for step in steps:
            if step.runs_pipeline and step.pipeline_id == pipeline_id:
                raise ValidationError(
                    f"step '{step.name}' runs this pipeline, which would never end"
                )
        while frontier:
            nested_id = frontier.pop()
            if nested_id in seen:
                continue
            seen.add(nested_id)
            try:
                nested = self.get(nested_id)
            except NotFoundError:
                raise ValidationError(
                    f"a step runs pipeline '{nested_id}', which does not exist"
                ) from None
            if pipeline_id and nested.id == pipeline_id:
                raise ValidationError(
                    f"pipeline '{nested.name}' runs this one, so nesting it "
                    f"here would never end"
                )
            frontier.extend(
                inner.pipeline_id for inner in nested.steps if inner.runs_pipeline
            )
        if len(seen) > MAX_NESTING_DEPTH:
            raise ValidationError(
                f"this would nest more than {MAX_NESTING_DEPTH} pipelines"
            )

    def _nested_name(self, pipeline_id: str | None) -> str:
        if not pipeline_id:
            return ""
        try:
            return self.get(pipeline_id).name
        except NotFoundError:
            return pipeline_id

    def _model_name(self, model_id: str) -> str:
        try:
            return self.executions.models.get(model_id).name
        except NotFoundError:
            return model_id
