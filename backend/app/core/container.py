"""The composition root.

One place builds the service graph from a session. The API (`api/deps.py`),
the seeder and the background worker all go through here, so there is exactly
one description of which concrete infrastructure implements which port.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.modules.analysis.application.services import (
    DashboardService,
    ExploreService,
    VisualizationService,
)
from app.modules.analysis.infrastructure.repositories import (
    SqlDashboardRepository,
    SqlVisualizationRepository,
)
from app.modules.applications.application.services import (
    ApplicationService,
)
from app.modules.applications.infrastructure.repositories import (
    SqlApplicationRepository,
)
from app.modules.data.application.services import DatasetService, SourceService
from app.modules.data.infrastructure.readers import get_reader
from app.modules.data.infrastructure.repositories import (
    SqlDatasetRepository,
    SqlSchemaRepository,
    SqlSourceRepository,
)
from app.modules.evaluation.application.services import (
    EvaluationService,
    ExperimentService,
)
from app.modules.evaluation.infrastructure.repositories import (
    SqlEvaluationRepository,
    SqlExperimentRepository,
)
from app.modules.execution.application.services import ExecutionService
from app.modules.execution.domain.entities import RunnableKind
from app.modules.execution.infrastructure.dispatch import build_dispatcher
from app.modules.execution.infrastructure.repositories import SqlExecutionRepository
from app.modules.jobs.application.services import JobService
from app.modules.jobs.infrastructure.dispatch import build_job_dispatcher
from app.modules.jobs.infrastructure.repositories import SqlJobRepository
from app.modules.lineage.application.services import LineageService
from app.modules.model.application.services import (
    ModelService,
)
from app.modules.model.domain.registry import registry
from app.modules.model.infrastructure.repositories import (
    SqlModelRepository,
)
from app.modules.orchestration.application.runner import pipeline_runner
from app.modules.orchestration.application.schedules import ScheduleService
from app.modules.orchestration.application.services import PipelineService
from app.modules.orchestration.infrastructure.repositories import (
    SqlPipelineRepository,
)
from app.modules.orchestration.infrastructure.schedule_repositories import (
    SqlScheduleRepository,
)
from app.modules.platform.application.api_keys import ApiKeyService
from app.modules.platform.application.audit import AuditService
from app.modules.platform.application.auth import AuthService
from app.modules.platform.application.projects import ProjectService
from app.modules.platform.application.workspaces import WorkspaceService
from app.modules.platform.infrastructure.api_key_repositories import (
    SqlApiKeyRepository,
)
from app.modules.platform.infrastructure.project_repositories import (
    SqlProjectRepository,
)
from app.modules.platform.infrastructure.repositories import (
    SqlAuditRepository,
    SqlUserRepository,
)
from app.modules.platform.infrastructure.workspace_repositories import (
    SqlWorkspaceRepository,
)
from app.modules.reporting.application.services import ReportService
from app.modules.reporting.infrastructure.repositories import SqlReportRepository
from app.modules.results.application.services import ResultService
from app.modules.results.infrastructure.repositories import SqlResultRepository
from app.shared.scoping import WorkspaceScope
from app.shared.storage import ObjectStore, store_from_settings


@lru_cache
def get_object_store() -> ObjectStore:
    """Process-wide store. Local filesystem or S3/MinIO, per configuration."""
    return store_from_settings(get_settings())


def step_worker(settings: Settings, scope: WorkspaceScope):
    """A callable that runs one pipeline step in a session of its own.

    Threads are why the session is new: a SQLAlchemy Session belongs to the
    thread that made it, so a worker that borrowed the caller's would trade a
    slow pipeline for a corrupted one. Each step commits its own execution,
    result and dataset; the run record stays with the thread that started it.
    """

    def run_step(
        pipeline_id: str,
        step_name: str,
        order: int,
        source_version_id,
        source_table,
        extra_inputs,
        depth: int,
    ):
        from app.core.database import session_scope

        with session_scope() as worker_session:
            services = build_services(
                worker_session, settings=settings, scope=scope
            )
            return services.pipelines.run_step_standalone(
                pipeline_id,
                step_name,
                order,
                source_version_id,
                source_table,
                extra_inputs,
                depth,
            )

    return run_step


@dataclass
class Services:
    """Every application service, wired for one unit of work."""

    session: Session
    settings: Settings

    sources: SourceService
    datasets: DatasetService
    pipelines: PipelineService

    models: ModelService
    experiments: ExperimentService
    evaluations: EvaluationService

    executions: ExecutionService
    results: ResultService
    lineage: LineageService
    reports: ReportService

    explore: ExploreService
    visualizations: VisualizationService
    dashboards: DashboardService

    applications: ApplicationService

    auth: AuthService
    workspaces: WorkspaceService
    projects: ProjectService
    api_keys: ApiKeyService
    audit: AuditService
    schedules: ScheduleService
    jobs: JobService


def build_services(
    session: Session,
    *,
    settings: Settings | None = None,
    scope: WorkspaceScope | None = None,
) -> Services:
    """Wire every service for one unit of work, inside one workspace.

    The scope is passed to every repository rather than checked in every
    service: isolation that depends on each caller remembering to filter is
    isolation that will eventually be forgotten somewhere.

    No scope means every workspace, which is what the background worker needs -
    it pops whatever the shared queue hands it.
    """
    settings = settings or get_settings()
    store = get_object_store()
    scope = scope or WorkspaceScope.unscoped()

    projects = ProjectService(
        SqlProjectRepository(session, scope),
        data_root=settings.data_root,
    )
    #  Where this request's uploads land. Resolved once here rather than in
    #  the source service, which should not have to know that projects exist
    #  in order to put a file somewhere.
    current = projects.find(scope.project_id)
    upload_directory = current.uploads_path if current else "uploads"

    sources = SourceService(
        SqlSourceRepository(session, scope),
        get_reader,
        upload_root=settings.data_root,
        upload_directory=upload_directory,
    )
    datasets = DatasetService(
        datasets=SqlDatasetRepository(
            session, scope
        ),
        schemas=SqlSchemaRepository(
            session, scope
        ),
        sources=SqlSourceRepository(
            session, scope
        ),
        store=store,
        readers=get_reader,
        storage_prefix=(current.slug if current else None),
    )
    models = ModelService(
        SqlModelRepository(session, scope), registry
    )
    results = ResultService(
        repository=SqlResultRepository(session, scope), store=store, datasets=datasets
    )
    executions = ExecutionService(
        repository=SqlExecutionRepository(
            session, scope
        ),
        models=models,
        datasets=datasets,
        results=results,
        registry=registry,
        store=store,
        dispatcher=build_dispatcher(session, settings),
    )
    visualizations = VisualizationService(
        SqlVisualizationRepository(session, scope), datasets, results
    )
    applications = ApplicationService(SqlApplicationRepository(session, scope))
    evaluations = EvaluationService(SqlEvaluationRepository(session, scope))

    #  Built before the container so the handlers can close over it.
    pipelines = PipelineService(
        repository=SqlPipelineRepository(
            session, scope
        ),
        datasets=datasets,
        executions=executions,
        results=results,
        #  Steps that do not read from each other run at the same time, each
        #  in a session of its own. Building that session is this file's job:
        #  a pipeline knows which steps are independent, not how a unit of
        #  work is made.
        worker=step_worker(settings, scope),
        max_parallel=(
            settings.pipeline_max_parallel_steps
            if settings.steps_may_run_in_parallel
            else 1
        ),
    )
    #  Now that both exist, execution learns how to run the other kind of
    #  runnable. Injected rather than imported: `orchestration` sits above
    #  `execution` in the dependency stack and that direction stays one-way.
    executions.runners[RunnableKind.PIPELINE.value] = pipeline_runner(pipelines)

    experiments = ExperimentService(
        repository=SqlExperimentRepository(
            session, scope
        ),
        models=models,
        datasets=datasets,
        registry=registry,
        evaluations=evaluations,
        executions=executions,
        pipelines=pipelines,
    )
    reports = ReportService(
        repository=SqlReportRepository(
            session, scope
        ),
        results=results,
        executions=executions,
        models=models,
        datasets=datasets,
        store=store,
    )

    #  What "background work" means in this build. The jobs module knows none
    #  of these kinds; naming them here is what keeps it underneath the things
    #  it runs rather than importing every one of them.
    job_handlers = {
        "pipeline_run": lambda job: _ran_pipeline(
            pipelines.run(job.target_id, **job.parameters)
        ),
        "experiment_run": lambda job: _ran_experiment(
            experiments.run(job.target_id, executions)
        ),
        "report_export": lambda job: reports.export(
            job.target_id, job.parameters.get("format", "markdown")
        ),
    }

    #  Built before the container so the schedule service can fire one: a
    #  scheduled pipeline is submitted as a job, not run inside the tick.
    job_service = JobService(
        repository=SqlJobRepository(session, scope),
        handlers=job_handlers,
        dispatcher=build_job_dispatcher(session, settings),
    )

    dashboards = DashboardService(SqlDashboardRepository(session, scope), visualizations)

    lineage = LineageService(
        sources=sources,
        datasets=datasets,
        models=models,
        pipelines=pipelines,
        executions=executions,
        results=results,
        visualizations=visualizations,
        dashboards=dashboards,
    )

    #  What a project holds, so it can refuse to be deleted out from under
    #  forty datasets. Injected as a callable for the same reason job handlers
    #  are: `platform` sits at the bottom of the stack and must not learn what
    #  a dataset is.
    projects.usage = _holdings(
        sources=sources,
        datasets=datasets,
        pipelines=pipelines,
        models=models,
        visualizations=visualizations,
        dashboards=dashboards,
        experiments=experiments,
    )

    return Services(
        session=session,
        settings=settings,
        sources=sources,
        datasets=datasets,
        pipelines=pipelines,
        models=models,
        experiments=experiments,
        evaluations=evaluations,
        executions=executions,
        results=results,
        lineage=lineage,
        reports=reports,
        explore=ExploreService(datasets),
        visualizations=visualizations,
        dashboards=dashboards,
        applications=applications,
        auth=AuthService(SqlUserRepository(session, scope), settings),
        workspaces=WorkspaceService(SqlWorkspaceRepository(session)),
        projects=projects,
        api_keys=ApiKeyService(SqlApiKeyRepository(session)),
        audit=AuditService(
            SqlAuditRepository(session, scope), enabled=settings.audit_enabled
        ),
        schedules=ScheduleService(
            SqlScheduleRepository(session, scope),
            executions,
            pipelines=pipelines,
            jobs=job_service,
        ),
        jobs=job_service,
    )


def _holdings(**services):
    """A counter of what a project holds, keyed by what a person would call it.

    Counted through the ordinary listing services, which are already scoped to
    the workspace — so this cannot report resources the caller may not see.
    """

    def count(project_id: str) -> dict[str, int]:
        held: dict[str, int] = {}
        for label, service in services.items():
            try:
                rows = service.list()
            except Exception:  # a listing that fails must not block a delete
                continue
            held[label] = sum(
                1 for row in rows if getattr(row, "project_id", None) == project_id
            )
        return held

    return count


def _ran_pipeline(run) -> dict:
    """What a caller needs to find the pipeline run this job produced."""
    return {
        "pipeline_run_id": run.id,
        "status": run.status.value,
        "steps": len(run.step_runs),
        "failed_steps": [s.step_name for s in run.step_runs if s.error],
    }


def _ran_experiment(experiment) -> dict:
    return {
        "experiment_id": experiment.id,
        "trials": len(experiment.trials),
        "executions": len(experiment.execution_ids),
    }
