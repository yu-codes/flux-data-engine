"""FastAPI dependencies.

Routes ask for a service; the container builds the whole graph from a session.
Nothing here knows which concrete repository implements which port — that lives
in `app/core/container.py`, the single composition root.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.container import Services, build_services, get_object_store
from app.core.database import get_session
from app.modules.analysis.application.services import (
    DashboardService,
    ExploreService,
    VisualizationService,
)
from app.modules.applications.application.services import (
    ApplicationService,
)
from app.modules.data.application.services import DatasetService, SourceService
from app.modules.evaluation.application.services import (
    EvaluationService,
    ExperimentService,
)
from app.modules.execution.application.services import ExecutionService
from app.modules.jobs.application.services import JobService
from app.modules.lineage.application.services import LineageService
from app.modules.model.application.services import (
    ModelService,
)
from app.modules.model.domain.registry import PluginRegistry, registry
from app.modules.orchestration.application.schedules import ScheduleService
from app.modules.orchestration.application.services import PipelineService
from app.modules.platform.application.api_keys import ApiKeyService
from app.modules.platform.application.audit import AuditService
from app.modules.platform.application.auth import AuthorizationError, AuthService
from app.modules.platform.application.workspaces import WorkspaceService
from app.modules.platform.infrastructure.api_key_repositories import (
    SqlApiKeyRepository,
)
from app.modules.platform.infrastructure.repositories import SqlUserRepository
from app.modules.platform.infrastructure.workspace_repositories import (
    SqlWorkspaceRepository,
)
from app.modules.reporting.application.services import ReportService
from app.modules.results.application.services import ResultService
from app.shared.scoping import WorkspaceScope
from app.shared.storage import ObjectStore

SessionDep = Annotated[Session, Depends(get_session)]

__all__ = [
    "SessionDep",
    "ServicesDep",
    "SourceServiceDep",
    "DatasetServiceDep",
    "PipelineServiceDep",
    "ModelServiceDep",
    "ExperimentServiceDep",
    "EvaluationServiceDep",
    "ExecutionServiceDep",
    "LineageServiceDep",
    "ResultServiceDep",
    "ReportServiceDep",
    "ExploreServiceDep",
    "VisualizationServiceDep",
    "DashboardServiceDep",
    "ApplicationServiceDep",
    "AuthServiceDep",
    "AuditServiceDep",
    "JobServiceDep",
    "ScheduleServiceDep",
    "WorkspaceServiceDep",
    "ApiKeyServiceDep",
    "ScopeDep",
    "RegistryDep",
    "ObjectStoreDep",
    "get_services",
]


def resolve_scope(request: Request, session: SessionDep) -> WorkspaceScope:
    """Which workspace this request is acting in, and on whose behalf.

    The workspace comes from the `X-Workspace` header - an id or a slug - and
    falls back to the installation's default. A header rather than a path
    prefix so that every existing URL keeps working and a client that knows
    nothing about workspaces still lands somewhere sensible.

    Resolved once, here, and handed to every repository. A request that names
    a workspace the caller is not in is refused rather than silently answered
    from the default, because quietly showing somebody the wrong workspace is
    worse than telling them no.
    """
    workspaces = WorkspaceService(SqlWorkspaceRepository(session))
    requested = request.headers.get("X-Workspace")

    #  A key names its own workspace, so it does not need a header and cannot
    #  be pointed at somebody else's by sending one.
    key = _presented_key(request, session)
    if key is not None:
        return WorkspaceScope(workspace_id=key.workspace_id, user_id=key.id)

    user = _caller(request, session)

    workspace = workspaces.get(requested) if requested else workspaces.default()
    if requested and user is not None and get_settings().auth_enabled:
        if workspaces.role_of(user, workspace.id) is None:
            raise AuthorizationError(
                f"you are not a member of the '{workspace.name}' workspace"
            )
    return WorkspaceScope(
        workspace_id=workspace.id, user_id=getattr(user, "id", None)
    )


def _presented_key(request: Request, session: Session):
    """The API key on this request, if there is a usable one.

    Sent as `X-Api-Key`, kept separate from `Authorization` so that a system
    credential and a person's session are never confused for one another - and
    so a key cannot be silently accepted where a login was expected.
    """
    secret = request.headers.get("X-Api-Key")
    if not secret:
        return None
    return ApiKeyService(SqlApiKeyRepository(session)).resolve(secret)


def _caller(request: Request, session: Session):
    """Who is asking, resolved from the token rather than from request state.

    `current_user` cannot be used here: it needs the services, and the services
    need the scope, so depending on it would be a cycle. The token is decoded
    directly instead - the same decode, one step earlier. A token that does not
    resolve is not an error here; the endpoint's own guard will refuse it in a
    moment, with the right status and message.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return None
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        auth = AuthService(SqlUserRepository(session), settings)
        return auth.user_from_token(header.split(" ", 1)[1].strip())
    except Exception:
        return None


ScopeDep = Annotated[WorkspaceScope, Depends(resolve_scope)]


def get_services(session: SessionDep, scope: ScopeDep) -> Services:
    return build_services(session, scope=scope)


ServicesDep = Annotated[Services, Depends(get_services)]


def _plugin_registry() -> PluginRegistry:
    return registry


def _object_store() -> ObjectStore:
    return get_object_store()


#  One accessor per service, so routes stay readable.
def _sources(services: ServicesDep) -> SourceService:
    return services.sources


def _datasets(services: ServicesDep) -> DatasetService:
    return services.datasets


def _pipelines(services: ServicesDep) -> PipelineService:
    return services.pipelines


def _models(services: ServicesDep) -> ModelService:
    return services.models


def _experiments(services: ServicesDep) -> ExperimentService:
    return services.experiments


def _evaluations(services: ServicesDep) -> EvaluationService:
    return services.evaluations


def _executions(services: ServicesDep) -> ExecutionService:
    return services.executions


def _results(services: ServicesDep) -> ResultService:
    return services.results


def _lineage(services: ServicesDep) -> LineageService:
    return services.lineage


def _reports(services: ServicesDep) -> ReportService:
    return services.reports


def _explore(services: ServicesDep) -> ExploreService:
    return services.explore


def _visualizations(services: ServicesDep) -> VisualizationService:
    return services.visualizations


def _dashboards(services: ServicesDep) -> DashboardService:
    return services.dashboards


def _applications(services: ServicesDep) -> ApplicationService:
    return services.applications



def _auth(services: ServicesDep) -> AuthService:
    return services.auth


def _audit(services: ServicesDep) -> AuditService:
    return services.audit


def _jobs(services: ServicesDep) -> JobService:
    return services.jobs


def _api_keys(services: ServicesDep) -> ApiKeyService:
    return services.api_keys


def _workspaces(services: ServicesDep) -> WorkspaceService:
    return services.workspaces


def _schedules(services: ServicesDep) -> ScheduleService:
    return services.schedules


SourceServiceDep = Annotated[SourceService, Depends(_sources)]
DatasetServiceDep = Annotated[DatasetService, Depends(_datasets)]
PipelineServiceDep = Annotated[PipelineService, Depends(_pipelines)]
ModelServiceDep = Annotated[ModelService, Depends(_models)]
ExperimentServiceDep = Annotated[ExperimentService, Depends(_experiments)]
EvaluationServiceDep = Annotated[EvaluationService, Depends(_evaluations)]
ExecutionServiceDep = Annotated[ExecutionService, Depends(_executions)]
ResultServiceDep = Annotated[ResultService, Depends(_results)]
LineageServiceDep = Annotated[LineageService, Depends(_lineage)]
ReportServiceDep = Annotated[ReportService, Depends(_reports)]
ExploreServiceDep = Annotated[ExploreService, Depends(_explore)]
VisualizationServiceDep = Annotated[VisualizationService, Depends(_visualizations)]
DashboardServiceDep = Annotated[DashboardService, Depends(_dashboards)]
ApplicationServiceDep = Annotated[ApplicationService, Depends(_applications)]
AuthServiceDep = Annotated[AuthService, Depends(_auth)]
AuditServiceDep = Annotated[AuditService, Depends(_audit)]
ScheduleServiceDep = Annotated[ScheduleService, Depends(_schedules)]
JobServiceDep = Annotated[JobService, Depends(_jobs)]
WorkspaceServiceDep = Annotated[WorkspaceService, Depends(_workspaces)]
ApiKeyServiceDep = Annotated[ApiKeyService, Depends(_api_keys)]
RegistryDep = Annotated[PluginRegistry, Depends(_plugin_registry)]
ObjectStoreDep = Annotated[ObjectStore, Depends(_object_store)]
