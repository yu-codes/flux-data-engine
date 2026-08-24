"""Simulation model provider: propagate uncertainty through an expression.

    profit = (price - cost) * demand
    where   cost ~ normal(20, 3),  demand ~ triangular(100, 400, 900)

A single arithmetic answer built from averages hides the thing a decision needs:
how wide the outcome is, and how often it lands somewhere unacceptable. This
draws each uncertain input from a stated distribution, evaluates the expression
many times, and reports the distribution of results — percentiles, spread, and
the probability of any threshold worth naming.

Deterministic despite being random: the seed is part of the configuration, so a
run is reproducible and two runs can be compared.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any

from app.modules.model.domain.entities import ModelDefinition, ModelType, RuntimeKind
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionKind,
    ExecutionOutcome,
    PluginDescriptor,
)
from app.plugins.formula.expression import (
    compile_expression,
    evaluate,
    expression_variables,
)
from app.shared.contracts import (
    Contract,
    ContractShape,
    FieldSpec,
    FieldType,
    ValidationResult,
)
from app.shared.errors import ExecutionError, ValidationError
from app.shared.payloads import ResultKind, ResultPayload
from app.shared.tabular import Table

PLUGIN_KEY = "monte-carlo"

MAX_TRIALS = 200_000
DEFAULT_TRIALS = 10_000

#  Distribution → the parameters it requires. Keeping this as data means the
#  validator, the UI hint and the sampler cannot drift apart.
DISTRIBUTIONS: dict[str, tuple[str, ...]] = {
    "normal": ("mean", "sd"),
    "uniform": ("min", "max"),
    "triangular": ("min", "mode", "max"),
    "lognormal": ("mean", "sd"),
    "fixed": ("value",),
}


class MonteCarloPlugin:
    """Repeated sampling of stated distributions through an expression."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Monte Carlo simulation",
            model_type=ModelType.SIMULATION,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Samples uncertain inputs from stated distributions and evaluates "
                "an expression over many trials, reporting the outcome's spread, "
                "percentiles and threshold probabilities rather than one number."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.SIMULATION, ExecutionKind.CALCULATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec(
                        "expression",
                        FieldType.STRING,
                        description="the outcome, e.g. (price - cost) * demand",
                    ),
                    FieldSpec(
                        "inputs",
                        FieldType.JSON,
                        description=(
                            'name → {"distribution": "normal", "mean": .., "sd": ..}. '
                            "Supports normal, uniform, triangular, lognormal, fixed."
                        ),
                    ),
                    FieldSpec(
                        "trials", FieldType.INTEGER, required=False, default=DEFAULT_TRIALS
                    ),
                    FieldSpec(
                        "seed",
                        FieldType.INTEGER,
                        required=False,
                        default=42,
                        description="makes the run reproducible",
                    ),
                    FieldSpec(
                        "thresholds",
                        FieldType.JSON,
                        required=False,
                        description='label → {"op": ">=", "value": 0} to report P(outcome)',
                    ),
                    FieldSpec("bins", FieldType.INTEGER, required=False, default=20),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="the outcome distribution as histogram buckets",
            ),
            examples=[
                {
                    "name": "Profit at risk",
                    "configuration": {
                        "expression": "(price - cost) * demand",
                        "inputs": {
                            "price": {"distribution": "fixed", "value": 50},
                            "cost": {"distribution": "normal", "mean": 20, "sd": 3},
                            "demand": {
                                "distribution": "triangular",
                                "min": 100,
                                "mode": 400,
                                "max": 900,
                            },
                        },
                        "thresholds": {"loss": {"op": "<", "value": 0}},
                    },
                }
            ],
        )

    # -- validation --------------------------------------------------------
    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}

        expression = config.get("expression")
        if not expression:
            result.add_error("configuration.expression must be an expression")
        else:
            try:
                compile_expression(str(expression))
            except ValidationError as exc:
                result.add_error(f"expression: {exc.message}")

        inputs = config.get("inputs")
        if not isinstance(inputs, dict) or not inputs:
            return result.add_error("configuration.inputs must declare at least one input")

        for name, spec in inputs.items():
            if not isinstance(spec, dict):
                result.add_error(f"inputs.{name}: expected an object")
                continue
            kind = spec.get("distribution", "fixed")
            required = DISTRIBUTIONS.get(kind)
            if required is None:
                result.add_error(
                    f"inputs.{name}: unknown distribution '{kind}'; "
                    f"expected one of {sorted(DISTRIBUTIONS)}"
                )
                continue
            missing = [p for p in required if spec.get(p) is None]
            if missing:
                result.add_error(f"inputs.{name}: {kind} needs {missing}")
            if kind in ("normal", "lognormal") and _number(spec.get("sd")) is not None:
                if _number(spec["sd"]) < 0:
                    result.add_error(f"inputs.{name}: sd cannot be negative")
            if kind == "uniform" and None not in (spec.get("min"), spec.get("max")):
                if _number(spec["max"]) < _number(spec["min"]):
                    result.add_error(f"inputs.{name}: max is below min")
            if kind == "triangular" and all(
                spec.get(p) is not None for p in ("min", "mode", "max")
            ):
                low, mode, high = (_number(spec[p]) for p in ("min", "mode", "max"))
                if not low <= mode <= high:
                    result.add_error(f"inputs.{name}: triangular needs min ≤ mode ≤ max")

        trials = config.get("trials", DEFAULT_TRIALS)
        if not isinstance(trials, int) or not 1 <= trials <= MAX_TRIALS:
            result.add_error(f"configuration.trials must be between 1 and {MAX_TRIALS:,}")

        if expression:
            try:
                unknown = expression_variables(str(expression)) - set(inputs)
                if unknown:
                    result.add_error(
                        f"expression reads {sorted(unknown)}, which is not a declared input"
                    )
            except ValidationError:
                pass
        return result

    # -- execution ---------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        source = config.get("expression")
        if not source:
            raise ValidationError("this simulation defines no expression")
        inputs = config.get("inputs") or {}
        if not inputs:
            raise ValidationError("this simulation declares no uncertain inputs")

        trials = int(config.get("trials", DEFAULT_TRIALS))
        if not 1 <= trials <= MAX_TRIALS:
            raise ValidationError(f"trials must be between 1 and {MAX_TRIALS:,}")

        expression = compile_expression(str(source))
        rng = random.Random(int(config.get("seed", 42)))
        samplers = {name: _sampler(name, spec) for name, spec in inputs.items()}

        outcomes: list[float] = []
        failed = 0
        drawn = 0
        stopped_early = False
        for _ in range(trials):
            #  A million trials over a slow expression is bounded in count and
            #  not in time. Checked here for the same reason the optimizer
            #  checks its grid: a distribution from the draws that finished is
            #  worth more than being killed with nothing - as long as it says
            #  how many draws it actually is.
            if drawn % 2000 == 0 and context.should_stop():
                stopped_early = True
                context.log(
                    f"stopped after {drawn:,} of {trials:,} trials: "
                    f"{'cancelled' if context.cancelled() else 'out of time'}"
                )
                break
            drawn += 1
            env = {name: sample(rng) for name, sample in samplers.items()}
            try:
                value = _number(evaluate(expression, env))
            except Exception:  # noqa: BLE001 - one bad draw is not a failed run
                value = None
            if value is None:
                failed += 1
                continue
            outcomes.append(value)

        if not outcomes and stopped_early:
            #  Nothing to summarise, but the reason is not a broken expression
            #  and must not be reported as one.
            raise ExecutionError(
                f"the simulation stopped after {drawn:,} of {trials:,} trials "
                f"({'cancelled' if context.cancelled() else 'out of time'}), "
                f"before any of them produced a value"
            )
        if not outcomes:
            raise ValidationError(
                f"every one of the {drawn} trials failed to evaluate; "
                f"check the expression against the declared inputs"
            )
        if failed:
            context.log(f"{failed} of {drawn} trials could not be evaluated")

        ordered = sorted(outcomes)
        percentiles = {
            f"p{p}": round(_quantile(ordered, p / 100), 6) for p in (5, 25, 50, 75, 95)
        }
        mean = statistics.fmean(outcomes)
        summary: dict[str, Any] = {
            "expression": str(source),
            "trials": len(outcomes),
            #  A distribution that stopped early is still an answer, but a
            #  reader has to be able to tell it apart from one that did not.
            "requested_trials": trials,
            "complete": not stopped_early,
            "mean": round(mean, 6),
            "stdev": round(statistics.pstdev(outcomes), 6) if len(outcomes) > 1 else 0.0,
            "min": round(ordered[0], 6),
            "max": round(ordered[-1], 6),
            **percentiles,
        }

        probabilities = {}
        for label, rule in (config.get("thresholds") or {}).items():
            hits = _count_matching(ordered, rule)
            probabilities[label] = round(hits / len(ordered), 6)
        if probabilities:
            summary["probabilities"] = probabilities

        rows = _histogram(ordered, int(config.get("bins", 20)))
        payload = ResultPayload.of_table(
            Table.from_rows(rows),
            kind=ResultKind.PROBABILITY,
            summary=summary,
            materialise_as_dataset=True,
            dataset_name=f"{context.definition.name} result",
        )
        return ExecutionOutcome(
            payload=payload,
            metrics={
                "trials": len(outcomes),
                "complete": 0 if stopped_early else 1,
                "mean": summary["mean"],
                "stdev": summary["stdev"],
                **percentiles,
                **{f"p_{k}": v for k, v in probabilities.items()},
            },
            logs=context.logs,
        )


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------
def _sampler(name: str, spec: Any):
    if not isinstance(spec, dict):
        raise ValidationError(f"inputs.{name}: expected an object")
    kind = spec.get("distribution", "fixed")
    if kind not in DISTRIBUTIONS:
        raise ValidationError(f"inputs.{name}: unknown distribution '{kind}'")
    missing = [p for p in DISTRIBUTIONS[kind] if spec.get(p) is None]
    if missing:
        raise ValidationError(f"inputs.{name}: {kind} needs {missing}")

    if kind == "fixed":
        value = _number(spec["value"])
        return lambda rng: value
    if kind == "normal":
        mean, sd = _number(spec["mean"]), _number(spec["sd"])
        return lambda rng: rng.gauss(mean, sd)
    if kind == "lognormal":
        mean, sd = _number(spec["mean"]), _number(spec["sd"])
        return lambda rng: rng.lognormvariate(mean, sd)
    if kind == "uniform":
        low, high = _number(spec["min"]), _number(spec["max"])
        return lambda rng: rng.uniform(low, high)
    low, mode, high = (_number(spec[p]) for p in ("min", "mode", "max"))
    return lambda rng: rng.triangular(low, high, mode)


def _count_matching(ordered: list[float], rule: Any) -> int:
    if not isinstance(rule, dict):
        raise ValidationError("each threshold must be an object with 'op' and 'value'")
    op = rule.get("op", ">=")
    bound = _number(rule.get("value"))
    if bound is None:
        raise ValidationError("each threshold needs a numeric 'value'")
    tests = {
        ">=": lambda v: v >= bound,
        ">": lambda v: v > bound,
        "<=": lambda v: v <= bound,
        "<": lambda v: v < bound,
    }
    if op not in tests:
        raise ValidationError(f"unsupported threshold operator '{op}'")
    return sum(1 for value in ordered if tests[op](value))


def _histogram(ordered: list[float], bins: int) -> list[dict[str, Any]]:
    bins = max(2, min(bins, 100))
    low, high = ordered[0], ordered[-1]
    if low == high:
        return [
            {"bucket": f"{low:.4g}", "lower": low, "upper": high, "trials": len(ordered)}
        ]
    width = (high - low) / bins
    counts = [0] * bins
    for value in ordered:
        counts[min(bins - 1, int((value - low) / width))] += 1
    return [
        {
            "bucket": f"{low + width * i:.4g}–{low + width * (i + 1):.4g}",
            "lower": round(low + width * i, 6),
            "upper": round(low + width * (i + 1), 6),
            "trials": count,
            "share": round(count / len(ordered), 6),
        }
        for i, count in enumerate(counts)
    ]


def _quantile(ordered: list[float], q: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(float(value)) else float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
