"""The reshaping and time-series vocabulary, held to what it claims.

`test_columnar_transforms.py` proves these eight are columnar and exercised.
This file is about their answers, and specifically about the answers that are
easy to get subtly wrong:

* a window that does not restart at each group is a window that reports one
  asset's history as another's;
* a slope measured per sample is a different number after the sampling
  interval changes, and comparing two assets on it is meaningless;
* a flat series fits a straight line perfectly, and calling that R² = 1 lets a
  stuck sensor claim the strongest trend in the fleet;
* two readings for the same cell is normal in any real store, and silently
  keeping whichever arrived last makes the answer depend on file order.

The fixture is deliberately awkward: two subjects sampled at different rates,
a gap, a duplicate reading, and a null.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.plugins.python_function import library
from app.shared.errors import ValidationError
from app.shared.tabular import Table

START = datetime(2026, 3, 1)


def at(hours: float) -> str:
    return (START + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def apply(key: str, table: Table, **options) -> Table:
    transform = library.get(key)
    return transform.apply(table, transform.parameters.coerce_record(options))


@pytest.fixture
def readings() -> Table:
    """Long-format readings: two subjects, two measurements, one hour apart.

    `slow` is sampled every three hours rather than every one, which is what
    makes a per-sample rate the wrong answer and a per-hour rate the right one.
    """
    rows = []
    for hour in range(6):
        rows.append({"ts": at(hour), "unit": "fast", "measure": "temp",
                     "value": 60.0 + 2 * hour})
        rows.append({"ts": at(hour), "unit": "fast", "measure": "vib",
                     "value": 3.0 + 0.5 * hour})
    for step in range(6):
        rows.append({"ts": at(3 * step), "unit": "slow", "measure": "temp",
                     "value": 60.0 + 6 * step})
        rows.append({"ts": at(3 * step), "unit": "slow", "measure": "vib",
                     "value": 4.0})
    #  A third subject exists only to hold a repeated cell, and `slow` carries
    #  a reading nobody recorded a value for. Both live away from the two
    #  series the arithmetic assertions read, because a fixture that tests
    #  duplicate handling by corrupting the trend tests nothing twice.
    rows.append({"ts": at(0), "unit": "dup", "measure": "temp", "value": 60.0})
    rows.append({"ts": at(0), "unit": "dup", "measure": "temp", "value": 64.0})
    rows.append({"ts": at(1), "unit": "slow", "measure": "temp", "value": None})
    return Table.from_rows(rows)


# --------------------------------------------------------------------------
# reshaping
# --------------------------------------------------------------------------
def test_pivot_wider_puts_measurements_on_one_row(readings):
    wide = apply(
        "pivot_wider",
        readings,
        keys=["unit", "ts"],
        name_from="measure",
        value_from="value",
    )
    assert {"unit", "ts", "temp", "vib"} <= set(wide.columns)
    row = next(r for r in wide.to_rows() if r["unit"] == "fast" and r["ts"] == at(1))
    assert row["temp"] == 62.0
    assert row["vib"] == 3.5


def test_a_repeated_cell_is_aggregated_not_silently_replaced(readings):
    """Two readings for one cell is a fact of every real store."""
    mean = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value")
    last = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value", aggregation="last")
    averaged = next(r for r in mean.to_rows() if r["unit"] == "dup")
    latest = next(r for r in last.to_rows() if r["unit"] == "dup")
    #  60.0 and the repeat at 64.0.
    assert averaged["temp"] == 62.0
    assert latest["temp"] == 64.0


def test_pivot_and_unpivot_round_trip(readings):
    wide = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value", aggregation="last")
    back = apply("unpivot_longer", wide, keys=["unit", "ts"],
                 columns=["temp", "vib"], name_to="measure", value_to="value")
    #  Every (unit, ts, measure) that had a value comes back exactly once.
    keys = {(r["unit"], r["ts"], r["measure"]) for r in back.to_rows()}
    assert len(keys) == back.num_rows
    assert ("fast", at(0), "temp") in keys


def test_unpivot_keeps_nulls_when_asked(readings):
    wide = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value")
    dropped = apply("unpivot_longer", wide, keys=["unit", "ts"], columns=["vib"])
    kept = apply("unpivot_longer", wide, keys=["unit", "ts"], columns=["vib"],
                 drop_missing=False)
    assert kept.num_rows > dropped.num_rows


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------
def test_resample_puts_two_sampling_rates_on_one_grid(readings):
    daily = apply(
        "resample_time",
        readings,
        timestamp="ts",
        period="day",
        group_by=["unit", "measure"],
        measures={"value": "mean"},
    )
    rows = {(r["unit"], r["measure"]): r for r in daily.to_rows()}
    #  Both subjects now have exactly one row for the day, whatever rate they
    #  were sampled at - which is the only way the two can be compared.
    assert rows[("fast", "temp")]["period"] == "2026-03-01"
    assert rows[("fast", "temp")]["sample_count"] == 6
    #  Seven, because the reading with no value is still a reading that arrived.
    assert rows[("slow", "temp")]["sample_count"] == 7


def test_a_reading_with_an_unreadable_timestamp_belongs_to_no_period():
    table = Table.from_rows(
        [{"ts": at(0), "v": 1.0}, {"ts": "not a date", "v": 99.0}]
    )
    daily = apply("resample_time", table, timestamp="ts", period="day",
                  measures={"v": "mean"})
    assert daily.num_rows == 1
    assert daily.to_rows()[0]["v_mean"] == 1.0


# --------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------
def test_a_rolling_window_restarts_at_each_group(readings):
    wide = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value")
    rolled = apply("rolling_stats", wide, column="temp", window=3,
                   statistics=["mean"], group_by=["unit"], order_by="ts",
                   min_periods=2)
    by_unit = {}
    for row in rolled.to_rows():
        by_unit.setdefault(row["unit"], []).append(row)
    for rows in by_unit.values():
        rows.sort(key=lambda r: r["ts"])
        #  The first reading of each group has nothing behind it.
        assert rows[0]["temp_roll_mean3"] is None


def test_rolling_slope_is_per_hour_not_per_sample(readings):
    """The number that makes two differently-sampled assets comparable."""
    wide = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value")
    rolled = apply("rolling_stats", wide, column="temp", window=4,
                   statistics=["slope"], group_by=["unit"], order_by="ts",
                   min_periods=3)
    latest = {}
    for row in sorted(rolled.to_rows(), key=lambda r: r["ts"]):
        if row["temp_roll_slope4"] is not None:
            latest[row["unit"]] = row["temp_roll_slope4"]
    #  fast rises 2°C every hour; slow rises 6°C every three hours. Both are
    #  2°C/hour, and a per-sample slope would have said 2 and 6.
    assert latest["fast"] == pytest.approx(2.0, abs=1e-6)
    assert latest["slow"] == pytest.approx(2.0, abs=1e-6)


def test_rate_of_change_is_also_per_unit_time(readings):
    wide = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value")
    rated = apply("rate_of_change", wide, column="temp", group_by=["unit"],
                  order_by="ts", per="hour")
    rates = [r["temp_per_hour"] for r in rated.to_rows() if r["temp_per_hour"] is not None]
    assert rates and all(r == pytest.approx(2.0, abs=1e-6) for r in rates)


def test_lag_looks_back_inside_the_group_only(readings):
    wide = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value")
    lagged = apply("lag_column", wide, column="temp", periods=1,
                   group_by=["unit"], order_by="ts")
    rows = sorted(
        (r for r in lagged.to_rows() if r["unit"] == "slow"), key=lambda r: r["ts"]
    )
    assert rows[0]["temp_lag1"] is None
    assert rows[1]["temp_lag1"] == rows[0]["temp"]


# --------------------------------------------------------------------------
# aggregating
# --------------------------------------------------------------------------
def test_linear_trend_reports_slope_r_squared_and_change(readings):
    wide = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value")
    trend = apply("linear_trend", wide, column="temp", group_by=["unit"],
                  order_by="ts", per="hour")
    rows = {r["unit"]: r for r in trend.to_rows()}
    assert rows["fast"]["temp_slope_per_hour"] == pytest.approx(2.0, abs=1e-4)
    assert rows["fast"]["temp_r_squared"] == pytest.approx(1.0, abs=1e-6)
    assert rows["fast"]["temp_direction"] == "increasing"
    #  The intercept is the fitted value where the window starts, not the
    #  fitted value at the epoch.
    assert rows["fast"]["temp_intercept"] == pytest.approx(60.0, abs=1e-4)


def test_a_flat_series_does_not_claim_a_perfect_fit():
    """A stuck sensor must not out-rank a real trend."""
    table = Table.from_rows([{"ts": at(h), "v": 5.0} for h in range(8)])
    trend = apply("linear_trend", table, column="v", order_by="ts", per="hour")
    row = trend.to_rows()[0]
    assert row["v_r_squared"] is None
    assert row["v_direction"] == "stable"


def test_a_noisy_series_is_reported_as_unstable():
    values = [10.0, 2.0, 14.0, 1.0, 13.0, 3.0, 11.0, 2.0]
    table = Table.from_rows(
        [{"ts": at(h), "v": v} for h, v in enumerate(values)]
    )
    trend = apply("linear_trend", table, column="v", order_by="ts", per="hour")
    assert trend.to_rows()[0]["v_direction"] == "unstable"


def test_correlation_reports_its_sample_size_and_refuses_a_flat_pair(readings):
    wide = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value")
    pairs = apply("correlation", wide, columns=["temp", "vib"],
                  group_by=["unit"], min_periods=3)
    rows = {r["unit"]: r for r in pairs.to_rows()}
    assert rows["fast"]["correlation"] == pytest.approx(1.0, abs=1e-6)
    #  `slow`'s vibration never moves, so there is no relationship to report -
    #  and zero would read as "measured, and unrelated".
    assert rows["slow"]["correlation"] is None
    assert rows["fast"]["points"] >= 3


def test_too_few_points_leaves_the_coefficient_null(readings):
    wide = apply("pivot_wider", readings, keys=["unit", "ts"],
                 name_from="measure", value_from="value")
    pairs = apply("correlation", wide, columns=["temp", "vib"],
                  group_by=["unit", "ts"], min_periods=5)
    assert all(r["correlation"] is None for r in pairs.to_rows())


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("key", "options"),
    [
        ("pivot_wider", {"keys": [], "name_from": "measure", "value_from": "value"}),
        ("pivot_wider", {"keys": ["unit"], "name_from": "measure",
                         "value_from": "value", "aggregation": "geometric"}),
        ("pivot_wider", {"keys": ["unit"], "name_from": "nope",
                         "value_from": "value"}),
        ("unpivot_longer", {"keys": ["unit", "ts", "measure", "value"]}),
        ("resample_time", {"timestamp": "ts", "period": "fortnight",
                           "measures": {"value": "mean"}}),
        ("resample_time", {"timestamp": "ts", "measures": {"value": "geomean"}}),
        ("resample_time", {"timestamp": "ts", "measures": {}}),
        ("rolling_stats", {"column": "value", "window": 1}),
        ("rolling_stats", {"column": "value", "statistics": ["kurtosis"]}),
        ("rate_of_change", {"column": "value", "per": "fortnight"}),
        ("rate_of_change", {"column": "value", "periods": 0}),
        ("lag_column", {"column": "value", "periods": 0}),
        ("linear_trend", {"column": "value", "per": "fortnight"}),
        ("correlation", {"columns": ["value"]}),
        ("correlation", {"columns": ["value", "nope"]}),
    ],
)
def test_bad_options_are_refused_with_a_reason(readings, key, options):
    with pytest.raises(ValidationError):
        apply(key, readings, **options)
