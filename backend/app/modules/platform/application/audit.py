"""Audit trail for state-changing actions."""

from __future__ import annotations

import logging
from typing import Any

from ..domain.entities import AuditEntry, User
from ..domain.ports import AuditRepository

logger = logging.getLogger(__name__)


class AuditService:
    """Append-only record of who changed what.

    Recording must never break the operation being recorded, so failures are
    logged and swallowed.
    """

    def __init__(self, repository: AuditRepository, *, enabled: bool = True):
        self.repository = repository
        self.enabled = enabled

    def record(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        actor: User | None = None,
        detail: dict[str, Any] | None = None,
        outcome: str = "succeeded",
    ) -> AuditEntry | None:
        if not self.enabled:
            return None
        entry = AuditEntry(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
            detail=detail or {},
            outcome=outcome,
        )
        try:
            return self.repository.add(entry)
        except Exception:
            logger.exception("could not write the audit entry for %s", action)
            return None

    def list(self, **filters) -> list[AuditEntry]:
        return self.repository.list(**filters)
