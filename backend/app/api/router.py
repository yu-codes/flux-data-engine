"""Aggregates every module's router under the versioned API prefix.

Each router is mounted with its module guard, so authorisation is decided once
per module rather than remembered route by route. Only the auth router is
mounted bare — signing in is what produces the token the guards check.

Plugins may contribute routes of their own; they are collected through
`app.plugins.contrib` so that adding a built-in application never means editing
this file, and nothing here names a particular domain.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.security import (
    ANALYSIS_GUARD,
    APPLICATION_GUARD,
    DATA_GUARD,
    EXECUTION_GUARD,
    MODEL_GUARD,
    PLATFORM_GUARD,
    RESULT_GUARD,
    SERVING_GUARD,
)
from app.api.system_routes import router as platform_router
from app.modules.analysis.api.routes import router as analysis_router
from app.modules.applications.api.public_routes import router as public_router
from app.modules.applications.api.routes import router as applications_router
from app.modules.data.api.routes import router as data_router
from app.modules.evaluation.api.routes import router as evaluation_router
from app.modules.execution.api.routes import router as execution_router
from app.modules.execution.api.serving_routes import router as serving_router
from app.modules.jobs.api.routes import router as jobs_router
from app.modules.lineage.api.routes import router as lineage_router
from app.modules.model.api.routes import router as model_router
from app.modules.orchestration.api.routes import router as pipeline_router
from app.modules.orchestration.api.schedule_routes import router as schedule_router
from app.modules.orchestration.api.serving_routes import (
    router as pipeline_serving_router,
)
from app.modules.platform.api.api_key_routes import router as api_key_router
from app.modules.platform.api.auth_routes import router as auth_router
from app.modules.platform.api.project_routes import router as project_router
from app.modules.platform.api.workspace_routes import router as workspace_router
from app.modules.reporting.api.routes import router as report_router
from app.modules.results.api.routes import router as results_router
from app.plugins.contrib import contributed_routers

api_router = APIRouter()

#  Sign-in and self-service: no module guard, individual endpoints still
#  require a valid token where it matters.
api_router.include_router(auth_router)

_GUARDED = (
    (platform_router, PLATFORM_GUARD),
    (schedule_router, PLATFORM_GUARD),
    (data_router, DATA_GUARD),
    (pipeline_router, DATA_GUARD),
    (analysis_router, ANALYSIS_GUARD),
    (model_router, MODEL_GUARD),
    (evaluation_router, MODEL_GUARD),
    (jobs_router, EXECUTION_GUARD),
    (workspace_router, PLATFORM_GUARD),
    #  Same guard as workspaces: choosing where work is filed is a platform
    #  concern, and creating a project creates a directory on the server.
    (project_router, PLATFORM_GUARD),
    (api_key_router, PLATFORM_GUARD),
    (execution_router, EXECUTION_GUARD),
    #  Invoking reads; submitting runs. Same module, different guard.
    (serving_router, SERVING_GUARD),
    #  Invoking a pipeline is the same act as invoking a model, so it answers
    #  to the same permission - not to the one that governs editing pipelines.
    (pipeline_serving_router, SERVING_GUARD),
    (results_router, RESULT_GUARD),
    #  Reading lineage is reading what produced a result, so it answers to the
    #  same permission as reading the result itself.
    (lineage_router, RESULT_GUARD),
    (report_router, RESULT_GUARD),
    (applications_router, APPLICATION_GUARD),
)

for module_router, guard in _GUARDED:
    api_router.include_router(module_router, dependencies=[Depends(guard)])

#  The one unguarded router. Mounted apart from the list above rather than with
#  a permissive guard, so that "which routes need no credential" is answerable
#  by reading one line instead of by checking every guard in the table.
api_router.include_router(public_router)

#  Whatever the plugins bring, mounted the same way as a module.
for contributed in contributed_routers():
    api_router.include_router(contributed.router, dependencies=[Depends(contributed.guard)])
