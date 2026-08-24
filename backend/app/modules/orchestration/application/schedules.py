"""Recurring executions.

A Schedule stores everything an Execution needs; the worker's scheduler loop
asks for the due ones and submits them. Nothing else about Execution changes —
a scheduled run is an ordinary run with a `schedule_id` in its context.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.modules.execution.application.services import ExecutionService
from app.shared.errors import ConflictError, NotFoundError, ValidationError
from app.shared.ids import utcnow

from ..domain.schedule_ports import ScheduleRepository
from ..domain.schedules import (
    MIN_INTERVAL_SECONDS,
    Schedule,
    ScheduleStatus,
    ScheduleTarget,
    next_cron_time,
    parse_cron,
)

logger = logging.getLogger(__name__)


class ScheduleService:
    def __init__(
        self,
        repository: ScheduleRepository,
        executions: ExecutionService,
        *,
        pipelines=None,
        jobs=None,
    ):
        self.repository = repository
        self.executions = executions
        #  Both optional so a schedule service can still be built for
        #  model-only use - the worker and the API get all three.
        self.pipelines = pipelines
        self.jobs = jobs

    # -- reads -------------------------------------------------------------
    def get(self, schedule_id: str) -> Schedule:
        schedule = self.repository.get(schedule_id)
        if not schedule:
            raise NotFoundError(f"schedule '{schedule_id}' not found")
        return schedule

    def list(self, *, status: str | None = None) -> list[Schedule]:
        return self.repository.list(status=status)

    def preview(self, *, cron: str | None, interval_seconds: int | None,
                count: int = 5) -> list[datetime]:
        """The next few fire times, so the UI can show what was just typed."""
        moments: list[datetime] = []
        moment = utcnow()
        for _ in range(count):
            if cron:
                moment = next_cron_time(cron, moment)
            else:
                from datetime import timedelta

                moment = moment + timedelta(
                    seconds=max(int(interval_seconds or 0), MIN_INTERVAL_SECONDS)
                )
            moments.append(moment)
        return moments

    # -- writes ------------------------------------------------------------
    def create(
        self,
        *,
        name: str,
        target_id: str,
        target_type: str = ScheduleTarget.MODEL.value,
        kind: str = "prediction",
        interval_seconds: int | None = None,
        cron: str | None = None,
        dataset_id: str | None = None,
        dataset_version_id: str | None = None,
        input_payload: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        description: str = "",
        created_by: str | None = None,
    ) -> Schedule:
        if self.repository.get_by_name(name):
            raise ConflictError(f"a schedule named '{name}' already exists")
        self._validate_trigger(interval_seconds, cron)
        target = ScheduleTarget(target_type)
        #  Fail now rather than at 3am: whatever is being scheduled must exist.
        self._resolve_target(target, target_id)

        schedule = Schedule(
            name=name,
            target_id=target_id,
            target_type=target,
            kind=kind,
            interval_seconds=interval_seconds,
            cron=cron,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            input_payload=input_payload or {},
            parameters=parameters or {},
            description=description,
            created_by=created_by,
        )
        schedule.schedule_next()
        return self.repository.add(schedule)

    def update(self, schedule_id: str, changes: dict[str, Any]) -> Schedule:
        schedule = self.get(schedule_id)
        for key in ("description", "kind", "dataset_id", "dataset_version_id"):
            if changes.get(key) is not None:
                setattr(schedule, key, changes[key])
        for key in ("input_payload", "parameters"):
            if changes.get(key) is not None:
                setattr(schedule, key, changes[key])

        trigger_changed = "interval_seconds" in changes or "cron" in changes
        if trigger_changed:
            interval = changes.get("interval_seconds", schedule.interval_seconds)
            cron = changes.get("cron", schedule.cron)
            #  Setting one trigger clears the other; they are mutually exclusive.
            if changes.get("cron"):
                interval = None
            elif changes.get("interval_seconds"):
                cron = None
            self._validate_trigger(interval, cron)
            schedule.interval_seconds = interval
            schedule.cron = cron
            schedule.schedule_next()

        if changes.get("status"):
            schedule.status = ScheduleStatus(changes["status"])
            if schedule.is_active and schedule.next_run_at is None:
                schedule.schedule_next()

        schedule.updated_at = utcnow()
        return self.repository.update(schedule)

    def pause(self, schedule_id: str) -> Schedule:
        return self.update(schedule_id, {"status": ScheduleStatus.PAUSED.value})

    def resume(self, schedule_id: str) -> Schedule:
        schedule = self.get(schedule_id)
        schedule.status = ScheduleStatus.ACTIVE
        schedule.schedule_next()
        schedule.updated_at = utcnow()
        return self.repository.update(schedule)

    def delete(self, schedule_id: str) -> None:
        self.repository.delete(self.get(schedule_id).id)

    # -- firing ------------------------------------------------------------
    def run_now(self, schedule_id: str) -> Schedule:
        """Fire a schedule immediately without disturbing its cadence."""
        return self._fire(self.get(schedule_id), reschedule=False)

    def run_due(self, *, limit: int = 25, now: datetime | None = None) -> list[Schedule]:
        """Fire every schedule whose time has come. Called by the worker."""
        now = now or utcnow()
        fired: list[Schedule] = []
        for schedule in self.repository.due(limit=limit):
            if not schedule.is_due(now):
                continue
            fired.append(self._fire(schedule, reschedule=True, now=now))
        return fired

    def _fire(
        self, schedule: Schedule, *, reschedule: bool, now: datetime | None = None
    ) -> Schedule:
        now = now or utcnow()
        try:
            if schedule.target_type is ScheduleTarget.PIPELINE:
                ran_id, status = self._fire_pipeline(schedule)
            else:
                ran_id, status = self._fire_model(schedule)
            schedule.last_execution_id = ran_id
            schedule.last_status = status
            if status == "failed":
                schedule.failure_count += 1
        except Exception as exc:
            #  A broken schedule must not stop the ones behind it in the queue.
            logger.exception("schedule '%s' failed to submit", schedule.name)
            schedule.last_status = "failed"
            schedule.last_execution_id = None
            schedule.failure_count += 1
            schedule.last_error = f"{type(exc).__name__}: {exc}"

        schedule.last_run_at = now
        schedule.run_count += 1
        if reschedule:
            schedule.schedule_next(after=now)
        schedule.updated_at = now
        return self.repository.update(schedule)

    # -- internals ---------------------------------------------------------
    def _resolve_target(self, target: ScheduleTarget, target_id: str) -> None:
        """Refuse a schedule that names something which is not there."""
        if target is ScheduleTarget.PIPELINE:
            if self.pipelines is None:
                raise ValidationError(
                    "this deployment cannot schedule pipelines"
                )
            self.pipelines.get(target_id)
            return
        self.executions.models.get(target_id)

    def _fire_model(self, schedule: Schedule) -> tuple[str | None, str]:
        execution = self.executions.submit(
            model_id=schedule.target_id,
            kind=schedule.kind,
            dataset_id=schedule.dataset_id,
            dataset_version_id=schedule.dataset_version_id,
            input_payload=schedule.input_payload,
            parameters=schedule.parameters,
            context={"schedule_id": schedule.id, "schedule_name": schedule.name},
        )
        return execution.id, execution.status.value

    def _fire_pipeline(self, schedule: Schedule) -> tuple[str | None, str]:
        """Submit the pipeline as a background job.

        A pipeline run is not one Execution, so there is no execution id to
        record - what a schedule points at afterwards is the Job, which is the
        thing that can be watched, cancelled and retried. Going through the
        queue rather than running it here is the point: a scheduler loop must
        not spend twenty minutes inside one tick.
        """
        if self.jobs is None:
            raise ValidationError("this deployment cannot schedule pipelines")
        job = self.jobs.submit(
            kind="pipeline_run",
            target_id=schedule.target_id,
            parameters={
                "dataset_version_id": schedule.dataset_version_id,
                **(schedule.parameters or {}),
            },
        )
        return job.id, job.status.value

    @staticmethod
    def _validate_trigger(interval_seconds: int | None, cron: str | None) -> None:
        if bool(interval_seconds) == bool(cron):
            raise ValidationError(
                "give exactly one trigger: interval_seconds or cron"
            )
        if interval_seconds is not None and interval_seconds < MIN_INTERVAL_SECONDS:
            raise ValidationError(
                f"interval_seconds must be at least {MIN_INTERVAL_SECONDS}"
            )
        if cron:
            try:
                parse_cron(cron)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
