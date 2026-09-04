"""SQL persistence for schedules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.ids import utcnow
from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.schedules import Schedule, ScheduleStatus
from . import schedule_orm as orm


class SqlScheduleRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, schedule: Schedule) -> Schedule:
        self.session.add(self._stamp(orm.schedule_to_row(self._file(schedule))))
        self.session.flush()
        return schedule

    def get(self, schedule_id: str) -> Schedule | None:
        row = self._fetch(orm.ScheduleRow, schedule_id)
        return orm.schedule_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Schedule | None:
        row = self.session.scalar(
            self._named(select(orm.ScheduleRow), orm.ScheduleRow)
            .where(orm.ScheduleRow.name == name)
        )
        return orm.schedule_to_entity(row) if row else None

    def list(self, *, status: str | None = None) -> list[Schedule]:
        stmt = self._scoped(select(orm.ScheduleRow), orm.ScheduleRow)
        if status:
            stmt = stmt.where(orm.ScheduleRow.status == status)
        rows = self.session.scalars(
            stmt.order_by(orm.ScheduleRow.created_at.desc())
        ).all()
        return [orm.schedule_to_entity(r) for r in rows]

    def due(self, limit: int = 25) -> list[Schedule]:
        """Active schedules, soonest first.

        The "is it due yet" decision stays in the domain rather than in SQL:
        Postgres stores timezone-aware timestamps and SQLite naive ones, so a
        comparison written here would be right on one backend and wrong on the
        other. The set of active schedules is small enough to filter in Python.
        """
        rows = self.session.scalars(
            self._scoped(select(orm.ScheduleRow), orm.ScheduleRow)
            .where(orm.ScheduleRow.status == ScheduleStatus.ACTIVE.value)
            .order_by(orm.ScheduleRow.next_run_at.asc().nulls_first())
        ).all()
        now = utcnow()
        schedules = [orm.schedule_to_entity(r) for r in rows]
        return [s for s in schedules if s.is_due(now)][:limit]

    def update(self, schedule: Schedule) -> Schedule:
        row = self._fetch(orm.ScheduleRow, schedule.id)
        orm.schedule_to_row(schedule, row)
        self.session.flush()
        return schedule

    def delete(self, schedule_id: str) -> None:
        row = self._fetch(orm.ScheduleRow, schedule_id)
        if row:
            self.session.delete(row)
            self.session.flush()
