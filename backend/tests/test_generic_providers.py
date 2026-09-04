"""Four providers that are not about any one domain, held to what they claim.

Scoring, risk, projection and data quality are the four things every
condition-, credit- or quality-assessment does, and each of them has one way of
being wrong that is worse than being inaccurate:

* a **score** computed from evidence that was not there, reported as if it
  were;
* a **risk level** that cannot be traced back to the cell of the grid it came
  from;
* a **projection** that answers with a date when the fit does not support one;
* a **quality check** that flags every intermittently-operated machine, which
  is the same as flagging none of them.

Each is pinned here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.plugins.data_quality.checks import CHECKS, assess_series
from app.plugins.projection.projecting import (
    BASIS_CALCULATED,
    BASIS_ESTIMATED,
    BASIS_INFERRED,
    BASIS_UNKNOWN,
    Limit,
    Projection,
    fit_series,
    projection_from_config,
)
from app.plugins.risk_matrix.matrix import matrix_from_config
from app.plugins.scorecard.scoring import scorecard_from_config
from app.shared.errors import ValidationError

# --------------------------------------------------------------------------
# scorecard
# --------------------------------------------------------------------------
CARD = {
    "components": [
        {"name": "wear", "column": "wear_pct", "kind": "linear",
         "good": 0, "bad": 100, "weight": 3},
        {"name": "spares", "column": "spares_days", "kind": "linear",
         "good": 30, "bad": 0, "weight": 1},
        {"name": "sampled", "column": "samples", "kind": "linear",
         "good": 24, "bad": 0, "weight": 1, "missing": "skip"},
    ],
    "bands": [
        {"upto": 40, "label": "poor"},
        {"upto": 75, "label": "fair"},
        {"upto": None, "label": "good"},
    ],
}


def test_a_score_reports_how_much_of_itself_it_could_measure():
    """An absent 'skip' component leaves the weighting, it does not score zero.

    The distinction is the whole point of coverage. Scored as zero, the partial
    record below would read 74 and look like a failing asset; excluded from the
    weighting it reads 92 with two-thirds of the evidence, which is what it is.
    """
    card = scorecard_from_config(CARD)
    complete = card.score({"wear_pct": 10, "spares_days": 30, "samples": 24})
    partial = card.score({"wear_pct": 10, "spares_days": 30})

    assert complete["coverage"] == 1.0
    assert partial["coverage"] == pytest.approx(0.8, abs=1e-6)
    assert partial["score"] == pytest.approx(92.5, abs=1e-6)
    #  Had it been scored as absent-is-bad, this would be 74.
    assert partial["score"] > 90


def test_a_component_states_what_its_absence_means():
    config = {
        "components": [
            {"name": "a", "column": "a", "kind": "linear", "good": 0, "bad": 10},
            {"name": "b", "column": "b", "kind": "linear", "good": 0, "bad": 10,
             "missing": "worst"},
        ]
    }
    card = scorecard_from_config(config)
    answer = card.score({"a": 0})
    #  'worst' is scored, not skipped: the coverage stays complete and the
    #  score falls. Treating it as absent would have reported 100.
    assert answer["coverage"] == 1.0
    assert answer["score"] == 50.0


def test_the_score_is_withheld_below_the_declared_coverage():
    card = scorecard_from_config({**CARD, "min_coverage": 0.9})
    answer = card.score({"wear_pct": 10})
    assert answer["score"] is None
    assert "not enough evidence" in answer["explanation"]


def test_every_component_reports_its_own_reading_and_share():
    card = scorecard_from_config(CARD)
    answer = card.score({"wear_pct": 80, "spares_days": 2, "samples": 24})
    named = {entry["name"]: entry for entry in answer["components"]}
    assert named["wear"]["score"] == 20.0
    assert named["wear"]["share"] > named["spares"]["share"]
    #  The explanation names the weakest components, because that is the part
    #  somebody can act on.
    assert "wear" in answer["explanation"]


def test_a_linear_component_without_its_endpoints_is_refused():
    with pytest.raises(ValidationError):
        scorecard_from_config({"components": [{"column": "x", "kind": "linear"}]})


def test_lower_is_better_is_expressed_by_the_endpoints_not_a_flag():
    card = scorecard_from_config(
        {"components": [{"column": "x", "kind": "linear", "good": 100, "bad": 0}]}
    )
    assert card.score({"x": 100})["score"] == 100.0
    assert card.score({"x": 0})["score"] == 0.0


# --------------------------------------------------------------------------
# risk matrix
# --------------------------------------------------------------------------
MATRIX = {
    "likelihood": {
        "column": "indication",
        "levels": ["low", "medium", "high"],
        "bands": [30, 60],
        "default": "low",
    },
    "consequence": {
        "column": "criticality",
        "levels": ["low", "medium", "high", "critical"],
        "default": "medium",
    },
    "grid": [
        ["LOW", "LOW", "MEDIUM", "HIGH"],
        ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        ["MEDIUM", "HIGH", "CRITICAL", "CRITICAL"],
    ],
    "severity_order": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
}


def test_the_cell_that_was_read_is_reported():
    matrix = matrix_from_config(MATRIX)
    answer = matrix.assess({"indication": 75, "criticality": "critical"})
    assert answer["risk_level"] == "CRITICAL"
    assert answer["likelihood"] == "high"
    assert answer["consequence"] == "critical"
    #  "Why is this CRITICAL" is answered by pointing at the grid.
    assert "high" in answer["explanation"] and "critical" in answer["explanation"]
    assert answer["rank"] == 4


def test_the_same_indication_reads_differently_by_consequence():
    matrix = matrix_from_config(MATRIX)
    fan = matrix.assess({"indication": 45, "criticality": "low"})
    transformer = matrix.assess({"indication": 45, "criticality": "critical"})
    assert fan["risk_level"] == "LOW"
    assert transformer["risk_level"] == "CRITICAL"


def test_a_level_nobody_declared_is_refused_not_rounded():
    matrix = matrix_from_config(MATRIX)
    with pytest.raises(ValidationError):
        matrix.assess({"indication": 10, "criticality": "catastrophic"})


def test_an_axis_that_cannot_be_read_produces_no_cell():
    matrix = matrix_from_config(
        {**MATRIX, "likelihood": {**MATRIX["likelihood"], "default": None}}
    )
    answer = matrix.assess({"criticality": "high"})
    assert answer["risk_level"] is None
    assert "could not be read" in answer["explanation"]


@pytest.mark.parametrize(
    "broken",
    [
        {**MATRIX, "grid": [["LOW", "LOW"]]},
        {**MATRIX, "likelihood": {**MATRIX["likelihood"], "bands": [60, 30]}},
        {**MATRIX, "likelihood": {**MATRIX["likelihood"], "bands": [10, 20, 30]}},
        {**MATRIX, "severity_order": ["LOW", "MEDIUM"]},
    ],
)
def test_a_malformed_matrix_is_refused_with_a_reason(broken):
    with pytest.raises(ValidationError):
        matrix_from_config(broken)


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------
START = datetime(2026, 4, 1)


def _series(values: list[float]):
    stamps = [START + timedelta(days=index) for index in range(len(values))]
    xs = [float(index) for index in range(len(values))]
    return fit_series(xs, values, stamps)


def test_a_limit_already_crossed_is_calculated_not_projected():
    projection = Projection(limits=(Limit("emergency", 50.0),))
    answer = projection.project(_series([40, 44, 48, 52, 56, 60, 64, 68]))
    limit = answer["limits"][0]
    assert limit["basis"] == BASIS_CALCULATED
    assert limit["periods"] == 0.0


def test_a_clean_trend_is_estimated_and_reported_as_a_window():
    projection = Projection(limits=(Limit("emergency", 100.0),), min_points=5)
    answer = projection.project(_series([float(10 + 2 * i) for i in range(20)]))
    limit = answer["limits"][0]
    assert limit["basis"] == BASIS_ESTIMATED
    #  A window, not a day: the standard error of the slope is what makes the
    #  answer honest.
    assert limit["periods_earliest"] is not None
    assert limit["periods_earliest"] <= limit["periods"]
    assert limit["date"] is not None


def test_a_noisy_trend_is_inferred_and_deliberately_undated():
    values = [10, 30, 12, 34, 15, 36, 18, 40, 20, 44, 24, 48, 26, 52]
    projection = Projection(limits=(Limit("emergency", 200.0),), min_r_squared=0.9)
    answer = projection.project(_series([float(v) for v in values]))
    limit = answer["limits"][0]
    assert limit["basis"] == BASIS_INFERRED
    assert limit["date"] is None
    assert limit["periods"] is not None


def test_a_series_moving_away_from_the_limit_says_so():
    projection = Projection(limits=(Limit("emergency", 100.0),))
    answer = projection.project(_series([float(60 - i) for i in range(15)]))
    limit = answer["limits"][0]
    assert limit["basis"] == BASIS_UNKNOWN
    assert "not moving towards" in limit["reason"]


def test_the_soonest_limit_is_the_one_a_reader_acts_on():
    projection = Projection(
        limits=(Limit("warning", 40.0), Limit("emergency", 100.0)), min_points=5
    )
    answer = projection.project(_series([float(10 + 2 * i) for i in range(20)]))
    assert answer["soonest"]["limit"] == "warning"


def test_a_projection_needs_at_least_one_limit():
    with pytest.raises(ValidationError):
        projection_from_config({"limits": []})


# --------------------------------------------------------------------------
# data quality
# --------------------------------------------------------------------------
def _stamps(count: int, minutes: int = 60):
    return [START + timedelta(minutes=minutes * index) for index in range(count)]


def test_a_clean_series_scores_full_marks():
    values = [20.0 + (index % 5) * 0.1 for index in range(200)]
    quality = assess_series(values, _stamps(200), expected_interval_minutes=60)
    assert quality.score == 100.0
    assert quality.flag == "good"
    assert not quality.flags


def test_a_stuck_instrument_is_named_as_one():
    values = [20.0 + (index % 7) * 0.1 for index in range(100)] + [21.0] * 40
    quality = assess_series(values, _stamps(140))
    assert quality.longest_flatline >= 40
    assert quality.score < 70
    assert any("stuck" in flag for flag in quality.flags)


def test_a_duty_cycle_is_not_reported_as_an_instrument_event():
    """The failure the step check was rewritten to avoid.

    A machine that runs by day and stops at night makes a large jump twice a
    day. Comparing the largest jump against the typical jump flagged every such
    asset in the fleet, which is the same as flagging none of them.
    """
    values: list[float] = []
    for _ in range(12):
        values.extend([30.0 + (i % 3) * 0.2 for i in range(16)])
        values.extend([0.4 + (i % 3) * 0.05 for i in range(8)])
    quality = assess_series(values, _stamps(len(values)), checks=("step",))
    assert not quality.flags, quality.flags


def test_a_rescaled_transmitter_is_caught():
    """Celsius reported as Fahrenheit: a plausible rising trend, and a fault."""
    values = [60.0 + (index % 5) * 0.3 for index in range(200)]
    values += [value * 9 / 5 + 32 for value in values[:60]]
    quality = assess_series(values, _stamps(len(values)), checks=("step",))
    assert quality.largest_step_ratio is not None
    assert quality.largest_step_ratio > 12
    assert any("rescaled" in flag for flag in quality.flags)


def test_real_degradation_is_not_read_as_a_rescale():
    """Six weeks of drift moves the level, but never between two samples."""
    values = [40.0 + 0.02 * index + (index % 5) * 0.1 for index in range(400)]
    quality = assess_series(values, _stamps(400), checks=("step",))
    assert not quality.flags, quality.flags


def test_sampling_gaps_are_measured_against_the_declared_interval():
    stamps = _stamps(60)
    stamps += [stamps[-1] + timedelta(hours=9 + index) for index in range(20)]
    values = [10.0 + (index % 4) * 0.1 for index in range(len(stamps))]
    quality = assess_series(values, stamps, expected_interval_minutes=60, checks=("gaps",))
    assert quality.gaps > 0
    assert any("gaps" in flag for flag in quality.flags)


def test_impossible_values_are_the_heaviest_penalty():
    values = [10.0] * 40 + [-500.0] * 4
    quality = assess_series(values, _stamps(44), minimum=0, maximum=100, checks=("range",))
    assert quality.out_of_range == 4
    assert quality.score < 92


def test_disabling_a_check_disables_it():
    values = [10.0] * 60
    with_flatline = assess_series(values, _stamps(60))
    without = assess_series(values, _stamps(60), checks=("missing",))
    assert with_flatline.score < without.score
    assert without.score == 100.0


def test_every_check_is_a_name_the_caller_can_ask_for():
    """A check nobody can select is a check nobody can turn off."""
    assert set(CHECKS) == {
        "missing", "duplicates", "range", "outliers",
        "flatline", "step", "gaps", "drift",
    }
