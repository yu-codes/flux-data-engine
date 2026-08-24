"""Cross-cutting domain errors.

Errors are raised by the domain/application layers and translated into HTTP
responses by the API layer, so no module needs to know about FastAPI.
"""

from __future__ import annotations


class FluxError(Exception):
    """Base class for every error the platform raises deliberately."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(FluxError):
    status_code = 404
    code = "not_found"


class ValidationError(FluxError):
    status_code = 422
    code = "validation_failed"


class ConflictError(FluxError):
    status_code = 409
    code = "conflict"


class ImmutabilityError(ConflictError):
    """Raised when an immutable object (a model version) is modified."""

    code = "immutable"


class PluginError(FluxError):
    status_code = 400
    code = "plugin_error"


class ExecutionError(FluxError):
    status_code = 400
    code = "execution_failed"


class UnsupportedError(FluxError):
    status_code = 400
    code = "unsupported"
