"""System API: health, capability discovery and a cross-module overview.

This reads from every module by design - it exists to answer "what can
this deployment do, and how much is in it". That makes it the API
layer's own surface rather than any module's, which is why it lives
here beside the router instead of inside `platform`.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import (
    ApplicationServiceDep,
    DatasetServiceDep,
    ExecutionServiceDep,
    ModelServiceDep,
    RegistryDep,
    ResultServiceDep,
    SourceServiceDep,
)
from app.core.config import get_settings
from app.core.observability import metrics
from app.modules.data.infrastructure.readers import supported_source_types
from app.modules.execution.domain.entities import ExecutionKind, ExecutionStatus
from app.modules.model.domain.entities import ModelType, RuntimeKind
from app.modules.results.domain.entities import ResultKind

router = APIRouter(tags=["platform"])


@router.get("/health", summary="Liveness probe")
def health():
    return {"status": "ok"}


@router.get("/metrics", summary="Prometheus text exposition", response_class=Response)
def prometheus_metrics():
    if not get_settings().metrics_enabled:
        return Response(content="# metrics are disabled\n", media_type="text/plain")
    return Response(
        content=metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/metrics/summary", summary="Human-readable counters")
def metrics_summary():
    return metrics.snapshot()


@router.get("/info", summary="Platform capabilities")
def info(registry: RegistryDep):
    settings = get_settings()
    return {
        "name": settings.app_name,
        "abstraction": "Data -> Model -> Execution -> Result -> Application",
        "execution_mode": settings.execution_mode,
        "storage_backend": settings.storage_backend,
        "auth_enabled": settings.auth_enabled,
        "scheduler_enabled": settings.scheduler_enabled,
        "source_types": supported_source_types(),
        "model_types": [t.value for t in ModelType],
        "runtimes": [r.value for r in RuntimeKind],
        "execution_kinds": [k.value for k in ExecutionKind],
        "execution_statuses": [s.value for s in ExecutionStatus],
        "result_kinds": [k.value for k in ResultKind],
        "providers": [
            {"key": d.key, "name": d.name, "model_type": d.model_type.value,
             "trainable": d.trainable}
            for d in registry.descriptors()
        ],
    }


@router.get("/overview", summary="Counts and recent activity for the dashboard")
def overview(
    sources: SourceServiceDep,
    datasets: DatasetServiceDep,
    models: ModelServiceDep,
    executions: ExecutionServiceDep,
    results: ResultServiceDep,
    applications: ApplicationServiceDep,
):
    recent = executions.list(limit=10)
    all_models = models.list()
    by_type: dict[str, int] = {}
    for model in all_models:
        by_type[model.type.value] = by_type.get(model.type.value, 0) + 1

    return {
        "counts": {
            "sources": len(sources.list()),
            "datasets": len(datasets.list()),
            "models": len(all_models),
            "executions": len(executions.list(limit=500)),
            "results": len(results.list(limit=500)),
            "applications": len(applications.list()),
        },
        "models_by_type": by_type,
        "recent_executions": [
            {
                "id": e.id,
                #  What ran, and what kind of runnable it was: the dashboard
                #  lists pipeline runs beside model runs and has to name both.
                "target_id": e.target_id,
                "target_type": e.target_type.value,
                "model_id": e.model_id,
                "kind": e.kind.value,
                "status": e.status.value,
                "duration_seconds": e.duration_seconds,
                "created_at": e.created_at,
                "result_id": e.result_id,
            }
            for e in recent
        ],
    }


@router.get("/execution-queue", summary="What the execution queue is holding")
def execution_queue(executions: ExecutionServiceDep):
    """Named for what it shows.

    It was `/jobs`, which is now a resource of its own - a pipeline run, an
    experiment run, a report export. This is the queue of single executions,
    which is a different thing, and having one name mean both was going to
    confuse somebody eventually.
    """
    pending = executions.list(status=ExecutionStatus.PENDING.value, limit=100)
    running = executions.list(status=ExecutionStatus.RUNNING.value, limit=100)
    failed = executions.list(status=ExecutionStatus.FAILED.value, limit=50)
    return {
        "mode": get_settings().execution_mode,
        "pending": [_job(e) for e in pending],
        "running": [_job(e) for e in running],
        "failed": [_job(e) for e in failed],
    }


def _job(execution) -> dict:
    return {
        "id": execution.id,
        "model_id": execution.model_id,
        "kind": execution.kind.value,
        "status": execution.status.value,
        "error": execution.error,
        "created_at": execution.created_at,
    }
