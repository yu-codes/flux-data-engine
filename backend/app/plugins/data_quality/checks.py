"""What can be wrong with a measurement series, checked one series at a time.

Every analysis downstream of a sensor assumes the sensor was working, and none
of them can tell the difference between a reading that changed and a reading
that stopped arriving. That is the whole failure chain this exists to break:

    instrument fault -> a value that looks anomalous -> an anomaly -> a
    decision taken about equipment that is fine

The checks are the ones that catch that chain rather than the ones that are
easiest to compute:

* **missing** — how much never arrived.
* **flatline** — the longest run of identical readings. A healthy sensor is
  never *exactly* constant, so this is the strongest single indication that
  the instrument, and not the thing it watches, is what stopped moving.
* **gaps** — readings that are further apart than the declared interval. An
  average computed over a series with a hole is an average of a different
  period than the one it claims.
* **step** — a *sustained* change in level: the mean of the readings just
  after some moment against the mean just before it, in units of the series'
  own noise. Sustained is the whole of it. A spike goes up and comes back, and
  a machine degrading over six weeks moves imperceptibly in any twelve-hour
  window; a transmitter that is rescaled, rewired or reconfigured moves once
  and stays moved. This is the check that catches a temperature transmitter
  switched from Celsius to Fahrenheit, which is otherwise a perfectly
  plausible rising trend and one of the most expensive false alarms a
  maintenance system can raise.
* **outliers** — by interquartile range rather than by standard deviation,
  because a series containing spikes has its standard deviation set *by* the
  spikes, and a z-score then reports that nothing is unusual.
* **out of range** — physically impossible values. A negative flow, a
  temperature above the boiling point of the oil: these are unit errors and
  wiring faults, and they are the ones that reach a chart looking like data.
* **drift** — the first half of the window against the second, in units of
  the series' own noise. Real degradation drifts too; this check does not
  decide which it is, it reports that the question exists.

Which checks apply is the caller's decision, and it is a real one rather than
a convenience. A series that mixes two operating regimes — a machine that runs
by day and stops at night — has a bimodal distribution and an eight-hour hole
every night, so its outliers are its idle readings and its gaps are its
weekends. Running every check over every series produces a fleet where
everything is suspect, which is the same as a fleet where nothing is.

So a caller checks sampling regularity against the raw stream, and distribution
and instrument behaviour against the readings the analysis will actually use.

Nothing here decides what to *do*. It produces the numbers a policy is applied
to, because "reject a reading" is a decision that belongs to whoever owns the
consequences of rejecting it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

CHECKS = ("missing", "duplicates", "range", "outliers", "flatline", "step",
          "gaps", "drift")

#  Penalties applied to a 100-point quality score. Deliberately unequal: a
#  stuck sensor invalidates the series, while a few missing readings degrade it.
PENALTY = {
    "missing": 60.0,       # multiplied by the missing fraction
    "gaps": 25.0,          # multiplied by the fraction of intervals that gape
    "outliers": 40.0,      # multiplied by the outlier fraction
    "out_of_range": 100.0,  # multiplied by the out-of-range fraction
    "flatline": 45.0,      # multiplied by the share of the series stuck
    "duplicates": 20.0,
    "drift": 10.0,
    "step": 50.0,
}


@dataclass
class SeriesQuality:
    """The verdict on one measurement of one subject."""

    readings: int = 0
    present: int = 0
    missing: int = 0
    duplicates: int = 0
    outliers: int = 0
    out_of_range: int = 0
    longest_flatline: int = 0
    largest_step_ratio: float | None = None
    gaps: int = 0
    largest_gap_minutes: float | None = None
    drift_sigma: float | None = None
    score: float = 100.0
    flags: list[str] = field(default_factory=list)

    @property
    def missing_pct(self) -> float:
        return 0.0 if not self.readings else round(100 * self.missing / self.readings, 3)

    def to_dict(self, **identity: Any) -> dict[str, Any]:
        return {
            **identity,
            "readings": self.readings,
            "present": self.present,
            "missing": self.missing,
            "missing_pct": self.missing_pct,
            "duplicates": self.duplicates,
            "outliers": self.outliers,
            "out_of_range": self.out_of_range,
            "longest_flatline": self.longest_flatline,
            "largest_step_ratio": self.largest_step_ratio,
            "gaps": self.gaps,
            "largest_gap_minutes": self.largest_gap_minutes,
            "drift_sigma": self.drift_sigma,
            "quality_score": round(self.score, 2),
            "quality_flag": self.flag,
            "issues": ", ".join(self.flags),
        }

    @property
    def flag(self) -> str:
        if self.score >= 90:
            return "good"
        if self.score >= 70:
            return "acceptable"
        if self.score >= 40:
            return "suspect"
        return "bad"


def _quantile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _level_shift(values: list[float], window: int = 12) -> float | None:
    """The largest sustained change of level, in units of the series' noise.

    Computed with prefix sums so the whole series costs one pass rather than
    one pass per candidate moment — a fleet is tens of thousands of series and
    the naive form is quadratic in the length of each.

    The scale is the standard deviation of successive differences, which is a
    measure of noise that a slow trend does not inflate: degradation moves a
    reading by a fraction of its noise between one sample and the next, while
    it moves the *level* over weeks. That is exactly the property that lets
    this separate a rescaled instrument from a failing bearing.
    """
    n = len(values)
    if n < window * 3:
        return None
    diffs = [abs(values[i] - values[i - 1]) for i in range(1, n)]
    scale = _stdev(diffs)
    if not scale or scale <= 0:
        return None
    #  Two window means differ by roughly scale * sqrt(2/window) under noise
    #  alone; anything much larger is a shift rather than a fluctuation.
    expected = scale * math.sqrt(2.0 / window)

    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)

    largest = 0.0
    for at in range(window, n - window + 1):
        before = (prefix[at] - prefix[at - window]) / window
        after = (prefix[at + window] - prefix[at]) / window
        largest = max(largest, abs(after - before))
    return largest / expected if expected > 0 else None


def assess_series(
    values: list[float | None],
    stamps: list[datetime | None],
    *,
    expected_interval_minutes: float | None = None,
    gap_tolerance: float = 2.0,
    flatline_readings: int = 6,
    step_ratio: float = 12.0,
    outlier_factor: float = 3.0,
    minimum: float | None = None,
    maximum: float | None = None,
    duplicates: int = 0,
    drift_sigma_limit: float = 3.0,
    checks: tuple[str, ...] = CHECKS,
) -> SeriesQuality:
    """Score one ordered series. Values and stamps are parallel and sorted."""
    quality = SeriesQuality(readings=len(values), duplicates=duplicates)
    present = [v for v in values if v is not None]
    quality.present = len(present)
    quality.missing = len(values) - len(present)
    enabled = set(checks)

    if quality.missing and "missing" in enabled:
        quality.score -= PENALTY["missing"] * (quality.missing / max(1, len(values)))
        if quality.missing_pct >= 5:
            quality.flags.append(f"{quality.missing_pct:.1f}% of readings missing")

    if duplicates and "duplicates" in enabled:
        quality.score -= min(PENALTY["duplicates"], PENALTY["duplicates"] * duplicates / 10)
        quality.flags.append(f"{duplicates} duplicated timestamps")

    # -- out of range ------------------------------------------------------
    if "range" in enabled and present and (minimum is not None or maximum is not None):
        bad = [
            v for v in present
            if (minimum is not None and v < minimum)
            or (maximum is not None and v > maximum)
        ]
        quality.out_of_range = len(bad)
        if bad:
            quality.score -= PENALTY["out_of_range"] * (len(bad) / len(present))
            quality.flags.append(
                f"{len(bad)} readings outside the physical range "
                f"[{minimum if minimum is not None else '-'}, "
                f"{maximum if maximum is not None else '-'}]"
            )

    # -- outliers, by IQR --------------------------------------------------
    if "outliers" in enabled and len(present) >= 8:
        ordered = sorted(present)
        q1, q3 = _quantile(ordered, 0.25), _quantile(ordered, 0.75)
        spread = q3 - q1
        if spread > 0:
            low, high = q1 - outlier_factor * spread, q3 + outlier_factor * spread
            far = [v for v in present if v < low or v > high]
            quality.outliers = len(far)
            if far:
                quality.score -= PENALTY["outliers"] * (len(far) / len(present))
                quality.flags.append(f"{len(far)} readings far outside the usual spread")

    # -- flatline ----------------------------------------------------------
    run = best = 0
    previous: float | None = None
    for value in values:
        if value is not None and previous is not None and value == previous:
            run += 1
            best = max(best, run + 1)
        else:
            run = 0
        if value is not None:
            previous = value
    quality.longest_flatline = best
    if "flatline" in enabled and best >= flatline_readings:
        #  Scaled by how long the instrument was stuck, not by what share of
        #  the series that was. Forty hours of a dead transmitter is forty
        #  hours whether the series is a week long or a year long, and scaling
        #  by the share let a long series absorb a serious fault into an
        #  "acceptable" score.
        severity = min(1.0, best / max(24.0, flatline_readings * 4.0))
        quality.score -= PENALTY["flatline"] * severity
        quality.flags.append(
            f"{best} consecutive identical readings — the instrument may be stuck"
        )

    # -- step change -------------------------------------------------------
    if "step" in enabled and len(present) >= 40:
        ratio = _level_shift(present)
        quality.largest_step_ratio = None if ratio is None else round(ratio, 2)
        if ratio is not None and ratio >= step_ratio:
            quality.score -= PENALTY["step"] * min(
                1.0, (ratio - step_ratio) / (step_ratio * 2) + 0.5
            )
            quality.flags.append(
                f"the level of this series shifted {ratio:.0f}× its own noise "
                f"and stayed shifted — the instrument was probably rescaled, "
                f"rewired or replaced"
            )

    # -- sampling gaps -----------------------------------------------------
    timed = [s for s in stamps if s is not None]
    if "gaps" in enabled and expected_interval_minutes and len(timed) >= 2:
        limit = expected_interval_minutes * gap_tolerance
        intervals = [
            (timed[i] - timed[i - 1]).total_seconds() / 60.0 for i in range(1, len(timed))
        ]
        wide = [gap for gap in intervals if gap > limit]
        quality.gaps = len(wide)
        quality.largest_gap_minutes = round(max(intervals), 2) if intervals else None
        if wide:
            share = len(wide) / max(1, len(intervals))
            quality.score -= PENALTY["gaps"] * min(1.0, share * 4)
            quality.flags.append(
                f"{len(wide)} sampling gaps longer than {limit:g} minutes"
            )

    # -- drift -------------------------------------------------------------
    if "drift" in enabled and len(present) >= 10:
        half = len(present) // 2
        first, second = present[:half], present[half:]
        spread = _stdev(first)
        if spread:
            shift = abs(_mean(second) - _mean(first)) / spread
            quality.drift_sigma = round(shift, 3)
            if shift >= drift_sigma_limit:
                quality.score -= PENALTY["drift"]
                quality.flags.append(
                    f"the series' level moved {shift:.1f}σ between its two halves"
                )

    quality.score = max(0.0, min(100.0, quality.score))
    return quality
