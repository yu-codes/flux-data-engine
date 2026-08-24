"""Shared pydantic base for API payloads.

The platform's vocabulary uses ``model_id`` / ``model_version_id`` throughout,
which collides with pydantic's reserved ``model_`` namespace. Domain language
wins; the namespace guard is turned off here once instead of per class.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
