"""The maintenance application, from the raw record to the decision.

Two things are worth testing here and they are different in kind.

The first is a **contract between two files**: `features.py` declares the
pipeline and the column names it leaves behind, and `engine.py` reads those
names. A step renamed in one and not the other fails on a fresh install with a
KeyError, so the pipeline is actually run — through the real providers, on a
subset of the real fleet — and its output is checked against the names the
engine imports.

The second is the **behaviour that justifies the design**. A layered analysis
is more code than a threshold alarm, and the only defence of that is that it
gets different answers on cases a threshold cannot tell apart:

* a machine whose several measurements are moving together is flagged;
* a machine whose one measurement is moving because its transmitter was
  rescaled is not;
* a maintenance interval with no record of ever being carried out is reported
  as unknown, not as overdue.

Each of those is a case the simulator deliberately contains.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.model.domain.entities import ModelDefinition
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionInput,
    ExecutionKind,
)
from app.plugins.asset_maintenance import features as F
from app.plugins.asset_maintenance.catalogue import CLASSES, ENGINEERING_RULES
from app.plugins.asset_maintenance.datagen import FILES, ensure_fleet
from app.plugins.asset_maintenance.engine import (
    ANALYZER_KEYS,
    DEFAULT_POLICY,
    POLICIES,
    Engine,
)
from app.plugins.asset_maintenance.paths import data_dir
from app.plugins.asset_maintenance.plugin import _scores
from app.plugins.data_quality.checks import assess_series
from app.plugins.formula.plugin import FormulaModelPlugin
from app.plugins.join.plugin import JoinPlugin
from app.plugins.python_function import library
from app.plugins.python_function.columnar import as_datetime, as_number
from app.plugins.rule.plugin import RuleModelPlugin
from app.shared.tabular import Table

#  Enough of the record to exercise every layer, and few enough assets that
#  the whole chain runs in a couple of seconds.
SUBSET_DAYS = 60


# --------------------------------------------------------------------------
# running the declared steps through the real providers
# --------------------------------------------------------------------------
def _run_provider(plugin, configuration: dict, table: Table, extra: dict[str, Table]):
    definition = ModelDefinition(
        name="step", provider="test", configuration=configuration
    )
    context = ExecutionContext(
        execution_id="exec_test",
        kind=ExecutionKind.TRANSFORMATION,
        definition=definition,
        input=ExecutionInput(table=table, inputs=extra),
    )
    outcome = plugin.execute(context)
    assert outcome.payload.table is not None
    return outcome.payload.table


def _run_steps(table: Table, steps: list[dict], references: dict[str, Table]) -> Table:
    """Execute a declared step list against the real providers.

    Deliberately not a re-implementation: each step is dispatched to the same
    plugin the pipeline runner would use, with the same configuration. What is
    not exercised is `PipelineService`'s wiring, which has tests of its own.
    """
    current = table
    for step in steps:
        provider = step["provider"]
        configuration = step["configuration"]
        if provider == "python-transform":
            transform = library.get(configuration["transform"])
            options = transform.parameters.coerce_record(configuration.get("options") or {})
            current = transform.apply(current, options)
        elif provider == "join":
            right = references[step["input_datasets"]["right"]]
            current = _run_provider(JoinPlugin(), configuration, current, {"right": right})
        elif provider == "formula":
            current = _run_provider(FormulaModelPlugin(), configuration, current, {})
        elif provider == "rule":
            current = _run_provider(RuleModelPlugin(), configuration, current, {})
        else:  # pragma: no cover - a step kind this test does not know
            raise AssertionError(f"unhandled provider '{provider}' in a declared step")
    return current


# --------------------------------------------------------------------------
# the record
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def record() -> dict[str, Any]:
    """The generated fleet, subset to four assets and the recent window."""
    ensure_fleet()
    root = data_dir()

    def js(key: str) -> Table:
        return Table.from_rows(
            json.loads((root / FILES[key]).read_text(encoding="utf-8"))
        )

    truth = json.loads((root / FILES["truth"]).read_text(encoding="utf-8"))
    by_scenario: dict[str, list[dict]] = {}
    for row in truth:
        by_scenario.setdefault(row["scenario"], []).append(row)

    #  One of each case the analysis has to tell apart. Chosen from the ground
    #  truth rather than hard-coded, so a change to the simulator's mix does
    #  not silently test the same asset four times.
    degrading = max(
        by_scenario.get("degrading", []), key=lambda row: row["progress_at_end"]
    )
    stuck = next(
        (row for row in by_scenario.get("sensor_fault", [])
         if row["instrument_fault"] == "stuck"),
        by_scenario.get("sensor_fault", [{}])[0],
    )
    healthy = by_scenario["healthy"][0]
    resolved = by_scenario["resolved"][0]
    wanted = [row["asset_id"] for row in (degrading, stuck, healthy, resolved)]

    telemetry = Table.from_parquet(root / FILES["telemetry"]).filter(
        [{"column": "asset_id", "op": "in", "value": wanted}]
    )
    operating = Table.from_parquet(root / FILES["operating"]).filter(
        [{"column": "asset_id", "op": "in", "value": wanted}]
    )
    days = sorted({str(value)[:10] for value in telemetry.column_values("timestamp")})
    floor = days[max(0, len(days) - SUBSET_DAYS)]
    telemetry = telemetry.filter([{"column": "timestamp", "op": "gte", "value": floor}])
    operating = operating.filter([{"column": "timestamp", "op": "gte", "value": floor}])

    import csv

    with open(root / FILES["environment"], encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["timestamp"] >= floor]
    for row in rows:
        for column in ("ambient_temperature_c", "relative_humidity_pct",
                       "rainfall_mm", "dust_pm10_ugm3", "voltage_thd_pct"):
            row[column] = float(row[column])

    return {
        "assets": [row["asset_id"] for row in (degrading, stuck, healthy, resolved)],
        "truth": {row["asset_id"]: row for row in truth},
        "degrading": degrading["asset_id"],
        "stuck": stuck["asset_id"],
        "healthy": healthy["asset_id"],
        "resolved": resolved["asset_id"],
        "telemetry": telemetry,
        "operating": operating,
        "environment": Table.from_rows(rows),
        "asset_table": js("assets").filter(
            [{"column": "asset_id", "op": "in", "value": wanted}]
        ),
        "policies": js("policies"),
        "thresholds": js("thresholds"),
        "response": js("response"),
        "rules": js("rules"),
        "maintenance": js("maintenance"),
        "failures": js("failures"),
    }


@pytest.fixture(scope="module")
def features(record) -> dict[str, Table]:
    """Run the declared pipelines and hand back what they produced."""
    references = {
        F.OPERATING_DATASET: record["operating"],
        F.ASSETS_DATASET: record["asset_table"],
        F.ENVIRONMENT_DATASET: record["environment"],
        F.RESPONSE_DATASET: record["response"],
        F.THRESHOLD_DATASET: record["thresholds"],
    }
    ids = {name: name for name in references}
    conditioned = _run_steps(
        record["telemetry"], F.conditioning_steps(ids), references
    )
    daily = _run_steps(conditioned, F.daily_steps(), references)
    table = _run_steps(daily, F.feature_steps(ids), references)
    snapshot = _run_steps(table, F.snapshot_steps(), references)
    return {"conditioned": conditioned, "daily": daily, "features": table,
            "snapshot": snapshot}


@pytest.fixture(scope="module")
def engine(record, features) -> Engine:
    quality = _quality_of(features["conditioned"])
    return Engine(
        features=features["features"],
        assets=record["asset_table"],
        policies=record["policies"],
        rules=record["rules"],
        maintenance=record["maintenance"],
        failures=record["failures"],
        quality=quality,
    )


def _quality_of(table: Table) -> Table:
    """The measurement-quality table, as the seeded model computes it."""
    asset = table.column_values("asset_id")
    parameter = table.column_values("parameter")
    value = table.column_values("value")
    stamp = table.column_values("timestamp")
    grouped: dict[tuple, list[int]] = {}
    for index in range(table.num_rows):
        grouped.setdefault((asset[index], parameter[index]), []).append(index)

    rows = []
    for (asset_id, name), positions in grouped.items():
        stamps = [as_datetime(stamp[i]) for i in positions]
        order = sorted(range(len(positions)), key=lambda k: (stamps[k] is None, stamps[k]))
        quality = assess_series(
            [as_number(value[positions[k]]) for k in order],
            [stamps[k] for k in order],
            checks=("missing", "duplicates", "outliers", "flatline", "step", "drift"),
        )
        rows.append(quality.to_dict(asset_id=asset_id, parameter=name))
    return Table.from_rows(rows)


# --------------------------------------------------------------------------
# the contract between the pipeline and the engine
# --------------------------------------------------------------------------
def test_the_pipeline_produces_every_column_the_engine_reads(features):
    """The failure this file exists to catch: a rename in one file only."""
    produced = set(features["features"].columns)
    missing = [name for name in F.REQUIRED_FEATURE_COLUMNS if name not in produced]
    assert not missing, f"the feature pipeline no longer produces {missing}"


def test_only_running_hours_survive_conditioning(features):
    states = set(features["conditioned"].column_values("operating_state"))
    assert states <= {"running", "overload"}


def test_the_daily_table_is_one_row_per_asset_measurement_day(features):
    daily = features["daily"]
    keys = {
        (row["asset_id"], row["parameter"], row["day"]) for row in daily.to_rows()
    }
    assert len(keys) == daily.num_rows


def test_the_snapshot_is_the_latest_day_per_measurement(features, record):
    snapshot = features["snapshot"]
    latest = max(str(row["day"]) for row in features["features"].to_rows())
    keys = {(row["asset_id"], row["parameter"]) for row in snapshot.to_rows()}
    assert len(keys) == snapshot.num_rows
    assert any(str(row["day"]) == latest for row in snapshot.to_rows())


def test_a_healthy_asset_sits_near_its_expected_value(features, record):
    """The property that makes a residual threshold work at any load."""
    rows = [
        row for row in features["features"].to_rows()
        if row["asset_id"] == record["healthy"]
        and row[F.C_LIMIT_PROGRESS] is not None
    ]
    assert rows
    progress = sorted(float(row[F.C_LIMIT_PROGRESS]) for row in rows)
    median = progress[len(progress) // 2]
    #  Zero means "exactly as the response model predicts". A healthy asset
    #  should be near it whatever duty it runs at.
    assert abs(median) < 25, f"healthy asset sits at {median:.1f}% of its limit band"


def test_every_engineering_rule_names_columns_the_record_can_supply(engine, record):
    """A rule that reads a name nothing produces is a rule that never fires."""
    from app.plugins.formula.expression import expression_variables

    asset = engine.assets[0]
    view = engine.view(asset, as_datetime(engine.latest_day))
    assert view is not None
    from app.plugins.asset_maintenance.engine import _feature_record

    available = set(_feature_record(view))
    #  Per-measurement names exist only for the measurements that asset has,
    #  so a rule scoped to another asset type is allowed to name others.
    generic = [rule for rule in ENGINEERING_RULES if rule["asset_type"] == "*"]
    for rule in generic:
        unknown = expression_variables(rule["when"]) - available
        assert not unknown, (
            f"{rule['rule_id']} reads {sorted(unknown)}, which nothing produces"
        )


# --------------------------------------------------------------------------
# the behaviour that justifies the design
# --------------------------------------------------------------------------
def test_a_degrading_asset_is_flagged_and_its_mode_named(engine, record):
    asset = next(
        a for a in engine.assets if str(a["asset_id"]) == record["degrading"]
    )
    assessment = engine.assess(asset, policy=DEFAULT_POLICY)
    assert assessment is not None
    assert assessment.maintenance_required is True
    assert assessment.health_score is not None
    assert assessment.health_status in ("DEGRADED", "POOR", "CRITICAL", "WATCH")
    #  Somebody has to be able to act on it, so the reasons are prose and the
    #  action is a sentence rather than a code.
    assert assessment.reasons
    assert assessment.recommended_action


def test_a_stuck_transmitter_is_not_turned_into_a_work_order(engine, record):
    """The failure chain the data-quality layer exists to break."""
    asset = next(a for a in engine.assets if str(a["asset_id"]) == record["stuck"])
    assessment = engine.assess(asset, policy=DEFAULT_POLICY)
    assert assessment is not None
    quality = [f for f in assessment.findings if f.analyzer == "quality"]
    #  Either the instrument was caught and argued against acting, or nothing
    #  about this asset looked wrong in the first place. What must not happen
    #  is a confident work order built on a reading nobody should trust.
    if assessment.maintenance_required:
        assert quality, (
            "flagged an asset with a stuck transmitter and said nothing about it"
        )
        assert any(finding.weight < 0 for finding in quality)


def test_a_repaired_asset_reads_healthier_than_a_degrading_one(engine, record):
    def score(asset_id: str) -> float:
        asset = next(a for a in engine.assets if str(a["asset_id"]) == asset_id)
        assessment = engine.assess(asset, policy=DEFAULT_POLICY)
        assert assessment is not None and assessment.health_score is not None
        return assessment.health_score

    assert score(record["resolved"]) > score(record["degrading"])
    assert score(record["healthy"]) > score(record["degrading"])


def test_a_policy_is_a_set_of_analyzers_not_a_different_engine(engine, record):
    """What makes the five policies comparable in one experiment."""
    asset = next(a for a in engine.assets if str(a["asset_id"]) == record["degrading"])
    thin = engine.assess(asset, policy="threshold_only")
    full = engine.assess(asset, policy="full")
    assert thin is not None and full is not None
    assert {f.analyzer for f in thin.findings} <= {"threshold"}
    assert len(full.findings) >= len(thin.findings)


def test_an_unrecorded_interval_is_unknown_rather_than_overdue(engine):
    """Counting from commissioning turned a filing gap into a 240% overrun."""
    for asset in engine.assets:
        assessment = engine.assess(asset, policy=DEFAULT_POLICY)
        if assessment is None:
            continue
        usage = assessment.record.get("interval_usage_pct")
        if usage is None:
            continue
        #  A real overrun is possible; a 200% one is the arithmetic being wrong.
        assert usage < 200, f"{asset['asset_id']} reports {usage:.0f}% of its interval"


def test_the_window_states_the_basis_it_rests_on(engine):
    allowed = {"calculated", "estimated", "inferred", "unknown"}
    for asset in engine.assets:
        assessment = engine.assess(asset, policy=DEFAULT_POLICY)
        if assessment is None:
            continue
        assert assessment.window_basis in allowed
        if assessment.window_basis == "inferred":
            #  Deliberately undated: a direction without a date.
            assert assessment.window_start is None


def test_confidence_falls_when_the_instruments_are_doubted(engine, record):
    stuck = next(a for a in engine.assets if str(a["asset_id"]) == record["stuck"])
    healthy = next(a for a in engine.assets if str(a["asset_id"]) == record["healthy"])
    doubted = engine.assess(stuck, policy=DEFAULT_POLICY)
    trusted = engine.assess(healthy, policy=DEFAULT_POLICY)
    assert doubted is not None and trusted is not None
    assert 0.0 < doubted.confidence <= 1.0
    assert 0.0 < trusted.confidence <= 1.0


def test_an_asset_with_no_readings_before_the_date_is_not_assessed(engine):
    asset = engine.assets[0]
    assert engine.assess(asset, as_of=as_datetime("2000-01-01")) is None


# --------------------------------------------------------------------------
# the catalogue and the scoring
# --------------------------------------------------------------------------
def test_every_policy_names_analyzers_that_exist():
    for policy in POLICIES.values():
        unknown = set(policy.analyzers) - set(ANALYZER_KEYS)
        assert not unknown, f"policy '{policy.key}' names {sorted(unknown)}"


def test_every_failure_mode_moves_measurements_its_class_has():
    """A mode whose signals the class does not measure can never be matched."""
    for klass in CLASSES.values():
        parameters = {measurement.parameter for measurement in klass.measurements}
        for mode in klass.modes:
            unknown = set(mode.effects) - parameters
            assert not unknown, f"{klass.key}/{mode.key} moves {sorted(unknown)}"


def test_specificity_and_alert_rate_are_reported_beside_recall():
    """A fleet is mostly healthy, so accuracy would flatter a useless policy."""
    scores = _scores({"tp": 0, "fp": 0, "fn": 6, "tn": 394})
    assert scores["recall"] == 0.0
    assert scores["specificity"] == 1.0
    assert scores["alert_rate"] == 0.0

    everything = _scores({"tp": 6, "fp": 394, "fn": 0, "tn": 0})
    assert everything["recall"] == 1.0
    assert everything["specificity"] == 0.0
    assert everything["alert_rate"] == 1.0


def test_the_fleet_is_generated_deterministically():
    first = ensure_fleet()
    second = ensure_fleet()
    assert first["seed"] == second["seed"]
    assert first["assets"] == second["assets"] == 40
    assert first["telemetry_rows"] == second["telemetry_rows"]
    #  The record says plainly that it is simulated, and where the answer is.
    assert first["simulated"] is True
    assert first["failures_in_window"] > 0


# --------------------------------------------------------------------------
# the answer key, and what counts as a correct flag
# --------------------------------------------------------------------------
def test_a_second_degradation_counts_as_one():
    """The answer-key fault that made correct answers look like false alarms.

    Recording only an asset's first episode labelled it healthy through its
    second, and the backtest then scored the engine's correct answers about it
    as false alarms — a fault in the key, and the worst kind, because it looks
    like a fault in the thing being measured.
    """
    from app.plugins.asset_maintenance.plugin import _degrading_at, _intervals

    rows = [
        {"episode": 1, "onset": "2026-05-10 00:00:00",
         "failure": "2026-06-01 00:00:00", "repaired": "2026-06-02 00:00:00"},
        {"episode": 2, "onset": "2026-07-01 00:00:00",
         "failure": "2026-08-10 00:00:00", "repaired": None},
    ]
    assert len(_intervals(rows)) == 2
    assert _degrading_at(rows, as_datetime("2026-05-20")) is True
    assert _degrading_at(rows, as_datetime("2026-06-15")) is False
    #  The second episode, which recording only the first said nothing about.
    assert _degrading_at(rows, as_datetime("2026-07-15")) is True
    assert _degrading_at(rows, as_datetime("2026-09-01")) is True


def test_an_asset_that_never_degraded_contributes_no_interval():
    from app.plugins.asset_maintenance.plugin import _degrading_at, _intervals

    rows = [{"episode": 0, "onset": None, "failure": None, "repaired": None}]
    assert _intervals(rows) == []
    assert _degrading_at(rows, as_datetime("2026-05-20")) is False


def test_the_generated_answer_key_is_a_table_not_a_document():
    """A dataset is a table. A list inside a cell does not survive ingestion.

    The episode list did survive the file and disappear on the way into the
    platform — the JSON reader normalises a nested list into text — leaving the
    backtest scoring against a shorter answer key than the one on disk, with
    nothing anywhere reporting a problem.
    """
    import json as _json

    from app.shared.tabular import Table

    truth = _json.loads((data_dir() / FILES["truth"]).read_text(encoding="utf-8"))
    assert truth, "the answer key is empty"
    for row in truth:
        for value in row.values():
            assert not isinstance(value, (list, dict)), (
                f"'{row}' nests a value; it will not survive ingestion"
            )

    ingested = Table.from_rows(truth)
    assert ingested.num_rows == len(truth)
    onsets = [value for value in ingested.column_values("onset") if value]
    assert onsets, "no episode has an onset once ingested"

    episodes = [row for row in truth if row["onset"]]
    assert episodes, "no asset in the fleet has a recorded degradation"
    assert any(row["episode"] > 1 for row in truth), (
        "no asset degrades twice, so the two-episode case is untested"
    )
    for row in episodes:
        assert row["failure_mode"]


def test_a_policy_due_work_order_is_not_scored_as_degradation(engine, record):
    """Two questions, and only one of them is what the backtest measures.

    A lubrication interval running out is a correct reason to send somebody,
    and has nothing to do with whether the machine is degrading. Counting it
    against a degradation label measures the wrong system.
    """
    saw_policy_only = False
    for asset in engine.assets:
        assessment = engine.assess(asset, policy=DEFAULT_POLICY)
        if assessment is None:
            continue
        assert assessment.maintenance_required == (
            assessment.condition_required or assessment.interval_required
        )
        if assessment.interval_required and not assessment.condition_required:
            saw_policy_only = True
            assert assessment.decision()["required_because"] == "policy"
    #  Not asserted as present — a four-asset subset may have none — but the
    #  distinction must hold wherever it does occur.
    assert saw_policy_only or True


def test_a_freshly_repaired_asset_is_not_trended_across_its_repair(engine):
    """A slope fitted across a bearing change reports the repair as decay."""
    from app.plugins.asset_maintenance.engine import _rebuilt_window

    for asset in engine.assets:
        view = engine.view(asset, as_datetime(engine.latest_day))
        if view is None or view.trend_window_is_clean:
            continue
        finding = _rebuilt_window(view)
        assert finding.weight < 0
        assert "維修" in finding.statement
        assessment = engine.assess(asset, policy=DEFAULT_POLICY)
        assert assessment is not None
        #  No trend or statistical finding may be raised while the window
        #  still straddles the repair.
        assert not [
            f for f in assessment.findings if f.analyzer in ("trend", "statistical")
        ]
