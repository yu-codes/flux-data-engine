"""Schedules: run a model on a timer.

A schedule submits executions, so it belongs beside pipelines rather than
beside users and audit - the two have nothing in common except that both
were once called "platform".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from app.shared.ids import new_id, utcnow


class ScheduleStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class ScheduleTarget(str, Enum):
    """What a schedule fires.

    A schedule used to name a model and nothing else, which meant the one kind
    of recurring work a data platform is most often asked for - re-run this
    pipeline every morning - could not be expressed at all. The trigger, the
    cadence and the bookkeeping are identical for both; only the verb differs.
    """

    MODEL = "model"
    PIPELINE = "pipeline"


@dataclass
class Schedule:
    """A recurring execution.

    The trigger is an interval plus an optional start time. Cron expressions
    are supported through `cron`, evaluated by the worker's scheduler loop.
    """

    name: str
    target_id: str
    target_type: ScheduleTarget = ScheduleTarget.MODEL
    kind: str = "prediction"
    interval_seconds: int | None = None
    cron: str | None = None
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    description: str = ""
    last_run_at: datetime | None = None
    last_execution_id: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    next_run_at: datetime | None = None
    run_count: int = 0
    failure_count: int = 0
    created_by: str | None = None
    id: str = field(default_factory=lambda: new_id("sch"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    @property
    def is_active(self) -> bool:
        return self.status is ScheduleStatus.ACTIVE

    def is_due(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        if not self.is_active:
            return False
        if self.next_run_at is None:
            return True
        return _aware(self.next_run_at) <= now

    def schedule_next(self, *, after: datetime | None = None) -> datetime:
        """Compute and store the next fire time."""
        base = _aware(after or utcnow())
        self.next_run_at = next_fire_time(self, base)
        return self.next_run_at


def _aware(value: datetime) -> datetime:
    """Postgres returns aware datetimes, SQLite naive ones; normalise to UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def next_fire_time(schedule: Schedule, after: datetime) -> datetime:
    """Next run for an interval or a five-field cron expression."""
    if schedule.cron:
        return next_cron_time(schedule.cron, after)
    seconds = max(int(schedule.interval_seconds or 0), MIN_INTERVAL_SECONDS)
    return after + timedelta(seconds=seconds)


MIN_INTERVAL_SECONDS = 30
#  A year of minutes is a safe upper bound for the cron search below.
_CRON_SEARCH_LIMIT_MINUTES = 366 * 24 * 60


def parse_cron(expression: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    """Parse ``minute hour day-of-month month day-of-week`` into allowed sets.

    Supports ``*``, ``a,b``, ``a-b`` and ``*/n`` — the subset that covers the
    schedules this platform realistically needs, with no extra dependency.
    """
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(
            f"a cron expression needs 5 fields "
            f"(minute hour day month weekday), got {len(fields)}"
        )
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    return tuple(  # type: ignore[return-value]
        _parse_cron_field(field, low, high)
        for field, (low, high) in zip(fields, ranges, strict=True)
    )


def _parse_cron_field(field: str, low: int, high: int) -> set[int]:
    allowed: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, _, raw_step = part.partition("/")
            if not raw_step.isdigit() or int(raw_step) < 1:
                raise ValueError(f"invalid cron step in '{field}'")
            step = int(raw_step)
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            raw_start, _, raw_end = part.partition("-")
            start, end = _cron_int(raw_start, field), _cron_int(raw_end, field)
        else:
            start = end = _cron_int(part, field)
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise ValueError(f"cron field '{field}' is out of range {low}-{high}")
        allowed.update(range(start, end + 1, step))
    if not allowed:
        raise ValueError(f"cron field '{field}' matches nothing")
    return allowed


def _cron_int(raw: str, field: str) -> int:
    if not raw.strip().isdigit():
        raise ValueError(f"invalid cron field '{field}'")
    return int(raw.strip())


def next_cron_time(expression: str, after: datetime) -> datetime:
    """First minute strictly after `after` that satisfies the expression."""
    minutes, hours, days, months, weekdays = parse_cron(expression)
    moment = _aware(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(_CRON_SEARCH_LIMIT_MINUTES):
        #  Python's Monday=0 maps to cron's Monday=1; cron Sunday is 0.
        weekday = (moment.weekday() + 1) % 7
        if (
            moment.minute in minutes
            and moment.hour in hours
            and moment.day in days
            and moment.month in months
            and weekday in weekdays
        ):
            return moment
        moment += timedelta(minutes=1)
    raise ValueError(f"cron expression '{expression}' never fires")
