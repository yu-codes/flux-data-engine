"""Mathematical model provider: fit a stated function to observed data.

    y = a·x + b        y = a·x² + b·x + c        y = a·e^(b·x)

This is a Model that is neither trained nor guessed at. The shape of the
relationship is chosen by the person, and least squares determines its
coefficients — the answer is deterministic, reproducible, and reported with the
residual statistics needed to judge whether the chosen shape was appropriate.

It is here because "mathematical" was a category the platform advertised and
could not deliver: the Model Library listed it with "none yet" beside it.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.modules.model.domain.entities import ModelDefinition, ModelType, RuntimeKind
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionKind,
    ExecutionOutcome,
    PluginDescriptor,
)
from app.shared.contracts import (
    Contract,
    ContractShape,
    FieldSpec,
    FieldType,
    ValidationResult,
)
from app.shared.errors import ValidationError
from app.shared.payloads import ResultKind, ResultPayload
from app.shared.tabular import Table

PLUGIN_KEY = "curve-fit"

#  Every family is fitted by ordinary least squares on a linear basis; the
#  non-linear ones get there by transforming the response first, which keeps the
#  whole provider closed-form and free of an optimiser.
FAMILIES = ("linear", "polynomial", "exponential", "power", "logarithmic")


class CurveFitPlugin:
    """Least-squares fitting of a chosen function family."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Curve fit",
            model_type=ModelType.MATHEMATICAL,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Fits a stated function — linear, polynomial, exponential, power "
                "or logarithmic — to two columns by least squares, and reports "
                "the coefficients with R² and RMSE. Deterministic: the same rows "
                "always give the same equation."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.CALCULATION, ExecutionKind.PREDICTION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec("x", FieldType.STRING, description="the independent column"),
                    FieldSpec("y", FieldType.STRING, description="the observed column"),
                    FieldSpec(
                        "family",
                        FieldType.STRING,
                        required=False,
                        default="linear",
                        enum=FAMILIES,
                    ),
                    FieldSpec(
                        "degree",
                        FieldType.INTEGER,
                        required=False,
                        default=2,
                        description="polynomial only; 2 to 6",
                    ),
                    FieldSpec(
                        "predict_for",
                        FieldType.ARRAY,
                        required=False,
                        description="x values to evaluate the fitted curve at",
                    ),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="each observation with its fitted value and residual",
            ),
            examples=[
                {
                    "name": "Wind against pressure",
                    "configuration": {
                        "x": "min_pressure",
                        "y": "wind_ms",
                        "family": "linear",
                    },
                }
            ],
        )

    # -- validation --------------------------------------------------------
    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        for key in ("x", "y"):
            if not config.get(key):
                result.add_error(f"configuration.{key} must name a column")

        family = config.get("family", "linear")
        if family not in FAMILIES:
            result.add_error(f"unknown family '{family}'; expected one of {list(FAMILIES)}")

        if family == "polynomial":
            degree = config.get("degree", 2)
            if not isinstance(degree, int) or not 2 <= degree <= 6:
                result.add_error("configuration.degree must be an integer from 2 to 6")

        declared = set(definition.input_contract.names)
        if declared:
            missing = {config.get("x"), config.get("y")} - declared - {None}
            if missing:
                result.add_warning(
                    f"reads {sorted(missing)}, which the input contract does not declare"
                )
        return result

    # -- dataset check -----------------------------------------------------
    def check_dataset(self, definition: ModelDefinition, schema_fields) -> ValidationResult:
        """The columns this fit needs are named in its configuration, not its
        contract, so only this provider can tell whether a dataset supplies
        them."""
        result = ValidationResult()
        config = definition.configuration or {}
        available = {field.name: field for field in schema_fields}
        for key in ("x", "y"):
            column = config.get(key)
            if not column:
                continue
            found = available.get(column)
            if found is None:
                result.add_error(
                    f"the dataset has no column '{column}' for {key}"
                )
            elif found.type.value not in ("integer", "float", "any"):
                result.add_warning(
                    f"'{column}' is {found.type.value}; unparseable rows are dropped"
                )
        return result

    # -- execution ---------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        x_name, y_name = config.get("x"), config.get("y")
        family = config.get("family", "linear")
        degree = int(config.get("degree", 2))

        if not context.input.has_table:
            raise ValidationError(
                "a curve fit needs a dataset; a single record has no shape"
            )

        pairs = _pairs(context.input.rows(), x_name, y_name)
        if len(pairs) < _minimum_points(family, degree):
            raise ValidationError(
                f"a {family} fit needs at least {_minimum_points(family, degree)} "
                f"complete rows; {len(pairs)} were usable"
            )

        xs = np.array([p[0] for p in pairs], dtype=float)
        ys = np.array([p[1] for p in pairs], dtype=float)
        coefficients, predict, equation = _fit(family, degree, xs, ys, context.log)

        fitted = predict(xs)
        residuals = ys - fitted
        ss_res = float(np.sum(residuals**2))
        ss_tot = float(np.sum((ys - ys.mean()) ** 2))
        #  A perfectly flat response has no variance to explain, so R² is
        #  undefined rather than 1: saying "explains everything" about a
        #  constant would be a lie the number cannot support.
        r_squared = None if ss_tot == 0 else round(1 - ss_res / ss_tot, 6)
        rmse = round(math.sqrt(ss_res / len(xs)), 6)

        rows = [
            {
                x_name: float(x),
                y_name: float(y),
                "fitted": round(float(f), 6),
                "residual": round(float(r), 6),
            }
            for x, y, f, r in zip(xs, ys, fitted, residuals, strict=True)
        ]

        requested = config.get("predict_for") or []
        predictions = []
        if requested:
            wanted = np.array([float(v) for v in requested], dtype=float)
            predictions = [
                {x_name: float(v), "predicted": round(float(p), 6)}
                for v, p in zip(wanted, predict(wanted), strict=True)
            ]

        summary: dict[str, Any] = {
            "equation": equation,
            "family": family,
            "points": len(rows),
            "r_squared": r_squared,
            "rmse": rmse,
        }
        if predictions:
            summary["predictions"] = predictions

        payload = ResultPayload.of_table(
            Table.from_rows(rows),
            kind=ResultKind.TABLE,
            summary=summary,
            materialise_as_dataset=True,
            dataset_name=f"{context.definition.name} result",
        )
        return ExecutionOutcome(
            payload=payload,
            metrics={
                "r_squared": r_squared,
                "rmse": rmse,
                "points": len(rows),
                "coefficients": coefficients,
            },
            logs=context.logs,
        )


# --------------------------------------------------------------------------
# fitting
# --------------------------------------------------------------------------
def _minimum_points(family: str, degree: int) -> int:
    return degree + 1 if family == "polynomial" else 2


def _pairs(rows: list[dict], x_name: str, y_name: str) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for row in rows:
        x, y = _number(row.get(x_name)), _number(row.get(y_name))
        if x is not None and y is not None:
            out.append((x, y))
    return out


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(float(value)) else float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _fit(family: str, degree: int, xs, ys, log):
    """Return (coefficients, predict, human-readable equation).

    The transformed families drop rows the transform cannot take — a log needs a
    positive value — and say how many, because a fit quietly computed on half
    the data is a misleading fit.
    """
    if family == "linear":
        slope, intercept = np.polyfit(xs, ys, 1)
        return (
            {"slope": round(float(slope), 6), "intercept": round(float(intercept), 6)},
            lambda v: slope * v + intercept,
            f"y = {slope:.4g}·x + {intercept:.4g}",
        )

    if family == "polynomial":
        coefficients = np.polyfit(xs, ys, degree)
        terms = " + ".join(
            f"{c:.4g}·x^{degree - i}" if degree - i > 1 else
            (f"{c:.4g}·x" if degree - i == 1 else f"{c:.4g}")
            for i, c in enumerate(coefficients)
        )
        return (
            {f"c{degree - i}": round(float(c), 6) for i, c in enumerate(coefficients)},
            lambda v: np.polyval(coefficients, v),
            f"y = {terms}",
        )

    if family == "exponential":
        keep = ys > 0
        _warn_dropped(log, keep, "exponential fit needs a positive y")
        slope, intercept = np.polyfit(xs[keep], np.log(ys[keep]), 1)
        a, b = float(np.exp(intercept)), float(slope)
        return (
            {"a": round(a, 6), "b": round(b, 6)},
            lambda v: a * np.exp(b * v),
            f"y = {a:.4g}·e^({b:.4g}·x)",
        )

    if family == "power":
        keep = (xs > 0) & (ys > 0)
        _warn_dropped(log, keep, "power fit needs positive x and y")
        slope, intercept = np.polyfit(np.log(xs[keep]), np.log(ys[keep]), 1)
        a, b = float(np.exp(intercept)), float(slope)
        return (
            {"a": round(a, 6), "b": round(b, 6)},
            lambda v: a * np.power(v, b),
            f"y = {a:.4g}·x^{b:.4g}",
        )

    keep = xs > 0
    _warn_dropped(log, keep, "logarithmic fit needs a positive x")
    slope, intercept = np.polyfit(np.log(xs[keep]), ys[keep], 1)
    a, b = float(slope), float(intercept)
    return (
        {"a": round(a, 6), "b": round(b, 6)},
        lambda v: a * np.log(np.maximum(v, 1e-12)) + b,
        f"y = {a:.4g}·ln(x) + {b:.4g}",
    )


def _warn_dropped(log, keep, reason: str) -> None:
    dropped = int((~keep).sum())
    if dropped:
        log(f"{dropped} rows excluded: {reason}")
    if int(keep.sum()) < 2:
        raise ValidationError(f"too few usable rows after filtering — {reason}")
