"""Experiment and evaluation services.

An experiment is a comparison you can check before you run: every trial is
validated against the model it names, the parameters it sets and the
dataset it will read, so a comparison that cannot run says so before it
consumes an execution slot rather than halfway through.
"""

from __future__ import annotations

from typing import Any

from app.modules.data.application.services import DatasetService
from app.modules.execution.application.services import ExecutionService
from app.modules.model.application.services import ModelService
from app.modules.model.domain.entities import ModelStatus
from app.modules.model.domain.registry import PluginRegistry
from app.shared.errors import ConflictError, NotFoundError, ValidationError
from app.shared.ids import utcnow

from ..domain.entities import Evaluation, Experiment, ExperimentTrial
from ..domain.ports import EvaluationRepository, ExperimentRepository

#  How far back a comparison looks. An experiment re-run many times still
#  only shows where each trial stands now; history is opt-in.
_RUN_SCAN_LIMIT = 200


class ExperimentService:
    """An experiment is a comparison you can check and then run.

    It needs the model library to resolve trials, the dataset service to read
    the schema it will run against, and the plugin registry to know what each
    model can do — validating a comparison means asking all three.
    """

    def __init__(
        self,
        repository: ExperimentRepository,
        models: ModelService,
        datasets: DatasetService,
        registry: PluginRegistry,
        evaluations: EvaluationService | None = None,
        executions: ExecutionService | None = None,
        pipelines=None,
    ):
        self.repository = repository
        self.models = models
        self.datasets = datasets
        self.registry = registry
        #  Reading results is part of what an experiment is for, so the two
        #  services that hold them are collaborators, not route-level details.
        self.evaluations = evaluations
        self.executions = executions
        #  Optional, and duck-typed on purpose: `evaluation` sits beside
        #  `orchestration` in the dependency stack, so it reads pipelines
        #  through whatever the container hands it rather than importing the
        #  service and pinning the two together.
        self.pipelines = pipelines

    def create(
        self,
        *,
        name: str,
        description: str = "",
        objective: str = "",
        primary_metric: str = "",
        primary_direction: str = "higher",
        dataset_version_id: str | None = None,
        trials: list[dict] | None = None,
        model_ids: list[str] | None = None,
    ) -> Experiment:
        if self.repository.get_by_name(name):
            raise ConflictError(f"an experiment named '{name}' already exists")
        return self.repository.add(
            Experiment(
                name=name,
                description=description,
                objective=objective,
                primary_metric=primary_metric,
                primary_direction=_direction(primary_direction),
                dataset_version_id=dataset_version_id,
                trials=_trials_from(trials, model_ids),
            )
        )

    def update(self, experiment_id: str, changes: dict[str, Any]) -> Experiment:
        experiment = self.get(experiment_id)
        for key in ("description", "objective", "primary_metric", "primary_direction"):
            if changes.get(key) is not None:
                setattr(experiment, key, changes[key])
        if changes.get("name"):
            experiment.name = changes["name"]
        if "dataset_version_id" in changes:
            experiment.dataset_version_id = changes["dataset_version_id"]
        if changes.get("trials") is not None:
            experiment.trials = _trials_from(changes["trials"], None)
        experiment.updated_at = utcnow()
        return self.repository.update(experiment)

    def check(self, experiment_id: str) -> dict[str, Any]:
        """Whether this experiment can run, and what is wrong if it cannot.

        Checked before anything executes, because the alternative is finding out
        one trial at a time: a comparison that fails halfway is worse than one
        that never started, since the partial results look like an answer.

        Every trial is reported on individually — one bad parameter should not
        hide the fact that the other four trials are ready.
        """
        experiment = self.get(experiment_id)
        report: dict[str, Any] = {
            "experiment_id": experiment.id,
            "runnable": True,
            "errors": [],
            "warnings": [],
            "trials": [],
        }
        if not experiment.trials:
            report["runnable"] = False
            report["errors"].append("this experiment has no trials to compare")

        if not experiment.primary_metric:
            report["warnings"].append(
                "no primary metric, so the trials cannot be ranked against each other"
            )

        schema_fields = None
        if experiment.dataset_version_id:
            try:
                #  The version stores a schema id; the fields live on the schema.
                version = self.datasets.get_version(experiment.dataset_version_id)
                if version.schema_id:
                    schema_fields = self.datasets.get_schema(version.schema_id).fields
            except NotFoundError:
                report["runnable"] = False
                report["errors"].append("the dataset version no longer exists")

        for index, trial in enumerate(experiment.trials):
            entry = _check_trial(self, experiment, trial, index, schema_fields)
            report["trials"].append(entry)
            if not entry["runnable"]:
                report["runnable"] = False
        return report

    def run(
        self, experiment_id: str, executions: ExecutionService | None = None
    ) -> Experiment:
        """Run every trial, as one act.

        The unit of execution is the experiment: submitting the trials one by
        one from a screen is how comparisons end up with a missing arm nobody
        notices. Validation runs first, and a failure to submit is recorded
        against the trial rather than abandoning the ones that did start.
        """
        experiment = self.get(experiment_id)
        runner = executions or self.executions
        if runner is None:
            raise ValidationError("this deployment cannot run experiments")
        report = self.check(experiment_id)
        if not report["runnable"]:
            raise ValidationError(
                "this experiment cannot run yet", details={"check": report}
            )

        for trial in experiment.trials:
            execution = runner.submit(
                #  Whichever kind of runnable the trial names. Everything else
                #  about a trial - the held-constant dataset, the parameters,
                #  the experiment it belongs to - is the same either way.
                model_id=trial.target_id if trial.target_type == "model" else None,
                pipeline_id=(
                    trial.target_id if trial.target_type == "pipeline" else None
                ),
                kind=trial.kind,
                dataset_version_id=experiment.dataset_version_id,
                parameters=trial.parameters,
                model_version_id=trial.model_version_id,
                experiment_id=experiment.id,
                context={
                    "experiment": experiment.name,
                    "trial": trial.display_name(),
                },
            )
            if execution.id not in experiment.execution_ids:
                experiment.execution_ids.append(execution.id)

        experiment.updated_at = utcnow()
        return self.repository.update(experiment)

    def get(self, experiment_id: str) -> Experiment:
        experiment = self.repository.get(experiment_id)
        if not experiment:
            raise NotFoundError(f"experiment '{experiment_id}' not found")
        return experiment

    def list(self) -> list[Experiment]:
        return self.repository.list()

    def attach_execution(self, experiment_id: str, execution_id: str) -> Experiment:
        experiment = self.get(experiment_id)
        if execution_id not in experiment.execution_ids:
            experiment.execution_ids.append(execution_id)
        return self.repository.update(experiment)

    def attach_model(self, experiment_id: str, model_id: str) -> Experiment:
        experiment = self.get(experiment_id)
        if model_id not in experiment.model_ids:
            experiment.model_ids.append(model_id)
        return self.repository.update(experiment)

    # -- reading results ---------------------------------------------------
    #  These two used to live in the route. "Which run represents which trial",
    #  "how are trials ranked" and "how are metric columns discovered" are the
    #  comparison semantics of an Experiment, so they belong to the experiment
    #  rather than to one HTTP handler that happened to need them first.

    def leaderboard(self, experiment_id: str) -> dict[str, Any]:
        """Where each trial stands.

        Two sources, in order of authority. A recorded Evaluation is a
        judgement: it carries the target and whether the trial met it. A
        succeeded execution is only a measurement, but a measurement beats the
        blank row this used to show for any trial nobody had got around to
        scoring - three successful runs of a k sweep reported "not evaluated"
        purely because the sweep never went through the evaluation form.
        """
        experiment = self.get(experiment_id)
        scored = self._evaluations_by_execution(experiment_id)
        runs = self._latest_run_per_trial(experiment_id)

        metric_names: list[str] = []
        rows: list[dict[str, Any]] = []
        for trial in experiment.trials:
            target = self._describe_target(trial)
            if target is None:
                #  Whatever it named is gone; the check endpoint says so in
                #  words, and a row about a thing that no longer exists would
                #  only be a row of blanks.
                continue
            label = trial.display_name(target["name"])
            execution = runs.get(label) or runs.get(trial.target_id)
            evaluation = scored.get(execution.id) if execution else None

            metrics = dict(
                evaluation.metrics
                if evaluation
                else (execution.metrics if execution else {})
            )
            for name, value in metrics.items():
                if name not in metric_names and _is_number(value):
                    metric_names.append(name)

            primary = None
            if experiment.primary_metric:
                candidate = metrics.get(experiment.primary_metric)
                if _is_number(candidate):
                    primary = float(candidate)
            rows.append(
                {
                    "trial": label,
                    "target_id": trial.target_id,
                    "target_type": trial.target_type,
                    #  Still spelled model_* because that is what a leaderboard
                    #  reader looks for; for a pipeline trial these describe the
                    #  pipeline, and model_id is empty rather than borrowed.
                    "model_id": trial.model_id,
                    "model_name": target["name"],
                    "model_type": target["type"],
                    "provider": target["provider"],
                    "evaluation_id": evaluation.id if evaluation else None,
                    "execution_id": execution.id if execution else None,
                    "metrics": metrics,
                    "primary_value": primary,
                    "passed": evaluation.passed if evaluation else None,
                    "evaluated_at": evaluation.created_at if evaluation else None,
                }
            )
        metric_names.sort()
        #  Best first, whichever way "best" runs for this metric; trials with
        #  nothing recorded sink to the bottom either way.
        better = _better_first(experiment.primary_direction)
        rows.sort(key=lambda row: (row["primary_value"] is None, better(row)))
        return {
            "experiment_id": experiment.id,
            "name": experiment.name,
            "objective": experiment.objective,
            "primary_metric": experiment.primary_metric,
            #  So a reader can tell "best" from "biggest".
            "primary_direction": experiment.primary_direction,
            "metric_names": metric_names,
            "rows": rows,
        }

    def compare(
        self,
        experiment_ids: list[str],
        *,
        metric: str | None = None,
        include_history: bool = False,
    ) -> dict[str, Any]:
        """Put several experiments side by side.

        Metric columns are discovered from the runs, never listed in advance.
        The comparison table used to name accuracy, correct and total - the
        metrics one particular backtest happens to report - so a model measured
        by RMSE or by an objective value showed three empty columns and looked
        broken.
        """
        rows: list[dict[str, Any]] = []
        metric_names: list[str] = []
        scoped: list[dict[str, Any]] = []

        for experiment_id in experiment_ids:
            experiment = self.get(experiment_id)
            scoped.append(
                {
                    "id": experiment.id,
                    "name": experiment.name,
                    "primary_metric": experiment.primary_metric,
            #  So a reader can tell "best" from "biggest".
            "primary_direction": experiment.primary_direction,
                }
            )
            #  Executions come back newest first, so the first sighting of a
            #  trial is its latest run. Without this, running an experiment
            #  three times put three near-identical rows in the table and made
            #  the leader a matter of which duplicate sorted first.
            seen: set[str] = set()
            for execution in self._succeeded_runs(experiment.id):
                try:
                    model_name = self.models.get(execution.model_id).name
                except NotFoundError:
                    model_name = execution.model_id
                trial_name = self._trial_name(experiment, execution, model_name)
                if not include_history:
                    if trial_name in seen:
                        continue
                    seen.add(trial_name)
                metrics = {
                    key: value
                    for key, value in (execution.metrics or {}).items()
                    if _is_number(value)
                }
                for name in metrics:
                    if name not in metric_names:
                        metric_names.append(name)
                rows.append(
                    {
                        "experiment_id": experiment.id,
                        "experiment": experiment.name,
                        "trial": trial_name,
                        "model_id": execution.model_id,
                        "model": model_name,
                        "execution_id": execution.id,
                        "finished_at": execution.finished_at,
                        "metrics": metrics,
                    }
                )

        metric_names.sort()
        #  Rank on the metric the experiments agree on, when they agree on one.
        chosen = metric or _shared_metric(scoped, metric_names)
        if chosen:
            rows.sort(
                key=lambda row: (
                    row["metrics"].get(chosen) is None,
                    -(row["metrics"].get(chosen) or 0.0),
                )
            )
        return {
            "experiments": scoped,
            "metric_names": metric_names,
            "ranked_by": chosen,
            "rows": rows,
        }

    # -- internals ---------------------------------------------------------
    def _succeeded_runs(self, experiment_id: str) -> list[Any]:
        if self.executions is None:
            return []
        return [
            execution
            for execution in self.executions.list(
                experiment_id=experiment_id, limit=_RUN_SCAN_LIMIT
            )
            if execution.status.value == "succeeded"
        ]

    def _evaluations_by_execution(self, experiment_id: str) -> dict[str, Any]:
        """Newest evaluation per execution; an experiment is re-run over time."""
        if self.evaluations is None:
            return {}
        found: dict[str, Any] = {}
        for evaluation in sorted(
            self.evaluations.list(experiment_id=experiment_id),
            key=lambda e: e.created_at,
        ):
            if evaluation.execution_id:
                found[evaluation.execution_id] = evaluation
        return found

    def _describe_target(self, trial: ExperimentTrial) -> dict[str, str] | None:
        """What a trial is comparing, in the words a leaderboard shows.

        Returns None when the target is gone, which is the one case a row
        cannot be built for.
        """
        if trial.target_type == "pipeline":
            if self.pipelines is None:
                return None
            try:
                pipeline = self.pipelines.get(trial.target_id)
            except NotFoundError:
                return None
            return {
                "name": pipeline.name,
                "type": "pipeline",
                "provider": f"{len(pipeline.steps)} steps",
            }
        try:
            model = self.models.get(trial.target_id)
        except NotFoundError:
            return None
        return {
            "name": model.name,
            "type": model.type.value,
            "provider": model.provider,
        }

    def _latest_run_per_trial(self, experiment_id: str) -> dict[str, Any]:
        runs: dict[str, Any] = {}
        for execution in self._succeeded_runs(experiment_id):
            key = (execution.context or {}).get("trial") or execution.target_id
            runs.setdefault(key, execution)
        return runs

    @staticmethod
    def _trial_name(experiment: Experiment, execution: Any, fallback: str) -> str:
        """Which trial a run belongs to.

        The run recorded it at submission; matching on model_id alone collapses
        two trials of one model configured differently, which is exactly the
        comparison somebody is most likely to want.
        """
        recorded = (execution.context or {}).get("trial")
        if recorded:
            return recorded
        trial = next(
            (
                t
                for t in experiment.trials
                if t.target_id == execution.target_id
                and t.target_type == execution.target_type.value
            ),
            None,
        )
        return trial.display_name(fallback) if trial else fallback

    def delete(self, experiment_id: str) -> None:
        self.repository.delete(self.get(experiment_id).id)


def _direction(value: str) -> str:
    """Only two answers, and an unknown one is not silently accepted."""
    normalised = (value or "higher").strip().lower()
    if normalised not in ("higher", "lower"):
        raise ValidationError(
            f"primary_direction must be 'higher' or 'lower', not '{value}'"
        )
    return normalised


def _better_first(direction: str):
    """A sort key that puts the best trial first."""
    sign = 1.0 if direction == "lower" else -1.0
    return lambda row: sign * (row["primary_value"] or 0.0)


def _is_number(value: Any) -> bool:
    """Booleans are ints in Python, and a metric column of True/False is noise."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _shared_metric(scoped: list[dict], metric_names: list[str]) -> str | None:
    """The metric the experiments agree on, when they agree on exactly one."""
    stated = {e["primary_metric"] for e in scoped if e["primary_metric"]}
    if len(stated) == 1:
        only = stated.pop()
        if only in metric_names:
            return only
    return metric_names[0] if metric_names else None


def _trials_from(
    trials: list[dict] | None, model_ids: list[str] | None
) -> list[ExperimentTrial]:
    """Accept trials, or a bare model list from an older caller."""
    if trials is not None:
        return [ExperimentTrial.from_dict(t) for t in trials]
    return [ExperimentTrial(target_id=model_id) for model_id in (model_ids or [])]


def _check_pipeline_trial(
    service: ExperimentService, entry: dict[str, Any], trial: ExperimentTrial
) -> dict[str, Any]:
    """Whether a pipeline trial can run: does the pipeline still exist."""
    if service.pipelines is None:
        entry["runnable"] = False
        entry["errors"].append("this deployment cannot compare pipelines")
        return entry
    try:
        pipeline = service.pipelines.get(trial.target_id)
    except NotFoundError:
        entry["runnable"] = False
        entry["errors"].append("this pipeline no longer exists")
        return entry
    entry["model_name"] = pipeline.name
    return entry


def _check_trial(
    service: ExperimentService,
    experiment: Experiment,
    trial: ExperimentTrial,
    index: int,
    schema_fields,
) -> dict[str, Any]:
    label = trial.display_name(f"trial {index + 1}")
    entry: dict[str, Any] = {
        "label": label,
        "target_id": trial.target_id,
        "target_type": trial.target_type,
        "model_id": trial.model_id,
        "model_name": None,
        "runnable": True,
        "errors": [],
        "warnings": [],
    }

    if trial.target_type == "pipeline":
        #  A pipeline has no provider contract to check a parameter against;
        #  what makes it runnable is that it exists and its steps are valid,
        #  which the pipeline service already decided when it was saved.
        return _check_pipeline_trial(service, entry, trial)

    try:
        model = service.models.get(trial.target_id)
    except NotFoundError:
        entry["runnable"] = False
        entry["errors"].append("this model no longer exists")
        return entry

    entry["model_name"] = model.name
    if model.status is ModelStatus.DEPRECATED:
        entry["warnings"].append("this model is deprecated")

    descriptor = None
    try:
        descriptor = service.registry.get(model.provider).describe()
    except Exception:  # noqa: BLE001 - a missing plugin is a trial problem
        entry["runnable"] = False
        entry["errors"].append(f"provider '{model.provider}' is not installed")

    if descriptor is not None:
        kinds = [k.value for k in descriptor.supported_kinds]
        if not kinds:
            entry["runnable"] = False
            entry["errors"].append("this model declares no execution kinds")
        elif trial.kind and trial.kind not in kinds:
            entry["runnable"] = False
            entry["errors"].append(
                f"'{trial.kind}' is not one of this model's kinds ({', '.join(kinds)})"
            )
        entry["kinds"] = kinds

    #  Parameters are checked against the model's own contract, so a typo is
    #  caught here rather than in the middle of a five-trial comparison.
    if trial.parameters:
        outcome = model.parameter_contract.validate_record(
            trial.parameters, path="parameters"
        )
        entry["errors"].extend(outcome.errors)
        entry["warnings"].extend(outcome.warnings)
        if not outcome.valid:
            entry["runnable"] = False

    if schema_fields is not None:
        outcome = model.input_contract.validate_schema(schema_fields)
        entry["errors"].extend(outcome.errors)
        entry["warnings"].extend(outcome.warnings)
        if not outcome.valid:
            entry["runnable"] = False

        #  A declared contract only covers models that declare their fields. One
        #  that names its columns in configuration can only be checked by its
        #  own provider, and says so when it cannot be.
        plugin = service.registry.get(model.provider) if descriptor else None
        checker = getattr(plugin, "check_dataset", None)
        if callable(checker):
            provider_outcome = checker(model, schema_fields)
            entry["errors"].extend(provider_outcome.errors)
            entry["warnings"].extend(provider_outcome.warnings)
            if not provider_outcome.valid:
                entry["runnable"] = False
        elif not model.input_contract.fields:
            entry["warnings"].append(
                "this provider validates its own input, so the columns it needs "
                "could not be checked in advance"
            )
    elif experiment.dataset_version_id is None and model.input_contract.fields:
        entry["warnings"].append(
            "no dataset chosen; this model declares an input contract"
        )

    return entry


class EvaluationService:
    """Scores an execution against a target. Metric names are open-ended."""

    def __init__(self, repository: EvaluationRepository):
        self.repository = repository

    def record(
        self,
        *,
        execution_id: str,
        metrics: dict[str, Any],
        target: dict[str, Any] | None = None,
        model_id: str | None = None,
        experiment_id: str | None = None,
        notes: str = "",
    ) -> Evaluation:
        target = target or {}
        return self.repository.add(
            Evaluation(
                execution_id=execution_id,
                metrics=metrics,
                target=target,
                passed=_meets_target(metrics, target),
                model_id=model_id,
                experiment_id=experiment_id,
                notes=notes,
            )
        )

    def list(self, **filters) -> list[Evaluation]:
        return self.repository.list(**filters)


def _meets_target(metrics: dict[str, Any], target: dict[str, Any]) -> bool | None:
    """Target form: {"metric": "accuracy", "min": 0.7} or {..., "max": 12.0}."""
    if not target or "metric" not in target:
        return None
    value = metrics.get(target["metric"])
    if not isinstance(value, (int, float)):
        return None
    if "min" in target and value < float(target["min"]):
        return False
    if "max" in target and value > float(target["max"]):
        return False
    return True
