"""Execution orchestration.

The single path every model takes, whatever its type:

    input -> validation -> execution -> output

A training run and a formula calculation differ only in which plugin method is
called and whether a new Model Version comes out the other end.
"""

from __future__ import annotations

import logging
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.observability import metrics
from app.modules.data.application.services import DatasetService
from app.modules.model.application.services import ModelService
from app.modules.model.domain.entities import (
    ModelDefinition,
    definition_from_snapshot,
)
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionInput,
    ExecutionKind,
    is_trainable,
)
from app.modules.model.domain.registry import PluginRegistry
from app.modules.results.application.services import ResultService
from app.shared.errors import (
    ExecutionError,
    NotFoundError,
    UnsupportedError,
    ValidationError,
)
from app.shared.ids import utcnow
from app.shared.payloads import ResultPayload
from app.shared.storage import ObjectStore
from app.shared.tabular import Table

from ..domain.entities import Execution, ExecutionStatus, RunnableKind
from ..domain.ports import (
    ExecutionDispatcher,
    ExecutionRepository,
    RunInline,
    RunnableRunner,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InvokeOutcome:
    """What one synchronous run produced, before it is shaped into a response.

    The payload rather than its first thousand rows, so a caller that is
    chaining - a pipeline invoking its steps - passes the whole table on
    instead of a truncated copy of it.
    """

    payload: ResultPayload
    metrics: dict[str, Any]
    logs: list[str]
    kind: ExecutionKind
    model_version_id: str | None = None
    duration_seconds: float = 0.0


def invoke_response(
    outcome: InvokeOutcome,
    *,
    target_id: str,
    target_type: str,
    metrics: dict[str, Any] | None = None,
    logs: list[str] | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """The body every `…/invoke` answers with, whatever kind of runnable ran.

    One shape for models and pipelines alike: an integration that can read the
    answer from one can read it from the other, which is the whole point of
    calling both of them runnables.
    """
    payload = outcome.payload
    table = payload.table
    return {
        "target_id": target_id,
        "target_type": target_type,
        #  Empty for anything that is not a model, rather than borrowed: a
        #  caller filtering on model_id must not match a pipeline.
        "model_id": target_id if target_type == RunnableKind.MODEL.value else None,
        "model_version_id": outcome.model_version_id,
        "kind": outcome.kind.value,
        "result_kind": payload.kind.value,
        #  The answer itself, in whichever shape the payload carries.
        "value": payload.value,
        "rows": table.to_rows(limit=INVOKE_MAX_ROWS) if table is not None else None,
        #  A caller that asked for an answer in fifty milliseconds is not
        #  asking for a hundred thousand rows in the response body. When a run
        #  produces more than fits here, the honest answer is the first page
        #  plus the count - and `POST /executions`, which keeps the whole
        #  result and can materialise it as a dataset.
        "row_count": table.num_rows if table is not None else None,
        "truncated": bool(table is not None and table.num_rows > INVOKE_MAX_ROWS),
        "summary": payload.summary,
        "metrics": outcome.metrics if metrics is None else metrics,
        "logs": outcome.logs if logs is None else logs,
        "duration_seconds": (
            outcome.duration_seconds if duration_seconds is None else duration_seconds
        ),
    }

#  How much of a table a synchronous call carries back. Enough for the shapes
#  invoke is for - a classification, a forecast, a scored list - and small
#  enough that one caller cannot turn a serving endpoint into a bulk export.
INVOKE_MAX_ROWS = 1_000


class ExecutionService:
    def __init__(
        self,
        *,
        repository: ExecutionRepository,
        models: ModelService,
        datasets: DatasetService,
        results: ResultService,
        registry: PluginRegistry,
        store: ObjectStore,
        dispatcher: ExecutionDispatcher | None = None,
        runners: dict[str, RunnableRunner] | None = None,
    ):
        self.repository = repository
        self.models = models
        self.datasets = datasets
        self.results = results
        self.registry = registry
        self.store = store
        #  Default to running in-process; the container injects the queue
        #  dispatcher when FLUX_EXECUTION_MODE=queue.
        self.dispatcher = dispatcher or RunInline()
        #  What can be run besides a model, keyed by `RunnableKind`. Empty by
        #  default: a deployment that never wired a pipeline runner simply
        #  cannot run one, and says so, rather than importing one from a layer
        #  above.
        self.runners: dict[str, RunnableRunner] = dict(runners or {})
        settings = get_settings()
        self.timeout_seconds = settings.execution_timeout_seconds
        self.max_attempts = settings.execution_max_attempts

    # -- reads -------------------------------------------------------------
    def get(self, execution_id: str) -> Execution:
        execution = self.repository.get(execution_id)
        if not execution:
            raise NotFoundError(f"execution '{execution_id}' not found")
        return execution

    def list(self, **filters) -> list[Execution]:
        return self.repository.list(**filters)

    def cancel(self, execution_id: str) -> Execution:
        """Stop an execution, or ask it to stop.

        A PENDING execution has not started, so cancelling it is immediate. A
        RUNNING one is somewhere inside a plugin call, and the honest thing is
        to record the request and let the runner act on it: marking it
        cancelled here is what used to happen, and the worker then finished the
        work and wrote `succeeded` straight over the top.
        """
        execution = self.get(execution_id)
        if execution.status.is_terminal:
            raise ValidationError(
                f"execution is already {execution.status.value} and cannot be cancelled"
            )
        if execution.status is ExecutionStatus.RUNNING:
            execution.request_cancel()
            execution.logs.append("cancellation requested")
            return self.repository.update(execution)
        execution.mark_cancelled("cancelled before it started")
        return self.repository.update(execution)

    # -- submit ------------------------------------------------------------
    def submit(
        self,
        *,
        model_id: str | None = None,
        pipeline_id: str | None = None,
        definition: ModelDefinition | None = None,
        kind: str | None = None,
        dataset_version_id: str | None = None,
        dataset_id: str | None = None,
        input_payload: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        model_version_id: str | None = None,
        experiment_id: str | None = None,
        force_inline: bool = False,
        input_table: Table | None = None,
        extra_inputs: dict[str, Table] | None = None,
        materialise_datasets: bool | None = None,
    ) -> Execution:
        """Create an execution and run it. Returns the terminal execution.

        `force_inline` is for a caller that cannot continue without the answer -
        a pipeline step whose output is the next step's input. It applies to
        this call only. Previously such callers overwrote the service's
        dispatcher, which changed the behaviour of every later submit on the
        same instance and never changed it back.
        """
        #  A pipeline is the other kind of runnable. It takes the same route -
        #  an Execution row, a dispatcher, a Result - and differs only in who
        #  does the work, which is why it can be scheduled, compared and served
        #  by everything that already knew how to do those things to a model.
        if pipeline_id:
            return self._submit_pipeline(
                pipeline_id,
                dataset_version_id=dataset_version_id,
                dataset_id=dataset_id,
                parameters=parameters,
                context=context,
                experiment_id=experiment_id,
                force_inline=force_inline,
            )

        #  Either a model from the library, or a definition supplied by the
        #  caller. The rest of this method cannot tell the difference, which is
        #  the point: an inline definition is not a second execution path.
        if definition is None:
            if not model_id:
                raise ValidationError(
                    "an execution needs either a model_id or a definition"
                )
            model = self.models.get(model_id)
        else:
            model = definition
        plugin = self.registry.get(model.provider)
        descriptor = plugin.describe()

        execution_kind = self._resolve_kind(kind, descriptor.supported_kinds)
        if execution_kind is ExecutionKind.TRAINING and not is_trainable(plugin):
            raise UnsupportedError(
                f"model '{model.name}' is executable but not trainable "
                f"(provider '{model.provider}' has no training step)"
            )

        inline = definition is not None
        version_id = None if inline else (model_version_id or model.current_version_id)
        resolved_dataset_version = self._resolve_dataset_version_id(
            dataset_version_id, dataset_id
        )

        execution = self.repository.add(
            Execution(
                target_id=None if inline else model.id,
                model_version_id=version_id,
                definition_snapshot=_snapshot_of(model) if inline else {},
                kind=execution_kind,
                dataset_version_id=resolved_dataset_version,
                input_payload=input_payload or {},
                parameters=parameters or {},
                context=context or {},
                runtime=model.runtime.value,
                experiment_id=experiment_id,
                lineage={
                    "model_id": None if inline else model.id,
                    "model_name": model.name,
                    "model_slug": model.slug,
                    "model_version_id": version_id,
                    "provider": model.provider,
                    "dataset_version_id": resolved_dataset_version,
                },
            )
        )

        if force_inline or self.dispatcher.runs_inline:
            return self.run(
                execution.id,
                input_table=input_table,
                extra_inputs=extra_inputs,
                materialise_datasets=materialise_datasets,
            )

        #  Queued mode: the caller gets a pending execution back and polls, or
        #  watches the Executions page, while a worker does the work.
        self.dispatcher.enqueue(execution.id)
        execution.logs.append(f"queued for the {self.dispatcher.mode} worker")
        return self.repository.update(execution)

    def _definition_for(
        self, execution: Execution, model: ModelDefinition
    ) -> ModelDefinition:
        """The definition this execution should run.

        A version used to be a label on a mutable row: the execution recorded a
        `model_version_id`, and then the plugin was handed the live model. Edit
        the configuration and re-run the same version, and the answer changed —
        two executions, one version, different results, with nothing in the
        record to show why.

        A version is the definition. When an execution names one, that snapshot
        is what runs; the model row supplies identity only. Executions that name
        no version run the model as it stands, which is what a draft is for.
        """
        if not execution.model_version_id:
            return model
        try:
            version = self.models.get_version(execution.model_version_id)
        except NotFoundError:
            execution.logs.append(
                f"version {execution.model_version_id} is gone; ran the current definition"
            )
            return model
        if version.model_id != model.id or not version.definition_snapshot:
            return model
        execution.logs.append(f"running model version {version.version}")
        return definition_from_snapshot(model, version.definition_snapshot)

    # -- serve -------------------------------------------------------------
    def _submit_pipeline(
        self,
        pipeline_id: str,
        *,
        dataset_version_id: str | None = None,
        dataset_id: str | None = None,
        parameters: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        experiment_id: str | None = None,
        force_inline: bool = False,
    ) -> Execution:
        """Record and dispatch a pipeline run as an ordinary execution."""
        if RunnableKind.PIPELINE.value not in self.runners:
            raise UnsupportedError(
                "this deployment cannot run pipelines as executions"
            )
        execution = self.repository.add(
            Execution(
                target_id=pipeline_id,
                target_type=RunnableKind.PIPELINE,
                kind=ExecutionKind.TRANSFORMATION,
                dataset_version_id=self._resolve_dataset_version_id(
                    dataset_version_id, dataset_id
                ),
                parameters=parameters or {},
                context=context or {},
                runtime="python",
                experiment_id=experiment_id,
                lineage={"pipeline_id": pipeline_id},
            )
        )
        if force_inline or self.dispatcher.runs_inline:
            return self.run(execution.id)
        self.dispatcher.enqueue(execution.id)
        return execution

    def invoke(
        self,
        *,
        model_id: str,
        kind: str | None = None,
        input_payload: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        dataset_version_id: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a model and return its answer, as the serving endpoint reports it."""
        outcome = self.invoke_once(
            model_id=model_id,
            kind=kind,
            input_payload=input_payload,
            parameters=parameters,
            dataset_version_id=dataset_version_id,
            dataset_id=dataset_id,
        )
        return invoke_response(
            outcome,
            target_id=model_id,
            target_type=RunnableKind.MODEL.value,
        )

    def invoke_once(
        self,
        *,
        model_id: str | None = None,
        definition: ModelDefinition | None = None,
        kind: str | None = None,
        input_payload: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        dataset_version_id: str | None = None,
        dataset_id: str | None = None,
        input_table: Table | None = None,
        extra_inputs: dict[str, Table] | None = None,
    ) -> InvokeOutcome:
        """Run one model once and hand back what it produced. Nothing is recorded.

        The same resolution, the same contracts and the same plugin call as
        `submit`; what is missing is the Execution row, the Result row and the
        dataset materialisation, because none of them is what a caller wanting
        an answer in fifty milliseconds is asking for.

        Training is refused: it publishes an immutable version, which is a
        change to the platform's state and therefore not something a read-shaped
        endpoint should do.
        """
        #  A caller may name a stored model or hand over a definition made on
        #  the spot - a pipeline step is the second kind, and invoking a
        #  pipeline has to be able to run one.
        model = self.models.get(model_id) if model_id else None
        if definition is None:
            if model is None:
                raise ValidationError("invoking needs a model id or a definition")
            definition = model
        plugin = self.registry.get(definition.provider)
        descriptor = plugin.describe()

        #  A caller has a dataset id far more often than a version id, and
        #  `submit` has always accepted either. Resolved the same way here, so
        #  the two verbs do not disagree about what an input is.
        dataset_version_id = self._resolve_dataset_version_id(
            dataset_version_id, dataset_id
        )

        execution_kind = self._resolve_kind(kind, descriptor.supported_kinds)
        if execution_kind is ExecutionKind.TRAINING:
            raise UnsupportedError(
                "training changes the model, so it cannot be invoked; "
                "submit it as an execution instead"
            )

        #  A definition object, not a row: invoking runs the current published
        #  version, exactly as an unpinned execution would.
        version = self.models.current_version(model.id) if model else None
        if model is not None and definition is model:
            definition = (
                definition_from_snapshot(model, version.definition_snapshot)
                if version and version.definition_snapshot
                else model
            )

        stand_in = Execution(
            target_id=model.id if model else None,
            kind=execution_kind,
            dataset_version_id=dataset_version_id,
            input_payload=input_payload or {},
            parameters=parameters or {},
        )
        validated = self._validated_parameters(definition, stand_in.parameters)
        execution_input = self._build_input(
            definition, stand_in, table=input_table, extra=extra_inputs
        )

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="flux-invoke-") as workdir:
            ctx = ExecutionContext(
                execution_id="invoke",
                kind=execution_kind,
                definition=definition,
                input=execution_input,
                parameters=validated,
                artifact_path=self._artifact_path(
                    version.id if version else None
                ),
                workdir=workdir,
                deadline=time.monotonic() + self._deadline_for(descriptor),
                datasets=self._required_datasets(descriptor),
            )
            outcome = plugin.execute(ctx)

        return InvokeOutcome(
            payload=outcome.payload,
            metrics=outcome.metrics,
            logs=ctx.logs + outcome.logs,
            kind=execution_kind,
            model_version_id=version.id if version else None,
            duration_seconds=round(time.monotonic() - started, 4),
        )

    # -- run ---------------------------------------------------------------
    def run(
        self,
        execution_id: str,
        *,
        input_table: Table | None = None,
        extra_inputs: dict[str, Table] | None = None,
        materialise_datasets: bool | None = None,
    ) -> Execution:
        execution = self.get(execution_id)
        if execution.status.is_terminal:
            return execution
        if execution.exhausted(self.max_attempts):
            #  Tried as many times as it is going to be. Saying so is better
            #  than being picked up by every recovery sweep from now on.
            execution.mark_failed(
                f"gave up after {execution.attempts} attempts without finishing"
            )
            return self.repository.update(execution)
        if execution.cancel_requested:
            #  Asked to stop before anyone picked it up.
            execution.mark_cancelled("cancelled before it started")
            return self.repository.update(execution)

        #  Claimed in one statement rather than marked running after a check.
        #  Two workers can hold the same id - the recovery sweep re-queues an
        #  execution that is merely slow to be picked up - and the loser has to
        #  find out from the database, not from a read it did earlier.
        #
        #  Claimed before anything is resolved, so that the row this method
        #  works on is the one it owns. Resolving first and claiming after
        #  would throw away whatever the resolution had already written to the
        #  entity - the version it chose, for one.
        claimed = self.repository.claim(execution.id)
        if claimed is None:
            logger.info(
                "execution %s is already claimed by another worker", execution.id
            )
            return self.get(execution.id)
        execution = claimed

        if execution.target_type is not RunnableKind.MODEL:
            return self._run_runnable(execution)

        model = None
        try:
            #  Inside the try from here on: a model that has been deleted or a
            #  provider that is no longer registered is a failed execution, not
            #  an exception out of a worker loop.
            if execution.model_id:
                model = self._definition_for(
                    execution, self.models.get(execution.model_id)
                )
            else:
                #  An inline definition: what ran is on the execution itself.
                model = definition_from_snapshot(
                    _placeholder_definition(execution), execution.definition_snapshot
                )
            plugin = self.registry.get(model.provider)
            descriptor = plugin.describe()

            self._note_provider_version(execution, descriptor)
            parameters = self._validated_parameters(model, execution.parameters)
            execution_input = self._build_input(
                model, execution, table=input_table, extra=extra_inputs
            )

            with tempfile.TemporaryDirectory(prefix="flux-exec-") as workdir:
                ctx = ExecutionContext(
                    execution_id=execution.id,
                    kind=execution.kind,
                    definition=model,
                    input=execution_input,
                    parameters=parameters,
                    context=execution.context,
                    artifact_path=self._artifact_path(execution.model_version_id),
                    workdir=workdir,
                    should_cancel=lambda: self._cancel_requested(execution.id),
                    deadline=time.monotonic() + self._deadline_for(descriptor),
                    datasets=self._required_datasets(descriptor),
                )

                if execution.kind is ExecutionKind.TRAINING:
                    execution = self._run_training(execution, model, plugin, ctx)
                else:
                    execution = self._run_execution(
                        execution, model, plugin, ctx,
                        materialise_datasets=materialise_datasets,
                    )
        except Exception as exc:  # surfaced to the user as a failed execution
            execution.logs.append(traceback.format_exc(limit=8))
            execution.mark_failed(f"{type(exc).__name__}: {exc}")
            self._observe(execution, model.provider if model else "unknown")
            self.repository.update(execution)
            if isinstance(exc, (ValidationError, UnsupportedError, NotFoundError)):
                raise
            raise ExecutionError(str(exc), details={"execution_id": execution.id}) from exc

        self._observe(execution, model.provider)
        return self.repository.update(execution)

    def _deadline_for(self, descriptor) -> int:
        """How long this provider gets.

        A provider that knows it needs longer says so; everything else takes
        the platform default. One number for a formula and a leave-one-out
        backtest is either too tight to be safe or too loose to be useful.
        """
        return int(getattr(descriptor, "timeout_seconds", None) or self.timeout_seconds)

    def _note_provider_version(self, execution: Execution, descriptor) -> None:
        """Record which provider version ran, and say when it has moved.

        Reproducibility was guaranteed as far as the definition and no
        further: the same pinned version, re-run after scikit-learn changed,
        gives a different answer and the record could not say why. It says so
        now - in the lineage, where the rest of "what produced this" lives.
        """
        running = str(getattr(descriptor, "version", "1"))
        recorded = (execution.lineage or {}).get("provider_version")
        execution.lineage = {**(execution.lineage or {}), "provider_version": running}
        if recorded and recorded != running:
            #  Not an error: re-running an old execution on a newer provider is
            #  a legitimate thing to do. It just must not look identical to
            #  having run it then.
            execution.logs.append(
                f"provider '{descriptor.key}' is version {running}; this "
                f"execution previously ran on {recorded}"
            )

    def _run_runnable(self, execution: Execution) -> Execution:
        """Run something that is not a model, through its injected runner.

        Everything after the runner returns is the path every execution takes:
        the outcome is persisted as a Result, metrics and logs land on the row,
        cancellation is checked before anything is written. Only the doing of
        the work differs, which is the whole point of naming the target.
        """
        runner = self.runners.get(execution.target_type.value)
        if runner is None:
            execution.mark_failed(
                f"nothing in this deployment can run a "
                f"{execution.target_type.value}"
            )
            return self.repository.update(execution)

        try:
            outcome = runner(execution)
        except Exception as exc:  # surfaced to the user as a failed execution
            execution.logs.append(traceback.format_exc(limit=8))
            execution.mark_failed(f"{type(exc).__name__}: {exc}")
            self._observe(execution, execution.target_type.value)
            self.repository.update(execution)
            if isinstance(exc, (ValidationError, UnsupportedError, NotFoundError)):
                raise
            raise ExecutionError(str(exc), details={"execution_id": execution.id}) from exc

        if self._cancel_requested(execution.id):
            #  Checked before the Result is written, for the same reason the
            #  model path checks it there: a cancelled execution pointing at a
            #  stored result reads as a UI glitch and is not one.
            execution.mark_cancelled("cancelled while running")
            return self.repository.update(execution)

        execution.logs.extend(outcome.logs)
        result = self.results.persist(
            execution_id=execution.id,
            payload=outcome.payload,
            lineage=execution.lineage,
        )
        execution.mark_succeeded(
            result_id=result.id,
            metrics={**execution.metrics, **outcome.metrics},
        )
        self._observe(execution, execution.target_type.value)
        return self.repository.update(execution)

    def _observe(self, execution: Execution, provider: str) -> None:
        """Report the run to the metric registry.

        The platform is about executions, so this is the histogram worth
        having: HTTP latency says nothing about a run that happened in a
        worker, which is where most of them happen.
        """
        try:
            metrics.observe_execution(
                provider=provider,
                kind=execution.kind.value,
                status=execution.status.value,
                seconds=execution.duration_seconds or 0.0,
            )
        except Exception:  # noqa: BLE001 - measurement must not break the thing
            logger.debug("could not record execution metrics", exc_info=True)

    def definition_for(self, execution: Execution) -> ModelDefinition:
        """What this execution runs, wherever the definition came from."""
        if execution.model_id:
            return self._definition_for(execution, self.models.get(execution.model_id))
        return definition_from_snapshot(
            _placeholder_definition(execution), execution.definition_snapshot
        )

    def _required_datasets(self, descriptor) -> dict[str, Any]:
        """Read the datasets a provider declared, by name.

        A missing optional dataset is simply absent; a missing required one is
        an error the provider would otherwise raise deep inside its own code,
        where the message would name a file path instead of a dataset.
        """
        resolved: dict[str, Any] = {}
        for required in getattr(descriptor, "required_datasets", ()):
            dataset = self.datasets.datasets.get_by_name(required.name)
            if dataset is None or not dataset.current_version_id:
                if required.required:
                    raise NotFoundError(
                        f"this model needs the '{required.name}' dataset, which "
                        f"this workspace does not have"
                    )
                continue
            resolved[required.key] = self.datasets.read_table(
                dataset.current_version_id
            )
        return resolved

    def _cancel_requested(self, execution_id: str) -> bool:
        """Re-read the flag: the request arrives from another transaction.

        Deliberately a fresh read rather than the in-memory object, because the
        whole point is that somebody else set it after this run started.
        """
        current = self.repository.get(execution_id)
        return bool(current and current.cancel_requested)

    def beat(self, execution_id: str) -> None:
        """Record that the worker holding this execution is still alive."""
        execution = self.repository.get(execution_id)
        if execution and execution.status is ExecutionStatus.RUNNING:
            execution.beat()
            self.repository.update(execution)

    def reclaim_stale(self, *, after_seconds: int, limit: int = 50) -> list[Execution]:
        """Fail RUNNING executions whose worker stopped reporting in.

        Recovery used to sweep only PENDING rows, so a worker that was killed
        mid-execution left the row RUNNING for ever - visible on the executions
        page as something perpetually in progress that nothing would ever
        finish.
        """
        now = utcnow()
        reclaimed = []
        for execution in self.repository.list(status="running", limit=limit):
            if not execution.is_stale(now=now, after_seconds=after_seconds):
                continue
            execution.logs.append(
                f"no heartbeat for over {after_seconds}s; the worker running "
                "this is gone"
            )
            execution.mark_failed("the worker running this execution stopped")
            reclaimed.append(self.repository.update(execution))
        return reclaimed

    # -- run paths ---------------------------------------------------------
    def _run_execution(
        self,
        execution: Execution,
        model: ModelDefinition,
        plugin,
        ctx: ExecutionContext,
        materialise_datasets: bool | None = None,
    ) -> Execution:
        outcome = plugin.execute(ctx)
        execution.logs.extend(ctx.logs + outcome.logs)

        #  Asked to stop while the plugin was working. The computation is over
        #  either way, but nothing is written down: a cancelled execution that
        #  points at a result is offering an answer nobody wanted, and the
        #  result row would belong to no one.
        if ctx.cancelled():
            execution.mark_cancelled("cancelled while running")
            return execution

        result = self.results.persist(
            execution_id=execution.id,
            payload=outcome.payload,
            metrics=outcome.metrics,
            dataset_name_hint=f"{model.name} result",
            lineage=execution.lineage,
            materialise_dataset=materialise_datasets,
        )
        execution.mark_succeeded(
            result_id=result.id, metrics={**outcome.metrics, **result.metrics}
        )
        return execution

    def _run_training(
        self, execution: Execution, model: ModelDefinition, plugin, ctx: ExecutionContext
    ) -> Execution:
        outcome = plugin.train(ctx)
        execution.logs.extend(ctx.logs + outcome.logs)

        #  Checked before the version is published: an immutable version nobody
        #  asked for cannot be taken back.
        if ctx.cancelled():
            execution.mark_cancelled("cancelled while training")
            return execution

        artifact_uri = None
        if outcome.artifact_path:
            source = Path(outcome.artifact_path)
            if source.exists():
                artifact_uri = self.store.put_file(
                    f"models/{model.id}/{execution.id}/{source.name}", source
                )

        version = self.models.publish_version(
            model.id,
            parameters=outcome.parameters,
            artifact_uri=artifact_uri,
            metrics=outcome.metrics,
            created_by_execution_id=execution.id,
            notes=f"trained by execution {execution.id}",
        )
        execution.produced_model_version_id = version.id

        result_id = None
        if outcome.payload is not None:
            result = self.results.persist(
                execution_id=execution.id,
                payload=outcome.payload,
                metrics=outcome.metrics,
                dataset_name_hint=f"{model.name} training report",
                lineage=execution.lineage,
            )
            result_id = result.id

        execution.mark_succeeded(result_id=result_id, metrics=outcome.metrics)
        execution.lineage["produced_model_version_id"] = version.id
        return execution

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _resolve_kind(
        requested: str | None, supported: tuple[ExecutionKind, ...]
    ) -> ExecutionKind:
        if requested is None:
            return supported[0] if supported else ExecutionKind.CALCULATION
        try:
            kind = ExecutionKind(requested)
        except ValueError as exc:
            raise ValidationError(
                f"unknown execution kind '{requested}'",
                details={"allowed": [k.value for k in ExecutionKind]},
            ) from exc
        if supported and kind not in supported:
            raise UnsupportedError(
                f"this model does not support '{kind.value}' executions",
                details={"supported": [k.value for k in supported]},
            )
        return kind

    def _resolve_dataset_version_id(
        self, dataset_version_id: str | None, dataset_id: str | None
    ) -> str | None:
        if dataset_version_id:
            self.datasets.get_version(dataset_version_id)
            return dataset_version_id
        if dataset_id:
            return self.datasets.current_version(dataset_id).id
        return None

    def _validated_parameters(
        self, model: ModelDefinition, raw: dict[str, Any]
    ) -> dict[str, Any]:
        contract = model.parameter_contract
        #  Model configuration supplies defaults; the request overrides them.
        merged = {**(model.configuration or {}), **(raw or {})}
        validation = contract.validate_record(merged)
        if not validation.valid:
            raise ValidationError(
                "parameters do not satisfy the model's parameter contract",
                details=validation.to_dict(),
            )
        coerced = contract.coerce_record(merged)
        #  Keep extras the provider may understand but the contract omits.
        return {**merged, **{k: v for k, v in coerced.items() if v is not None}}

    def _build_input(
        self,
        model: ModelDefinition,
        execution: Execution,
        *,
        table: Table | None = None,
        extra: dict[str, Table] | None = None,
    ) -> ExecutionInput:
        """Assemble what the plugin reads.

        A caller may hand the table over directly - a pipeline passing one
        step's output to the next - which avoids a write and a read for data
        that is on its way somewhere else anyway.
        """
        dataset_id = None

        if table is not None:
            pass
        elif execution.dataset_version_id:
            version = self.datasets.get_version(execution.dataset_version_id)
            dataset_id = version.dataset_id
            table = self.datasets.read_table(execution.dataset_version_id)
        elif execution.input_payload.get("rows"):
            table = Table.from_rows(execution.input_payload["rows"])

        record = {
            key: value
            for key, value in (execution.input_payload or {}).items()
            if key != "rows"
        }

        contract = model.input_contract
        if table is not None and contract.fields:
            validation = contract.validate_schema(table.schema_fields())
            if not validation.valid:
                raise ValidationError(
                    "input data does not satisfy the model's input contract",
                    details=validation.to_dict(),
                )
        elif record and contract.fields:
            validation = contract.validate_record(record)
            if not validation.valid:
                raise ValidationError(
                    "input payload does not satisfy the model's input contract",
                    details=validation.to_dict(),
                )

        return ExecutionInput(
            table=table,
            record=record,
            dataset_version_id=execution.dataset_version_id,
            dataset_id=dataset_id,
            inputs=dict(extra or {}),
        )

    def _artifact_path(self, model_version_id: str | None) -> str | None:
        version = self.models.find_version(model_version_id)
        if not version or not version.artifact_uri:
            return None
        return str(self.store.local_path(version.artifact_uri))


def _snapshot_of(definition: ModelDefinition) -> dict[str, Any]:
    """The behavioural half of a definition, as plain data."""
    return {
        "name": definition.name,
        "provider": definition.provider,
        "type": definition.type.value,
        "runtime": definition.runtime.value,
        "configuration": definition.configuration,
        "input_contract": definition.input_contract.to_dict(),
        "parameter_contract": definition.parameter_contract.to_dict(),
        "output_contract": definition.output_contract.to_dict(),
        "metadata": definition.metadata,
    }


def _placeholder_definition(execution: Execution) -> ModelDefinition:
    """A definition object for the snapshot to be rehydrated onto.

    `definition_from_snapshot` copies identity from the model row and behaviour
    from the snapshot. An inline definition has no row, so the identity it
    keeps is the execution's own - which is exactly what a step is: a thing
    that exists for the duration of one run.
    """
    snapshot = execution.definition_snapshot or {}
    return ModelDefinition(
        id=execution.id,
        name=snapshot.get("name") or "inline definition",
        slug=execution.id,
        provider=snapshot.get("provider") or "",
    )
