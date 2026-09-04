"""Projecting a measured trend onto a limit, without inventing precision.

The question is old and general: a number is moving, a line has been drawn, and
somebody wants to know when the two will meet. Disk capacity, tank level,
battery health, brake pad thickness, a bearing's vibration — the arithmetic is
the same and so are the ways of getting it wrong.

The dangerous failure is not being off by a week. It is answering at all when
the data cannot support an answer, because a date carries an authority a range
does not, and nobody downstream re-derives how it was obtained. So every
projection here states its **basis**, and the four values are not degrees of
confidence in one kind of answer — they are four different kinds:

    calculated   the limit is already crossed. Not a projection; a reading.
    estimated    a fit good enough to date, reported as a window from the
                 standard error of the slope, not as a single day.
    inferred     movement is towards the limit but the fit is too weak to
                 date. A direction and an ordering, and deliberately no date.
    unknown      not moving towards the limit, or too little to say.

A caller that treats `inferred` as `estimated` has thrown away the only thing
separating a projection from a guess, which is why the basis travels beside the
number rather than in a footnote.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.shared.errors import ValidationError

BASIS_CALCULATED = "calculated"
BASIS_ESTIMATED = "estimated"
BASIS_INFERRED = "inferred"
BASIS_UNKNOWN = "unknown"

DIRECTIONS = ("rising", "falling")


@dataclass(frozen=True)
class Limit:
    """A boundary worth knowing the distance to."""

    name: str
    value: float


@dataclass
class Fit:
    """A least-squares line, with the uncertainty that makes it usable."""

    points: int
    slope: float | None          # units per period
    intercept: float | None
    r_squared: float | None
    slope_stderr: float | None
    last_value: float | None
    last_time: datetime | None
    span: float                  # periods covered by the fit

    @property
    def usable(self) -> bool:
        return self.slope is not None and self.points >= 2


def fit_series(
    xs: list[float], ys: list[float], stamps: list[datetime | None]
) -> Fit:
    """Fit y on x and report how well the line holds.

    `slope_stderr` is what turns a projection into a window. Without it the
    only honest output would be a date, and a date computed from six noisy
    readings is the most over-trusted number in condition monitoring.
    """
    n = len(xs)
    last_value = ys[-1] if ys else None
    last_time = stamps[-1] if stamps else None
    span = (xs[-1] - xs[0]) if n >= 2 else 0.0
    if n < 2:
        return Fit(n, None, None, None, None, last_value, last_time, span)

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return Fit(n, None, None, None, None, last_value, last_time, span)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x

    syy = sum((y - mean_y) ** 2 for y in ys)
    residual = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys, strict=False))
    r_squared = None if syy <= 0 else max(0.0, 1.0 - residual / syy)
    stderr = None
    if n > 2:
        variance = residual / (n - 2)
        stderr = math.sqrt(variance / sxx) if variance >= 0 else None
    return Fit(n, slope, intercept, r_squared, stderr, last_value, last_time, span)


def _crossing(current: float, slope: float, target: float) -> float | None:
    """Periods until a value moving at `slope` reaches `target`."""
    gap = target - current
    if slope == 0:
        return None
    periods = gap / slope
    return periods if periods >= 0 else None


@dataclass
class Projection:
    """The configured question: which limits, in which direction, how strictly."""

    limits: tuple[Limit, ...]
    direction: str = "rising"
    min_points: int = 5
    min_r_squared: float = 0.35
    horizon: float = 365.0
    #  Width of the reported window, in standard errors of the slope.
    confidence_multiplier: float = 1.96

    def project(self, fit: Fit) -> dict[str, Any]:
        """Distance to every limit, each with the basis it rests on."""
        answers: list[dict[str, Any]] = []
        for limit in self.limits:
            answers.append(self._one(fit, limit))
        #  The soonest limit that is actually going to be reached is the one a
        #  reader acts on; reporting every limit and no summary makes them do
        #  the comparison by eye.
        dated = [a for a in answers if a["periods"] is not None]
        soonest = min(dated, key=lambda a: a["periods"]) if dated else None
        crossed = [a for a in answers if a["basis"] == BASIS_CALCULATED]
        return {
            "limits": answers,
            "soonest": soonest,
            "crossed": [a["limit"] for a in crossed],
            "slope": None if fit.slope is None else round(fit.slope, 8),
            "r_squared": None if fit.r_squared is None else round(fit.r_squared, 4),
            "points": fit.points,
            "span": round(fit.span, 4),
            "last_value": fit.last_value,
        }

    def _one(self, fit: Fit, limit: Limit) -> dict[str, Any]:
        blank = {
            "limit": limit.name,
            "limit_value": limit.value,
            "periods": None,
            "periods_earliest": None,
            "periods_latest": None,
            "date": None,
            "basis": BASIS_UNKNOWN,
            "reason": "",
        }
        if fit.last_value is None:
            return {**blank, "reason": "no readings"}

        beyond = (
            fit.last_value >= limit.value
            if self.direction == "rising"
            else fit.last_value <= limit.value
        )
        if beyond:
            return {
                **blank,
                "periods": 0.0,
                "basis": BASIS_CALCULATED,
                "date": fit.last_time.date().isoformat() if fit.last_time else None,
                "reason": (
                    f"already at or past {limit.name}: "
                    f"{fit.last_value:g} vs {limit.value:g}"
                ),
            }
        if not fit.usable or fit.points < 2:
            return {**blank, "reason": "too few readings to fit a trend"}

        towards = fit.slope > 0 if self.direction == "rising" else fit.slope < 0
        if not towards:
            return {
                **blank,
                "reason": f"not moving towards {limit.name}",
            }

        periods = _crossing(fit.last_value, fit.slope, limit.value)
        if periods is None or periods > self.horizon:
            return {
                **blank,
                "basis": BASIS_UNKNOWN if periods is None else BASIS_INFERRED,
                "reason": (
                    f"beyond the {self.horizon:g}-period horizon"
                    if periods is not None
                    else "the trend does not reach the limit"
                ),
            }

        strong = (
            fit.points >= self.min_points
            and fit.r_squared is not None
            and fit.r_squared >= self.min_r_squared
        )
        if not strong:
            #  A direction and an ordering, and deliberately no date: the fit
            #  is not good enough to put one in front of a planner.
            return {
                **blank,
                "periods": round(periods, 2),
                "basis": BASIS_INFERRED,
                "reason": (
                    f"moving towards {limit.name}, but the fit is weak "
                    f"(R²={fit.r_squared if fit.r_squared is not None else 0:.2f} "
                    f"over {fit.points} readings)"
                ),
            }

        earliest, latest = self._window(fit, limit, periods)
        return {
            "limit": limit.name,
            "limit_value": limit.value,
            "periods": round(periods, 2),
            "periods_earliest": None if earliest is None else round(earliest, 2),
            "periods_latest": None if latest is None else round(latest, 2),
            "date": _shift(fit.last_time, periods),
            "date_earliest": _shift(fit.last_time, earliest),
            "date_latest": _shift(fit.last_time, latest),
            "basis": BASIS_ESTIMATED,
            "reason": (
                f"at {fit.slope:+.4g} per period from {fit.last_value:g}, "
                f"{limit.name} ({limit.value:g}) is about {periods:.0f} periods away"
            ),
        }

    def _window(
        self, fit: Fit, limit: Limit, periods: float
    ) -> tuple[float | None, float | None]:
        """How wide the answer honestly is, from the slope's standard error."""
        if fit.slope is None or fit.slope_stderr is None:
            return None, None
        if fit.slope_stderr == 0:
            #  A perfect fit. The window has zero width, which is a window —
            #  reporting no window at all would say the projection could not
            #  be bounded, when in fact it could be bounded exactly.
            return periods, periods
        margin = self.confidence_multiplier * fit.slope_stderr
        fast = fit.slope + margin if fit.slope > 0 else fit.slope - margin
        slow = fit.slope - margin if fit.slope > 0 else fit.slope + margin
        earliest = _crossing(fit.last_value or 0.0, fast, limit.value)
        slowest = _crossing(fit.last_value or 0.0, slow, limit.value)
        if slowest is None or slowest > self.horizon:
            #  The slow end of the interval never gets there inside the
            #  horizon. Saying so is the point of reporting a window at all.
            slowest = None
        return (earliest if earliest is not None else periods), slowest


def _shift(moment: datetime | None, periods: float | None) -> str | None:
    if moment is None or periods is None:
        return None
    return (moment + timedelta(days=float(periods))).date().isoformat()


def projection_from_config(config: dict[str, Any]) -> Projection:
    raw_limits = config.get("limits") or []
    if not isinstance(raw_limits, list) or not raw_limits:
        raise ValidationError("a projection needs at least one limit")
    limits: list[Limit] = []
    for entry in raw_limits:
        if not isinstance(entry, dict):
            raise ValidationError("every limit must be an object")
        value = entry.get("value")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ValidationError("every limit needs a numeric 'value'") from None
        limits.append(Limit(name=str(entry.get("name") or f"limit {numeric:g}"),
                            value=numeric))

    direction = str(config.get("direction", "rising"))
    if direction not in DIRECTIONS:
        raise ValidationError(
            f"unknown direction '{direction}'", details={"allowed": list(DIRECTIONS)}
        )
    horizon = float(config.get("horizon", 365) or 365)
    if horizon <= 0:
        raise ValidationError("the horizon must be positive")
    return Projection(
        limits=tuple(limits),
        direction=direction,
        min_points=int(config.get("min_points", 5) or 5),
        min_r_squared=float(config.get("min_r_squared", 0.35) or 0.0),
        horizon=horizon,
        confidence_multiplier=float(config.get("confidence_multiplier", 1.96) or 1.96),
    )
