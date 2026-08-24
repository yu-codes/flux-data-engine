"""Running a pipeline as an execution.

A Pipeline fits the platform's own formula as well as a Model does: inputs,
parameters, a versioned definition, an output. For a long time only a Model
could be executed, which meant everything built on top of execution -
scheduling, experiments, serving, lineage - either had to be built a second
time for pipelines or was simply denied to them.

This is the adapter that closes that gap. It is handed to `ExecutionService`
by the composition root rather than imported by it, because `orchestration`
sits above `execution` in the dependency stack and that direction must not be
reversed for convenience. The execution layer knows only that runnables exist.
"""

from __future__ import annotations

from typing import Any

from app.modules.model.domain.plugin import ExecutionOutcome
from app.shared.errors import ExecutionError
from app.shared.payloads import ResultKind, ResultPayload
from app.shared.tabular import Table

from ..domain.entities import RunStatus


def pipeline_runner(pipelines):
    """A runner for `RunnableKind.PIPELINE`, closed over the pipeline service."""

    def run(execution) -> ExecutionOutcome:
        run_record = pipelines.run(
            execution.target_id,
            dataset_version_id=execution.dataset_version_id,
            triggered_by=(execution.context or {}).get("triggered_by"),
            #  So the run and the execution can find each other afterwards.
            execution_id=execution.id,
        )
        if run_record.status is not RunStatus.SUCCEEDED:
            #  A pipeline collects its step failures rather than raising, which
            #  is right for the pipeline view - you want to see which step went
            #  wrong. It is not right for the execution: an execution whose
            #  work did not finish must not report success, or every count
            #  built on execution status is quietly wrong from here on.
            failed = [s.step_name for s in run_record.step_runs if s.error]
            named = ", ".join(failed) or "no step succeeded"
            raise ExecutionError(
                run_record.error or f"the pipeline run did not finish: {named}",
                details={"pipeline_run_id": run_record.id, "failed_steps": failed},
            )
        return _outcome(pipelines, run_record)

    return run


def _outcome(pipelines, run_record) -> ExecutionOutcome:
    """What the pipeline produced, in the shape every execution reports.

    The payload is the pipeline's own output table, so invoking a pipeline
    answers with the rows it produces rather than with a report about itself -
    which is what makes a pipeline usable as a served runnable at all.
    """
    table = _output_table(pipelines, run_record)
    failed = [step.step_name for step in run_record.step_runs if step.error]
    metrics: dict[str, Any] = {
        "steps": len(run_record.step_runs),
        "succeeded_steps": run_record.succeeded_steps,
        "failed_steps": len(failed),
        "duration_seconds": run_record.duration_seconds or 0.0,
    }
    summary = {
        "pipeline_run_id": run_record.id,
        "status": run_record.status.value,
        "outputs": list(run_record.output_dataset_ids),
        "failed": failed,
    }
    if run_record.error:
        summary["error"] = run_record.error

    return ExecutionOutcome(
        payload=ResultPayload.of_table(
            table,
            kind=ResultKind.TABLE,
            summary=summary,
            #  The pipeline already materialised what it was built to produce;
            #  a second copy under the execution would be the same rows with a
            #  different name.
            materialise_as_dataset=False,
        ),
        metrics=metrics,
        logs=[
            f"step '{step.step_name}': {step.status.value}"
            + (f" - {step.error}" if step.error else "")
            for step in run_record.step_runs
        ],
    )


def _output_table(pipelines, run_record) -> Table:
    """The pipeline's terminal output, or an empty table when it produced none."""
    for dataset_id in run_record.output_dataset_ids:
        try:
            dataset = pipelines.datasets.get(dataset_id)
            if dataset.current_version_id:
                return pipelines.datasets.read_table(dataset.current_version_id)
        except Exception:  # noqa: BLE001 - a missing output is not a failed run
            continue
    return Table.from_rows([])
