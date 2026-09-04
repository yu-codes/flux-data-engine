"""A simulated plant, generated once so the application has a record to read.

Condition monitoring cannot be demonstrated on invented numbers, because the
whole subject is about relationships *between* numbers: a bearing that runs
hot because the plant room is hot, a current that is high because the load is
high, a vibration that rises for six weeks and then stops rising because
somebody changed the bearing. A file of random walks has none of those, and an
analysis built against one is an analysis that has never been tested.

So this generates a fleet with a history that hangs together:

* every reading is produced from load, ambient conditions and the equipment's
  own response coefficients — so operating context genuinely explains most of
  the variation, and an analysis that ignores it genuinely gets the wrong
  answer;
* degradation follows a **failure mode**, which moves several signals in a
  pattern rather than moving one past a line;
* a failure inside the window is followed by a corrective maintenance event,
  after which the signals return to baseline — so "before and after" is real
  and a baseline computed across a repair is visibly wrong;
* three assets have instrument faults that look exactly like degradation, and
  are not. They are in the record on purpose: a system that flags them is a
  system that will cry wolf in service.

It is a simulation and the code says so. `meta.json` records the seed, the
window and the ground truth, and the validation report states plainly that the
scores are measured against a known answer — which is the one thing a real
fleet cannot give you, and the reason a simulated one is worth having.

Generation is deterministic: the same seed produces the same fleet, so a
result computed today can be reproduced tomorrow.
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.shared.tabular import Table

from .catalogue import (
    CLASSES,
    ENGINEERING_RULES,
    AssetClass,
    Measurement,
    response_row,
    threshold_row,
)

logger = logging.getLogger(__name__)

SEED = 20260903
WINDOW_DAYS = 120
INTERVAL_MINUTES = 60
HOURS = WINDOW_DAYS * 24
STAMP = "%Y-%m-%d %H:%M:%S"

FILES = {
    "assets": "assets.json",
    "specifications": "asset_specifications.json",
    "sensors": "asset_sensors.json",
    "telemetry": "telemetry.parquet",
    "operating": "operating_context.parquet",
    "environment": "environment.csv",
    "maintenance": "maintenance_events.json",
    "failures": "failure_events.json",
    "policies": "maintenance_policies.json",
    "thresholds": "condition_thresholds.json",
    "response": "response_model.json",
    "rules": "engineering_rules.json",
    "truth": "ground_truth.json",
    "meta": "meta.json",
}

SITES = [
    {
        "site_id": "SITE-TY", "site_name": "桃園一廠", "region": "north",
        "base_temperature": 23.0, "seasonal_amplitude": 6.5, "base_humidity": 77.0,
        "base_dust": 36.0, "rain_probability": 0.14,
    },
    {
        "site_id": "SITE-TC", "site_name": "台中二廠", "region": "central",
        "base_temperature": 25.0, "seasonal_amplitude": 7.0, "base_humidity": 70.0,
        "base_dust": 48.0, "rain_probability": 0.10,
    },
    {
        "site_id": "SITE-KH", "site_name": "高雄三廠", "region": "south",
        "base_temperature": 27.0, "seasonal_amplitude": 5.5, "base_humidity": 73.0,
        "base_dust": 58.0, "rain_probability": 0.08,
    },
]

CRITICALITIES = ("critical", "high", "medium", "low")

#  Annual operating hours implied by each duty pattern. Used for the runtime
#  an asset already had before the window opens, which is what makes a
#  usage-based policy mean anything on day one.
ANNUAL_HOURS = {
    "continuous": 8400,
    "two_shift": 4100,
    "intermittent": 2400,
    "standby": 260,
}

MANUFACTURERS = {
    "centrifugal_pump": ["Grundfos", "KSB", "Ebara", "大井"],
    "electric_motor": ["ABB", "Siemens", "TECO 東元", "WEG"],
    "air_compressor": ["Atlas Copco", "Ingersoll Rand", "Kobelco"],
    "control_valve": ["Fisher", "Samson", "Masoneilan"],
    "diesel_generator": ["Cummins", "Caterpillar", "Mitsubishi"],
    "power_transformer": ["士林電機", "Hitachi", "華城電機"],
    "water_chiller": ["Trane", "York", "Daikin"],
    "centrifugal_fan": ["Ebara", "Nicotra", "Fantech"],
}

TECHNICIANS = [
    "陳建志", "林雅婷", "黃國豪", "張慧敏",
    "吳承翰", "李孟樺", "外包 · 泰昌機電",
]

#  What each scenario is for. The counts below add up to the fleet.
SCENARIOS = {
    "healthy": 12,      # nothing wrong, and the system must say so
    "degrading": 10,    # degrading now, failure projected beyond the window
    "resolved": 11,     # failed inside the window and was repaired
    "sensor_fault": 4,  # the instrument is wrong, not the machine
    "new_asset": 3,     # commissioned recently: no history to learn from
}

FLEET_MIX = [
    ("centrifugal_pump", 7, "PUMP"),
    ("electric_motor", 7, "MOT"),
    ("air_compressor", 5, "COMP"),
    ("control_valve", 5, "VLV"),
    ("diesel_generator", 3, "GEN"),
    ("power_transformer", 4, "TRF"),
    ("water_chiller", 4, "CHL"),
    ("centrifugal_fan", 5, "FAN"),
]

QUALITY_ISSUES = ("stuck", "unit_error", "drift", "range_error")


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------
@dataclass
class AssetPlan:
    """One asset, and everything that is going to happen to it."""

    asset_id: str
    name: str
    klass: AssetClass
    site: dict
    criticality: str
    scenario: str
    duty: str
    base_load: float
    commissioned: datetime
    manufacturer: str
    model_number: str
    location: str
    runtime_at_start: float
    starts_at_start: int
    #  Degradation, when there is any.
    mode_key: str | None = None
    onset_hour: float | None = None
    failure_hour: float | None = None
    repair_hour: float | None = None
    repair_end_hour: float | None = None
    #  A second episode, for assets that failed early in the window.
    second: dict[str, Any] | None = None
    #  Instrument faults.
    quality_issue: str | None = None
    quality_parameter: str | None = None
    quality_from: int = 0
    quality_to: int = 0
    #  Hours with no telemetry at all, for the one asset whose logger dropped.
    outage: tuple[int, int] | None = None
    first_hour: int = 0
    events: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    def degradation(self, hour: float) -> float:
        """How far this asset's current episode has progressed, 0 to 1."""
        progress = self._progress(
            self.onset_hour, self.failure_hour, self.repair_hour, hour
        )
        if self.second:
            progress = max(
                progress,
                self._progress(
                    self.second["onset_hour"],
                    self.second["failure_hour"],
                    self.second.get("repair_hour"),
                    hour,
                ),
            )
        return progress

    def mode_at(self, hour: float) -> str | None:
        if self.second and hour >= self.second["onset_hour"]:
            second = self._progress(
                self.second["onset_hour"], self.second["failure_hour"],
                self.second.get("repair_hour"), hour,
            )
            if second > 0:
                return self.second["mode_key"]
        first = self._progress(self.onset_hour, self.failure_hour, self.repair_hour, hour)
        return self.mode_key if first > 0 else None

    @staticmethod
    def _progress(
        onset: float | None, failure: float | None, repair: float | None, hour: float
    ) -> float:
        if onset is None or failure is None or hour < onset:
            return 0.0
        if repair is not None and hour >= repair:
            #  Repaired. The signals return to baseline, which is what makes a
            #  baseline computed across the repair visibly wrong.
            return 0.0
        span = max(1.0, failure - onset)
        return max(0.0, min(1.0, (hour - onset) / span))

    def maintenance_hours(self) -> list[tuple[float, float]]:
        """Windows during which the asset is down for work."""
        windows = []
        for event in self.events:
            start = event.get("_start_hour")
            if start is None:
                continue
            windows.append((start, start + max(1.0, event["downtime_hours"])))
        return windows


def _plan_fleet(rng: random.Random, now: datetime) -> list[AssetPlan]:
    """Lay out the whole fleet before a single reading is produced."""
    scenarios: list[str] = []
    for name, count in SCENARIOS.items():
        scenarios.extend([name] * count)
    total = sum(count for _, count, _ in FLEET_MIX)
    if len(scenarios) != total:
        raise RuntimeError(
            f"the scenario plan covers {len(scenarios)} assets but the fleet has {total}"
        )
    #  Shuffled with the shared seed so scenarios are spread across classes and
    #  sites rather than lining up with them - a fleet where every pump is
    #  broken and every motor is fine would let an analysis cheat.
    rng.shuffle(scenarios)

    window_start = now - timedelta(hours=HOURS - 1)
    plans: list[AssetPlan] = []
    index = 0
    quality_assigned = 0
    for class_key, count, prefix in FLEET_MIX:
        klass = CLASSES[class_key]
        for serial in range(1, count + 1):
            scenario = scenarios[index]
            site = SITES[index % len(SITES)]
            criticality = _criticality(rng, klass, index)
            asset_id = f"{prefix}-{serial:03d}"
            duty = klass.duty
            age_years = (
                round(rng.uniform(0.15, 0.55), 2)
                if scenario == "new_asset"
                else round(rng.uniform(2.0, 16.0), 2)
            )
            commissioned = now - timedelta(days=age_years * 365.25)
            annual = ANNUAL_HOURS[duty]
            plan = AssetPlan(
                asset_id=asset_id,
                name=f"{site['site_name']} {klass.label} {serial:02d}",
                klass=klass,
                site=site,
                criticality=criticality,
                scenario=scenario,
                duty=duty,
                base_load=round(rng.uniform(0.45, 0.92), 3),
                commissioned=commissioned,
                manufacturer=rng.choice(MANUFACTURERS[class_key]),
                model_number=(
                    f"{prefix}-{rng.choice(['A', 'B', 'C', 'X'])}"
                    f"{rng.randrange(100, 999)}"
                ),
                location=(
                    f"{site['site_name']} {rng.choice('ABCD')} 區 "
                    f"{rng.randrange(1, 9)} 號機房"
                ),
                runtime_at_start=round(age_years * annual * rng.uniform(0.88, 1.06), 1),
                starts_at_start=int(
                    age_years * _starts_per_year(duty) * rng.uniform(0.8, 1.2)
                ),
            )
            if scenario == "new_asset":
                #  Commissioned inside the window: the telemetry starts when the
                #  asset does, which is the cold-start case the engine has to
                #  handle without a baseline of its own.
                started = max(0, HOURS - int(rng.uniform(18, 34) * 24))
                plan.first_hour = started
                plan.runtime_at_start = 0.0
                plan.starts_at_start = 0
                plan.commissioned = window_start + timedelta(hours=started)
            elif scenario == "degrading":
                mode = rng.choice(klass.modes)
                development = rng.uniform(*mode.development_days) * 24
                #  Still developing at the end of the window, at a progress the
                #  scenario spreads across the fleet: some barely started, some
                #  nearly there.
                progress = rng.uniform(0.30, 0.94)
                plan.mode_key = mode.key
                plan.failure_hour = HOURS - 1 + development * (1 - progress)
                plan.onset_hour = plan.failure_hour - development
            elif scenario == "resolved":
                mode = rng.choice(klass.modes)
                development = rng.uniform(*mode.development_days) * 24
                failure_hour = rng.uniform(0.30 * HOURS, 0.82 * HOURS)
                plan.mode_key = mode.key
                plan.failure_hour = failure_hour
                plan.onset_hour = failure_hour - development
                plan.repair_hour = failure_hour + rng.uniform(2, 20)
                if rng.random() < 0.45 and failure_hour < 0.55 * HOURS:
                    #  A second episode, so the record contains assets that
                    #  failed twice - which is what makes "has this happened
                    #  before" a question worth asking of the history.
                    second_mode = rng.choice(klass.modes)
                    second_development = rng.uniform(*second_mode.development_days) * 24
                    second_failure = min(
                        HOURS - 24.0, failure_hour + rng.uniform(0.30, 0.55) * HOURS
                    )
                    plan.second = {
                        "mode_key": second_mode.key,
                        "failure_hour": second_failure,
                        "onset_hour": second_failure - second_development,
                        "repair_hour": second_failure + rng.uniform(3, 22),
                    }
            elif scenario == "sensor_fault":
                issue = QUALITY_ISSUES[quality_assigned % len(QUALITY_ISSUES)]
                quality_assigned += 1
                plan.quality_issue = issue
                plan.quality_parameter = _fault_parameter(rng, klass, issue)
                length = int(rng.uniform(4, 11) * 24)
                plan.quality_to = HOURS - int(rng.uniform(0, 6) * 24)
                plan.quality_from = max(0, plan.quality_to - length)
                if issue == "drift":
                    #  Drift is slow by definition; it needs most of the window.
                    plan.quality_from = int(HOURS * 0.45)
                    plan.quality_to = HOURS
            plans.append(plan)
            index += 1

    #  Exactly one logger outage in the fleet, on an asset that is otherwise
    #  healthy, so a sampling gap can be seen without a degradation beside it.
    for plan in plans:
        if plan.scenario == "healthy":
            start = int(HOURS * 0.6)
            plan.outage = (start, start + 17)
            break
    return plans


def _criticality(rng: random.Random, klass: AssetClass, index: int) -> str:
    """Criticality follows the equipment, with variation within a class.

    A transformer is critical because the site stops without it; a fan usually
    is not. Assigning it at random would make criticality a coin flip that the
    risk matrix then takes seriously.
    """
    leaning = {
        "power_transformer": ("critical", "critical", "high"),
        "diesel_generator": ("critical", "high", "high"),
        "water_chiller": ("high", "high", "medium"),
        "air_compressor": ("high", "high", "medium"),
        "centrifugal_pump": ("high", "medium", "medium", "low"),
        "electric_motor": ("high", "medium", "medium", "low"),
        "control_valve": ("medium", "medium", "low"),
        "centrifugal_fan": ("medium", "low", "low"),
    }[klass.key]
    return leaning[(index + rng.randrange(0, len(leaning))) % len(leaning)]


def _starts_per_year(duty: str) -> int:
    return {"continuous": 24, "two_shift": 500, "intermittent": 1800, "standby": 90}[duty]


def _fault_parameter(rng: random.Random, klass: AssetClass, issue: str) -> str:
    """Pick a measurement the fault can plausibly affect."""
    if issue == "unit_error":
        temperatures = [m.parameter for m in klass.measurements if m.unit == "°C"]
        if temperatures:
            return rng.choice(temperatures)
    return rng.choice([m.parameter for m in klass.measurements]).__str__()


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------
def _environment(rng: random.Random, now: datetime) -> tuple[list[dict], dict]:
    """Hourly weather per site, and a lookup the telemetry reads back."""
    start = now - timedelta(hours=HOURS - 1)
    rows: list[dict] = []
    ambient: dict[str, list[float]] = {}
    for site in SITES:
        temperatures: list[float] = []
        wet = 0.0
        for hour in range(HOURS):
            moment = start + timedelta(hours=hour)
            day_of_year = moment.timetuple().tm_yday
            seasonal = site["seasonal_amplitude"] * math.sin(
                2 * math.pi * (day_of_year - 110) / 365.0
            )
            diurnal = -4.6 * math.cos(2 * math.pi * (moment.hour - 14) / 24.0)
            rain = 0.0
            if rng.random() < site["rain_probability"] / 6:
                wet = rng.uniform(3, 10)
            if wet > 0:
                rain = round(rng.uniform(0.4, 9.5), 2)
                wet -= 1
            temperature = (
                site["base_temperature"] + seasonal + diurnal
                - (2.6 if rain else 0.0)
                + rng.gauss(0, 0.7)
            )
            humidity = min(
                99.0,
                max(
                    35.0,
                    site["base_humidity"] - 0.55 * diurnal
                    + (14 if rain else 0) + rng.gauss(0, 3.2),
                ),
            )
            dust = max(
                4.0,
                site["base_dust"] * (0.55 + 0.9 * rng.random())
                * (0.35 if rain else 1.0),
            )
            #  Power quality degrades a little in the afternoon peak, and
            #  occasionally badly - which is a real cause of motor heating and
            #  therefore something the analysis should be able to see.
            thd = 2.3 + 0.8 * max(0.0, math.sin(2 * math.pi * (moment.hour - 15) / 24))
            if rng.random() < 0.004:
                thd += rng.uniform(1.5, 4.0)
            rows.append(
                {
                    "timestamp": moment.strftime(STAMP),
                    "site_id": site["site_id"],
                    "site_name": site["site_name"],
                    "ambient_temperature_c": round(temperature, 2),
                    "relative_humidity_pct": round(humidity, 1),
                    "rainfall_mm": rain,
                    "dust_pm10_ugm3": round(dust, 1),
                    "voltage_thd_pct": round(thd + rng.gauss(0, 0.12), 3),
                    "weather": "rain" if rain else ("cloudy" if humidity > 82 else "fair"),
                }
            )
            temperatures.append(round(temperature, 2))
        ambient[site["site_id"]] = temperatures
    return rows, ambient


# --------------------------------------------------------------------------
# operating context
# --------------------------------------------------------------------------
def _operating(
    rng: random.Random, plan: AssetPlan, now: datetime
) -> tuple[list[dict], list[str], list[float]]:
    """Hour by hour: what the machine was doing and how hard.

    Returned alongside the rows as two parallel lists, because the telemetry
    generator needs exactly these two and re-deriving them would be a second
    definition of the same thing.
    """
    start = now - timedelta(hours=HOURS - 1)
    windows = plan.maintenance_hours()
    rows: list[dict] = []
    states: list[str] = []
    loads: list[float] = []
    runtime = plan.runtime_at_start
    starts = plan.starts_at_start
    previous_running = False

    for hour in range(HOURS):
        moment = start + timedelta(hours=hour)
        if hour < plan.first_hour:
            states.append("not_commissioned")
            loads.append(0.0)
            continue

        in_maintenance = any(low <= hour < high for low, high in windows)
        state, load = _duty_state(rng, plan, moment, hour)
        if in_maintenance:
            state, load = "maintenance", 0.0

        running = state in ("running", "overload", "startup")
        if running:
            runtime += 1.0
        if running and not previous_running:
            starts += 1
        previous_running = running

        rows.append(
            {
                "timestamp": moment.strftime(STAMP),
                "asset_id": plan.asset_id,
                "operating_state": state,
                "running": running,
                "load_pct": round(load * 100, 2),
                "runtime_hours_total": round(runtime, 1),
                "start_count_total": starts,
                "mode": "maintenance" if in_maintenance else "normal",
            }
        )
        states.append(state)
        loads.append(load)
    return rows, states, loads


def _duty_state(
    rng: random.Random, plan: AssetPlan, moment: datetime, hour: int
) -> tuple[str, float]:
    """The state and load implied by this asset's duty pattern."""
    weekday = moment.weekday()
    clock = moment.hour
    swing = 0.10 * math.sin(2 * math.pi * (clock - 10) / 24.0)
    noise = rng.gauss(0, 0.035)

    if plan.duty == "continuous":
        load = plan.base_load + swing + noise
        #  A weekend lull, because a load that never changes teaches an
        #  analysis nothing about whether it depends on load.
        if weekday >= 5:
            load -= 0.16
        state = "running"
        if load > 1.0:
            state = "overload"
        return state, max(0.05, min(1.12, load))

    if plan.duty == "two_shift":
        if weekday >= 5 and rng.random() < 0.75:
            return "idle", 0.0
        if clock == 6:
            return "startup", max(0.1, plan.base_load * 0.5 + noise)
        if clock == 22:
            return "shutdown", max(0.05, plan.base_load * 0.3)
        if 6 < clock < 22:
            load = plan.base_load + swing + noise
            return ("overload" if load > 1.0 else "running"), max(0.1, min(1.15, load))
        return "idle", 0.0

    if plan.duty == "intermittent":
        #  Blocks of a few hours, decided by the hour index so the same asset
        #  behaves the same way on a re-run.
        block = (hour // 5 + hash(plan.asset_id) % 7) % 3
        if block == 0:
            return "idle", 0.0
        load = plan.base_load + swing + noise
        return "running", max(0.1, min(1.1, load))

    #  standby: a weekly no-load test, and the occasional real call-out.
    if weekday == 2 and clock in (10, 11):
        return "running", max(0.15, 0.28 + noise)
    if rng.random() < 0.0015:
        return "running", max(0.3, plan.base_load + noise)
    return "standby", 0.0


# --------------------------------------------------------------------------
# telemetry
# --------------------------------------------------------------------------
def _telemetry(
    rng: random.Random,
    plan: AssetPlan,
    now: datetime,
    ambient: list[float],
    states: list[str],
    loads: list[float],
    columns: dict[str, list],
) -> None:
    """Every reading this asset produced, appended column-wise."""
    start = now - timedelta(hours=HOURS - 1)
    stamps = [
        (start + timedelta(hours=hour)).strftime(STAMP) for hour in range(HOURS)
    ]
    duplicates: list[int] = []
    if plan.scenario == "resolved" and rng.random() < 0.4:
        #  A store that occasionally writes a reading twice. Every real
        #  historian does; an analysis that assumes otherwise is wrong.
        duplicates = sorted(rng.sample(range(plan.first_hour, HOURS), 40))

    for measurement in plan.klass.measurements:
        sensor_id = f"{plan.asset_id}-{measurement.parameter[:4].upper()}"
        stuck_value: float | None = None
        for hour in range(plan.first_hour, HOURS):
            if plan.outage and plan.outage[0] <= hour < plan.outage[1]:
                continue
            state = states[hour] if hour < len(states) else "idle"
            value = _reading(
                rng, plan, measurement, hour, ambient[hour], state, loads[hour]
            )

            quality = "good"
            missing = False
            if value is None:
                quality, missing = "no_data", True
            else:
                #  Instrument faults, applied to the one parameter each
                #  affected asset was given.
                if (
                    plan.quality_issue
                    and measurement.parameter == plan.quality_parameter
                    and plan.quality_from <= hour < plan.quality_to
                ):
                    value, quality, stuck_value = _corrupt(
                        rng, plan, measurement, value, hour, stuck_value
                    )
                if rng.random() < 0.012:
                    value, quality, missing = None, "no_data", True
                elif rng.random() < 0.0022:
                    value = round(value * rng.uniform(2.5, 6.0), measurement.decimals)
                    quality = "suspect"

            rows = [hour] + ([hour] if hour in duplicates else [])
            for _ in rows:
                columns["timestamp"].append(stamps[hour])
                columns["asset_id"].append(plan.asset_id)
                columns["sensor_id"].append(sensor_id)
                columns["parameter"].append(measurement.parameter)
                columns["value"].append(value)
                columns["unit"].append(measurement.unit)
                columns["sampling_interval_minutes"].append(INTERVAL_MINUTES)
                columns["quality"].append(quality)
                columns["missing"].append(missing)


def _reading(
    rng: random.Random,
    plan: AssetPlan,
    measurement: Measurement,
    hour: int,
    ambient: float,
    state: str,
    load: float,
) -> float | None:
    """One value, produced from load, ambient and the current degradation."""
    if state in ("not_commissioned",):
        return None
    if state in ("idle", "standby", "off", "maintenance", "shutdown"):
        if measurement.idle is None or measurement.idle_follows_ambient:
            #  A stopped machine's temperatures fall back towards the room.
            base = ambient + rng.gauss(0, 0.4)
        else:
            base = measurement.idle + abs(rng.gauss(0, measurement.noise * 0.4))
        return round(max(measurement.physical_min, base), measurement.decimals)

    value = (
        measurement.intercept
        + measurement.load_coefficient * load
        + measurement.ambient_coefficient * ambient
    )

    progress = plan.degradation(hour)
    if progress > 0:
        mode_key = plan.mode_at(hour)
        mode = plan.klass.mode(mode_key) if mode_key else None
        if mode:
            effect = mode.effects.get(measurement.parameter)
            if effect:
                #  Accelerating, because degradation is: the last fortnight of
                #  a bearing's life moves further than the first month of it.
                value += effect * (progress ** 1.8)
    #  A degrading machine is a noisier machine, which is itself a signal.
    spread = measurement.noise * (1.0 + 1.6 * progress)
    value += rng.gauss(0, spread)
    value = max(measurement.physical_min, min(measurement.physical_max, value))
    return round(value, measurement.decimals)


def _corrupt(
    rng: random.Random,
    plan: AssetPlan,
    measurement: Measurement,
    value: float,
    hour: int,
    stuck_value: float | None,
) -> tuple[float, str, float | None]:
    """Apply this asset's instrument fault to one reading."""
    issue = plan.quality_issue
    if issue == "stuck":
        if stuck_value is None:
            stuck_value = value
        return stuck_value, "suspect", stuck_value
    if issue == "unit_error":
        #  Celsius reported as Fahrenheit. It reads as a dramatic temperature
        #  rise and is a configuration change on a transmitter.
        return round(value * 9 / 5 + 32, measurement.decimals), "suspect", stuck_value
    if issue == "range_error":
        if rng.random() < 0.10:
            return (
                round(measurement.physical_max * rng.uniform(1.4, 3.0),
                      measurement.decimals),
                "bad",
                stuck_value,
            )
        return value, "good", stuck_value
    #  drift: a slow zero-shift on the transmitter, unaccompanied by any change
    #  in the measurements that would have to move with it if it were real.
    span = max(1, plan.quality_to - plan.quality_from)
    fraction = (hour - plan.quality_from) / span
    offset = abs(measurement.bands[1]) * 1.5 * fraction
    if measurement.direction == "low":
        offset = -offset
    return round(value + offset, measurement.decimals), "suspect", stuck_value


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------
def _history(rng: random.Random, plan: AssetPlan, now: datetime) -> None:
    """Maintenance and failure events, written onto the plan.

    Built before the telemetry so the downtime windows are known when the
    operating context is produced - a maintenance event with no matching gap in
    the record is exactly the kind of inconsistency that makes a demonstration
    dataset useless.
    """
    start = now - timedelta(hours=HOURS - 1)
    klass = plan.klass
    counter = 0

    def new_id(prefix: str) -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}-{plan.asset_id}-{counter:02d}"

    # -- preventive history, walking back from the window -------------------
    if plan.scenario != "new_asset":
        annual = ANNUAL_HOURS[plan.duty]
        for task, interval_hours, action_kind in klass.usage_policy:
            if annual <= 0:
                continue
            every_days = max(20.0, interval_hours / annual * 365.25)
            #  Back to commissioning rather than to an arbitrary three years.
            #  A bearing replacement due every 1,400 operating days has no
            #  record at all inside a three-year window, and an engine that
            #  reads "no record" as "never done" then reports every motor in
            #  the fleet as 240% through its interval.
            life_days = max(0.0, (now - plan.commissioned).days)
            occurrences = min(8, int(life_days / every_days))
            for step in range(occurrences):
                when = now - timedelta(days=every_days * (step + rng.uniform(0.1, 0.5)))
                if when < plan.commissioned:
                    continue
                downtime = round(rng.uniform(1.5, 6.0), 1)
                plan.events.append(
                    {
                        "maintenance_id": new_id("PM"),
                        "asset_id": plan.asset_id,
                        "maintenance_date": when.strftime(STAMP),
                        "maintenance_type": "preventive",
                        "trigger": "usage_interval",
                        "task": task,
                        "action_kind": action_kind,
                        "failure_type": None,
                        "symptom": None,
                        "root_cause": None,
                        "action": f"依保養政策執行{task}",
                        "parts_replaced": None,
                        "downtime_hours": downtime,
                        "labour_hours": round(downtime * rng.uniform(0.7, 1.6), 1),
                        "cost": int(rng.uniform(3_000, 26_000)),
                        "technician": rng.choice(TECHNICIANS),
                        "result": "normal",
                        "notes": "",
                        "related_failure_id": None,
                        "_start_hour": _hour_of(when, start),
                    }
                )
        for task, interval_days, action_kind in klass.time_policy:
            if interval_days > 120:
                continue
            occurrences = min(6, int(WINDOW_DAYS / interval_days))
            for step in range(occurrences):
                when = now - timedelta(days=interval_days * (step + rng.uniform(0.1, 0.6)))
                downtime = round(rng.uniform(0.5, 3.0), 1)
                plan.events.append(
                    {
                        "maintenance_id": new_id("IN"),
                        "asset_id": plan.asset_id,
                        "maintenance_date": when.strftime(STAMP),
                        "maintenance_type": "inspection",
                        "trigger": "calendar",
                        "task": task,
                        "action_kind": action_kind,
                        "failure_type": None,
                        "symptom": None,
                        "root_cause": None,
                        "action": f"定期{task}",
                        "parts_replaced": None,
                        "downtime_hours": downtime,
                        "labour_hours": round(downtime, 1),
                        "cost": int(rng.uniform(1_500, 9_000)),
                        "technician": rng.choice(TECHNICIANS),
                        "result": "normal",
                        "notes": "",
                        "related_failure_id": None,
                        "_start_hour": _hour_of(when, start),
                    }
                )

        # -- failures before the window ----------------------------------
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            mode = rng.choice(klass.modes)
            when = plan.commissioned + timedelta(
                days=rng.uniform(60, max(90.0, (start - plan.commissioned).days or 90))
            )
            if when >= start:
                continue
            _record_failure(rng, plan, mode, when, start, new_id, historic=True)

    # -- the episodes inside the window --------------------------------
    episodes: list[dict | None] = [
        {
            "mode_key": plan.mode_key,
            "failure_hour": plan.failure_hour,
            "repair_hour": plan.repair_hour,
        }
    ]
    if plan.second:
        episodes.append(plan.second)
    for episode in episodes:
        if not episode or not episode.get("mode_key") or episode.get("repair_hour") is None:
            continue
        mode = klass.mode(episode["mode_key"])
        if mode is None:
            continue
        when = start + timedelta(hours=float(episode["failure_hour"]))
        _record_failure(rng, plan, mode, when, start, new_id, historic=False,
                        repair_hour=float(episode["repair_hour"]))

    plan.events.sort(key=lambda event: event["maintenance_date"])
    plan.failures.sort(key=lambda event: event["failure_date"])


def _record_failure(
    rng: random.Random,
    plan: AssetPlan,
    mode,
    when: datetime,
    window_start: datetime,
    new_id,
    *,
    historic: bool,
    repair_hour: float | None = None,
) -> None:
    """One failure and the corrective maintenance that answered it."""
    failure_id = new_id("FL")
    maintenance_id = new_id("CM")
    downtime = round(
        rng.uniform(3, 10) if mode.severity == "minor"
        else rng.uniform(6, 26) if mode.severity == "major"
        else rng.uniform(12, 52),
        1,
    )
    repair_at = (
        window_start + timedelta(hours=repair_hour)
        if repair_hour is not None
        else when + timedelta(hours=rng.uniform(1, 8))
    )
    impact = {
        "minor": "局部產線降載",
        "major": "單一產線停機",
        "critical": "全廠區受影響",
    }[mode.severity]

    plan.failures.append(
        {
            "failure_id": failure_id,
            "asset_id": plan.asset_id,
            "failure_date": when.strftime(STAMP),
            "failure_type": mode.key,
            "failure_type_label": mode.label,
            "failure_mode": mode.label,
            "severity": mode.severity,
            "symptoms": mode.symptom,
            "root_cause": mode.root_cause,
            "downtime_hours": downtime,
            "production_impact": impact,
            "resolution": mode.action,
            "detected_by": (
                "condition_monitoring" if not historic and rng.random() < 0.55
                else rng.choice(["operator_report", "alarm", "routine_inspection"])
            ),
            "related_maintenance_id": maintenance_id,
            "within_window": not historic,
        }
    )
    plan.events.append(
        {
            "maintenance_id": maintenance_id,
            "asset_id": plan.asset_id,
            "maintenance_date": repair_at.strftime(STAMP),
            "maintenance_type": "corrective",
            "trigger": "failure",
            "task": mode.action,
            "action_kind": "repair",
            "failure_type": mode.key,
            "symptom": mode.symptom,
            "root_cause": mode.root_cause,
            "action": mode.action,
            "parts_replaced": mode.parts,
            "downtime_hours": downtime,
            "labour_hours": round(downtime * rng.uniform(0.6, 1.2), 1),
            "cost": int(
                rng.uniform(25_000, 90_000)
                if mode.severity != "critical"
                else rng.uniform(90_000, 420_000)
            ),
            "technician": rng.choice(TECHNICIANS),
            "result": "normal" if rng.random() < 0.9 else "partial",
            "notes": f"{mode.label}：{mode.symptom}",
            "related_failure_id": failure_id,
            "_start_hour": _hour_of(repair_at, window_start) if not historic else None,
        }
    )


def _hour_of(moment: datetime, window_start: datetime) -> float | None:
    delta = (moment - window_start).total_seconds() / 3600.0
    return delta if 0 <= delta < HOURS else None


# --------------------------------------------------------------------------
# reference tables
# --------------------------------------------------------------------------
def _policies() -> list[dict]:
    rows: list[dict] = []
    for klass in CLASSES.values():
        for task, hours, kind in klass.usage_policy:
            rows.append(
                {
                    "policy_id": f"PL-{klass.key}-{len(rows) + 1:03d}",
                    "asset_type": klass.key,
                    "asset_type_label": klass.label,
                    "policy_kind": "usage_based",
                    "task": task,
                    "action_kind": kind,
                    "interval_hours": hours,
                    "interval_days": None,
                    "priority": "medium" if kind == "inspection" else "high",
                    "source": "manufacturer",
                    "policy_version": "2026.1",
                }
            )
        for task, days, kind in klass.time_policy:
            rows.append(
                {
                    "policy_id": f"PL-{klass.key}-{len(rows) + 1:03d}",
                    "asset_type": klass.key,
                    "asset_type_label": klass.label,
                    "policy_kind": "time_based",
                    "task": task,
                    "action_kind": kind,
                    "interval_hours": None,
                    "interval_days": days,
                    "priority": "medium",
                    "source": "regulatory" if "檢" in task else "manufacturer",
                    "policy_version": "2026.1",
                }
            )
    return rows


def _thresholds() -> list[dict]:
    return [
        threshold_row(klass, measurement)
        for klass in CLASSES.values()
        for measurement in klass.measurements
    ]


def _response_model() -> list[dict]:
    return [
        response_row(klass, measurement)
        for klass in CLASSES.values()
        for measurement in klass.measurements
    ]


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------
def generate(target: Path, *, now: datetime | None = None, force: bool = False) -> dict:
    """Write the whole record, unless it is already there.

    Returns the manifest so a caller can report what exists without reading
    every file back.
    """
    target.mkdir(parents=True, exist_ok=True)
    meta_path = target / FILES["meta"]
    if meta_path.exists() and not force:
        return json.loads(meta_path.read_text(encoding="utf-8"))

    now = (now or datetime.now()).replace(minute=0, second=0, microsecond=0)
    window_start = now - timedelta(hours=HOURS - 1)
    rng = random.Random(SEED)

    plans = _plan_fleet(rng, now)
    for plan in plans:
        _history(rng, plan, now)

    environment_rows, ambient = _environment(rng, now)

    assets: list[dict] = []
    specifications: list[dict] = []
    sensors: list[dict] = []
    operating_rows: list[dict] = []
    maintenance_rows: list[dict] = []
    failure_rows: list[dict] = []
    telemetry: dict[str, list] = {
        name: []
        for name in (
            "timestamp", "asset_id", "sensor_id", "parameter", "value", "unit",
            "sampling_interval_minutes", "quality", "missing",
        )
    }

    for plan in plans:
        klass = plan.klass
        assets.append(
            {
                "asset_id": plan.asset_id,
                "asset_name": plan.name,
                "asset_type": klass.key,
                "asset_type_label": klass.label,
                "manufacturer": plan.manufacturer,
                "model_number": plan.model_number,
                "site_id": plan.site["site_id"],
                "site_name": plan.site["site_name"],
                "location": plan.location,
                "installation_date": (
                    plan.commissioned - timedelta(days=14)
                ).strftime("%Y-%m-%d"),
                "commission_date": plan.commissioned.strftime("%Y-%m-%d"),
                "age_years": round((now - plan.commissioned).days / 365.25, 2),
                "design_life_years": klass.design_life_years,
                "mtbf_hours": klass.mtbf_hours,
                "criticality": plan.criticality,
                "duty_pattern": plan.duty,
                "status": "in_service",
                "owner": rng.choice(["生產一課", "生產二課", "設施課", "電氣課"]),
                "cost_center": f"CC-{plan.site['site_id'][-2:]}-{rng.randrange(10, 99)}",
            }
        )
        for name, value, unit in klass.specifications:
            specifications.append(
                {
                    "asset_id": plan.asset_id,
                    "asset_type": klass.key,
                    "specification": name,
                    "value": round(value * rng.uniform(0.85, 1.15), 2),
                    "unit": unit,
                    "source": "nameplate",
                }
            )
        for measurement in klass.measurements:
            sensors.append(
                {
                    "asset_id": plan.asset_id,
                    "sensor_id": f"{plan.asset_id}-{measurement.parameter[:4].upper()}",
                    "parameter": measurement.parameter,
                    "parameter_label": measurement.label,
                    "unit": measurement.unit,
                    "sampling_interval_minutes": INTERVAL_MINUTES,
                    "installed_on": plan.commissioned.strftime("%Y-%m-%d"),
                    "direction": measurement.direction,
                }
            )

        rows, states, loads = _operating(rng, plan, now)
        operating_rows.extend(rows)
        _telemetry(
            rng, plan, now, ambient[plan.site["site_id"]], states, loads, telemetry
        )
        maintenance_rows.extend(
            {k: v for k, v in event.items() if not k.startswith("_")}
            for event in plan.events
        )
        failure_rows.extend(plan.failures)

    #  What actually happened, kept apart from the record the analysis reads.
    #  It exists because this fleet is simulated and its answers are therefore
    #  checkable - which is the whole reason a simulated fleet is worth having.
    #
    #  Episodes are a list. Writing only the first one silently labelled every
    #  asset in its *second* degradation as healthy, and the backtest then
    #  counted the engine's correct answers about them as false alarms - which
    #  is a fault in the answer key, and the worst kind, because it looks like
    #  a fault in the thing being measured.
    #  One row per degradation episode, not one row per asset with a list
    #  inside it. A dataset is a table: the JSON reader normalises a nested
    #  list into text, so an episode list survived the file and disappeared on
    #  ingestion — leaving the backtest scoring against a shorter answer key
    #  than the one on disk, with nothing anywhere reporting a problem.
    #
    #  An asset with no degradation still gets a row, so the scenario it
    #  belongs to is recorded either way.
    truth = []
    for plan in plans:
        common = {
            "asset_id": plan.asset_id,
            "scenario": plan.scenario,
            "progress_at_end": round(plan.degradation(HOURS - 1), 3),
            "instrument_fault": plan.quality_issue,
            "instrument_parameter": plan.quality_parameter,
            "failures_in_window": len(
                [e for e in plan.failures if e.get("within_window")]
            ),
        }
        episodes = _episodes(plan, window_start)
        if not episodes:
            truth.append(
                {
                    **common,
                    "episode": 0,
                    "failure_mode": None,
                    "onset": None,
                    "onset_before_record": False,
                    "failure": None,
                    "repaired": None,
                }
            )
            continue
        for index, episode in enumerate(episodes, start=1):
            truth.append({**common, "episode": index, **episode})

    _write_json(target / FILES["assets"], assets)
    _write_json(target / FILES["specifications"], specifications)
    _write_json(target / FILES["sensors"], sensors)
    _write_json(target / FILES["maintenance"], maintenance_rows)
    _write_json(target / FILES["failures"], failure_rows)
    _write_json(target / FILES["policies"], _policies())
    _write_json(target / FILES["thresholds"], _thresholds())
    _write_json(target / FILES["response"], _response_model())
    _write_json(target / FILES["rules"], list(ENGINEERING_RULES))
    _write_json(target / FILES["truth"], truth)

    Table.from_columns(telemetry).write_parquet(target / FILES["telemetry"])
    Table.from_rows(operating_rows).write_parquet(target / FILES["operating"])
    _write_csv(target / FILES["environment"], environment_rows)

    meta = {
        "generated_at": now.strftime(STAMP),
        "window_start": window_start.strftime(STAMP),
        "window_end": now.strftime(STAMP),
        "window_days": WINDOW_DAYS,
        "sampling_interval_minutes": INTERVAL_MINUTES,
        "seed": SEED,
        "assets": len(assets),
        "sensors": len(sensors),
        "telemetry_rows": len(telemetry["timestamp"]),
        "operating_rows": len(operating_rows),
        "environment_rows": len(environment_rows),
        "maintenance_events": len(maintenance_rows),
        "failure_events": len(failure_rows),
        "failures_in_window": sum(
            1 for row in failure_rows if row.get("within_window")
        ),
        "scenarios": {
            name: sum(1 for plan in plans if plan.scenario == name) for name in SCENARIOS
        },
        "simulated": True,
        "note": (
            "A simulated fleet. Readings are produced from load, ambient "
            "conditions and equipment response coefficients, with degradation "
            "following declared failure modes. Ground truth is in "
            "ground_truth.json, which is what makes the decision policies "
            "measurable."
        ),
    }
    _write_json(meta_path, meta)
    logger.info(
        "generated the maintenance fleet: %s assets, %s readings, %s failures",
        meta["assets"], meta["telemetry_rows"], meta["failure_events"],
    )
    return meta


def ensure_fleet(*, force: bool = False) -> dict:
    """Make sure the record exists, generating it once if it does not.

    Called when the plugin's fixture is built, which is the only hook before
    the datasets that read these files are created. It is a side effect in a
    function that is otherwise a declaration, and it is deliberate: the
    alternative is a fresh clone where the whole application is missing and
    the reason is a file nobody was told to produce.

    Generation is skipped the moment `meta.json` exists, so this costs one
    stat call on every run after the first.
    """
    from .paths import data_dir

    return generate(data_dir(), force=force)


def _episodes(plan: AssetPlan, window_start: datetime) -> list[dict]:
    """Every degradation this asset went through, as dated intervals."""

    def moment(hours: float | None) -> str | None:
        if hours is None:
            return None
        #  Clamped to the start of the record rather than dropped. A
        #  degradation that began before the first reading was still under way
        #  on day one, and writing `null` for its onset removed the whole
        #  episode from the answer key - which then scored the engine's correct
        #  answers about that asset as false alarms.
        return (window_start + timedelta(hours=max(0.0, hours))).strftime(STAMP)

    episodes = []
    for mode_key, onset, failure, repair in (
        (plan.mode_key, plan.onset_hour, plan.failure_hour, plan.repair_hour),
        (
            (plan.second or {}).get("mode_key"),
            (plan.second or {}).get("onset_hour"),
            (plan.second or {}).get("failure_hour"),
            (plan.second or {}).get("repair_hour"),
        ),
    ):
        if not mode_key or onset is None:
            continue
        episodes.append(
            {
                "failure_mode": mode_key,
                "onset": moment(onset),
                "onset_before_record": onset < 0,
                "failure": moment(failure),
                "repaired": moment(repair),
            }
        )
    return episodes


def _write_json(path: Path, rows: Any) -> None:
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _write_csv(path: Path, rows: list[dict]) -> None:
    import csv

    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
