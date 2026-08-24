"""Optimization model provider: search a bounded space for the best settings.

    maximise  price * demand
    where     demand = 500 - 3 * price
    over      price from 10 to 200

No solver, no gradients, no training: the objective and the constraints are
expressions over named variables, and the provider evaluates them across a grid
of candidate settings and reports the best one with the runners-up beside it.

That last part is the point. An optimiser that returns only its answer cannot
be checked; returning the ranked neighbourhood shows whether the optimum is a
clear peak or a plateau where the recommendation barely matters.
"""

from __future__ import annotations

import itertools
import math
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
    VisibleWhen,
)
from app.shared.errors import ValidationError
from app.shared.payloads import ResultKind, ResultPayload
from app.shared.tabular import Table

PLUGIN_KEY = "optimizer"

#  A grid is bounded work, and a bound is what makes this safe to expose over
#  an API: the product of the per-variable step counts can never exceed this.
MAX_CANDIDATES = 200_000


class OptimizerPlugin:
    """Grid search over declared variable ranges, subject to constraints."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Grid optimiser",
            model_type=ModelType.OPTIMIZATION,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Maximises or minimises an expression over bounded variables, "
                "subject to constraint expressions. Reports the best setting and "
                "the ranked alternatives, so a flat optimum is visible as one."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.OPTIMIZATION, ExecutionKind.CALCULATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec(
                        "objective",
                        FieldType.STRING,
                        description="expression to optimise, e.g. price * demand",
                    ),
                    FieldSpec(
                        "goal",
                        FieldType.STRING,
                        required=False,
                        default="maximise",
                        enum=("maximise", "minimise"),
                    ),
                    FieldSpec(
                        "variables",
                        FieldType.JSON,
                        description="what the search may vary, and within what",
                        values=FieldSpec(
                            name="variable",
                            type=FieldType.JSON,
                            fields=(
                                FieldSpec(
                                    "kind",
                                    FieldType.STRING,
                                    required=False,
                                    default="range",
                                    enum=("range", "choices"),
                                    description="a numeric range, or a fixed set",
                                ),
                                #  The three bounds only mean anything for a
                                #  range, and `choices` only for a set. Saying
                                #  so is what lets a form show one or the
                                #  other instead of all four at once.
                                FieldSpec(
                                    "min",
                                    FieldType.FLOAT,
                                    required=False,
                                    visible_when=VisibleWhen("kind", equals="range"),
                                ),
                                FieldSpec(
                                    "max",
                                    FieldType.FLOAT,
                                    required=False,
                                    visible_when=VisibleWhen("kind", equals="range"),
                                ),
                                FieldSpec(
                                    "step",
                                    FieldType.FLOAT,
                                    required=False,
                                    visible_when=VisibleWhen("kind", equals="range"),
                                ),
                                FieldSpec(
                                    "choices",
                                    FieldType.ARRAY,
                                    required=False,
                                    visible_when=VisibleWhen("kind", equals="choices"),
                                ),
                            ),
                        ),
                    ),
                    FieldSpec(
                        "derived",
                        FieldType.JSON,
                        required=False,
                        description="name → expression evaluated before the objective",
                    ),
                    FieldSpec(
                        "constraints",
                        FieldType.ARRAY,
                        required=False,
                        description="expressions that must all hold, e.g. margin >= 0.2",
                        item=FieldSpec(name="constraint", type=FieldType.STRING),
                    ),
                    FieldSpec("top", FieldType.INTEGER, required=False, default=10),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="the best candidates, ranked, with their objective value",
            ),
            examples=[
                {
                    "name": "Best price",
                    "configuration": {
                        "objective": "price * demand",
                        "goal": "maximise",
                        "variables": {"price": {"min": 10, "max": 200, "step": 5}},
                        "derived": {"demand": "max(0, 500 - 3 * price)"},
                        "constraints": ["demand >= 50"],
                    },
                }
            ],
        )

    # -- validation --------------------------------------------------------
    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}

        objective = config.get("objective")
        if not objective:
            result.add_error("configuration.objective must be an expression")
        else:
            try:
                compile_expression(str(objective))
            except ValidationError as exc:
                result.add_error(f"objective: {exc.message}")

        goal = config.get("goal", "maximise")
        if goal not in ("maximise", "minimise"):
            result.add_error("configuration.goal must be 'maximise' or 'minimise'")

        variables = config.get("variables")
        if not isinstance(variables, dict) or not variables:
            return result.add_error(
                "configuration.variables must declare at least one variable"
            )

        try:
            size = _grid_size(variables)
        except ValidationError as exc:
            return result.add_error(exc.message)
        if size > MAX_CANDIDATES:
            result.add_error(
                f"that grid has {size:,} candidates, above the {MAX_CANDIDATES:,} limit — "
                f"widen the step or narrow the range"
            )
        elif size > 20_000:
            result.add_warning(f"{size:,} candidates will be evaluated; this may be slow")

        for label, source in (config.get("derived") or {}).items():
            try:
                compile_expression(str(source))
            except ValidationError as exc:
                result.add_error(f"derived.{label}: {exc.message}")

        for index, source in enumerate(config.get("constraints") or []):
            try:
                compile_expression(str(source))
            except ValidationError as exc:
                result.add_error(f"constraints[{index}]: {exc.message}")

        #  Anything the objective reads must come from somewhere.
        if objective:
            known = set(variables) | set(config.get("derived") or {})
            try:
                unknown = expression_variables(str(objective)) - known
                if unknown:
                    result.add_error(
                        f"objective reads {sorted(unknown)}, which is neither a "
                        f"variable nor derived"
                    )
            except ValidationError:
                pass
        return result

    # -- execution ---------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        objective_source = config.get("objective")
        if not objective_source:
            raise ValidationError("this optimiser defines no objective")

        variables = config.get("variables") or {}
        if not variables:
            raise ValidationError("this optimiser declares no variables to search")

        size = _grid_size(variables)
        if size > MAX_CANDIDATES:
            raise ValidationError(
                f"that grid has {size:,} candidates, above the {MAX_CANDIDATES:,} limit"
            )

        goal = config.get("goal", "maximise")
        top = max(1, min(int(config.get("top", 10)), 200))
        objective = compile_expression(str(objective_source))
        derived = {
            name: compile_expression(str(source))
            for name, source in (config.get("derived") or {}).items()
        }
        constraints = [
            (str(source), compile_expression(str(source)))
            for source in (config.get("constraints") or [])
        ]

        #  A dataset, when given, supplies constants shared by every candidate —
        #  the setting being optimised is the grid, not the rows.
        constants: dict[str, Any] = {}
        if context.input.has_table:
            rows = context.input.rows()
            if len(rows) == 1:
                constants = {k: v for k, v in rows[0].items() if str(k).isidentifier()}
                context.log("using the single input row as constants")
            elif rows:
                context.log(
                    f"input has {len(rows)} rows; an optimiser reads constants, "
                    f"so they were ignored"
                )
        elif context.input.record:
            constants = {
                k: v for k, v in context.input.record.items() if str(k).isidentifier()
            }

        names = list(variables)
        evaluated = 0
        rejected = 0
        failed = 0
        scored: list[dict[str, Any]] = []

        stopped_early = False
        for combination in itertools.product(*(_values(variables[n], n) for n in names)):
            #  A grid is bounded, but a large one over a slow objective is not
            #  bounded in time. Checked here rather than trusting the size
            #  limit alone: the best answer found so far is a better outcome
            #  than being killed with nothing to show.
            if evaluated % 500 == 0 and context.should_stop():
                stopped_early = True
                context.log(
                    f"stopped after {evaluated:,} candidates: "
                    f"{'cancelled' if context.cancelled() else 'out of time'}"
                )
                break
            candidate = dict(zip(names, combination, strict=True))
            env: dict[str, Any] = {**constants, **candidate}
            try:
                for name, tree in derived.items():
                    env[name] = evaluate(tree, env)
                if any(not bool(evaluate(tree, env)) for _, tree in constraints):
                    rejected += 1
                    continue
                value = evaluate(objective, env)
            except Exception:  # noqa: BLE001 - one bad candidate is not a failed run
                failed += 1
                continue
            number = _number(value)
            if number is None:
                failed += 1
                continue
            evaluated += 1
            scored.append(
                {
                    **candidate,
                    **{k: _plain(env[k]) for k in derived},
                    "objective": round(number, 6),
                }
            )

        if not scored:
            raise ValidationError(
                f"no candidate satisfied the constraints — {rejected} rejected, "
                f"{failed} could not be evaluated"
            )

        scored.sort(key=lambda row: row["objective"], reverse=goal == "maximise")
        best = scored[0]
        ranked = [{"rank": i + 1, **row} for i, row in enumerate(scored[:top])]

        payload = ResultPayload.of_table(
            Table.from_rows(ranked),
            kind=ResultKind.TABLE,
            summary={
                "goal": goal,
                "objective": str(objective_source),
                "best": {k: v for k, v in best.items()},
                "evaluated": evaluated,
                "rejected_by_constraints": rejected,
                #  Said plainly, because a partial search is a different claim
                #  from a complete one and the reader has to know which they
                #  are looking at.
                "complete": not stopped_early,
            },
            materialise_as_dataset=True,
            dataset_name=f"{context.definition.name} result",
        )
        return ExecutionOutcome(
            payload=payload,
            metrics={
                "best_objective": best["objective"],
                "evaluated": evaluated,
                "rejected_by_constraints": rejected,
                "failed_candidates": failed,
                "search_space": size,
                "complete": 0 if stopped_early else 1,
            },
            logs=context.logs,
        )


# --------------------------------------------------------------------------
# the grid
# --------------------------------------------------------------------------
def _values(spec: Any, name: str) -> list[Any]:
    if isinstance(spec, dict) and spec.get("choices") is not None:
        choices = spec["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValidationError(f"variable '{name}': choices must be a non-empty list")
        return list(choices)
    if isinstance(spec, list):
        if not spec:
            raise ValidationError(f"variable '{name}': the list of values is empty")
        return list(spec)
    if not isinstance(spec, dict):
        raise ValidationError(f"variable '{name}': expected a range or a list of choices")

    low, high = _number(spec.get("min")), _number(spec.get("max"))
    step = _number(spec.get("step")) or 1.0
    if low is None or high is None:
        raise ValidationError(f"variable '{name}': min and max are required")
    if high < low:
        raise ValidationError(f"variable '{name}': max is below min")
    if step <= 0:
        raise ValidationError(f"variable '{name}': step must be positive")

    count = int(math.floor((high - low) / step)) + 1
    return [round(low + i * step, 10) for i in range(count)]


def _grid_size(variables: dict) -> int:
    total = 1
    for name, spec in variables.items():
        total *= len(_values(spec, name))
        if total > MAX_CANDIDATES:
            return total
    return total


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(float(value)) else float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _plain(value: Any) -> Any:
    return round(value, 6) if isinstance(value, float) else value
