"""SQL persistence for jobs."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.util import identity_key

from app.shared.ids import utcnow
from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import Job, JobStatus
from . import orm


class SqlJobRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, job: Job) -> Job:
        self.session.add(self._stamp(orm.to_row(self._file(job))))
        self.session.flush()
        return job

    def get(self, job_id: str) -> Job | None:
        row = self._fetch(orm.JobRow, job_id)
        return orm.to_entity(row) if row else None

    def update(self, job: Job) -> Job:
        row = self._fetch(orm.JobRow, job.id)
        if row is None:
            return self.add(job)
        orm.to_row(job, row)
        self.session.flush()
        return orm.to_entity(row)

    def claim(self, job_id: str) -> Job | None:
        """Move PENDING -> RUNNING in one statement, or answer None."""
        now = utcnow()
        result = self.session.execute(
            update(orm.JobRow)
            .where(
                orm.JobRow.id == job_id,
                orm.JobRow.status == JobStatus.PENDING.value,
            )
            .values(
                status=JobStatus.RUNNING.value,
                started_at=now,
                heartbeat_at=now,
                attempts=orm.JobRow.attempts + 1,
                updated_at=now,
            )
        )
        if not result.rowcount:
            return None
        #  See `SqlExecutionRepository.claim`: expire the row the Core UPDATE
        #  changed, not everything the session happens to have loaded.
        loaded = self.session.identity_map.get(identity_key(orm.JobRow, job_id))
        if loaded is not None:
            self.session.expire(loaded)
        return self.get(job_id)

    def list(
        self,
        *,
        kind: str | None = None,
        target_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Job]:
        stmt = self._scoped(select(orm.JobRow), orm.JobRow)
        if kind:
            stmt = stmt.where(orm.JobRow.kind == kind)
        if target_id:
            stmt = stmt.where(orm.JobRow.target_id == target_id)
        if status:
            stmt = stmt.where(orm.JobRow.status == status)
        stmt = stmt.order_by(orm.JobRow.created_at.desc()).limit(limit)
        return [orm.to_entity(row) for row in self.session.scalars(stmt)]
