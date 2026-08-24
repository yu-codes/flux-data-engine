"""flux-data-engine API.

A general-purpose Data, Model and Execution platform:

    Data -> Model -> Execution -> Result -> Application

A Model here is any versioned, describable, executable computational unit -
formula, rule, statistical, simulation, optimisation or machine learning.
Machine learning is one provider, not the architecture.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.console import configure_streams
from app.core.container import build_services
from app.core.database import import_all_orm_models, session_scope
from app.core.errors import register_exception_handlers
from app.core.observability import (
    JsonFormatter,
    ObservabilityMiddleware,
    RequestIdFilter,
)
from app.plugins.bootstrap import register_builtin_plugins

configure_streams()
logging.basicConfig(
    level=logging.INFO,
    #  The request id is in every line, not only in the two the middleware
    #  writes. A correlation id that cannot be grepped for correlates nothing.
    format="%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestIdFilter())
    if get_settings().log_format == "json":
        _handler.setFormatter(JsonFormatter())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    import_all_orm_models()
    register_builtin_plugins()
    settings.storage_root.mkdir(parents=True, exist_ok=True)

    if settings.auth_enabled:
        if settings.is_default_secret:
            logger.warning(
                "FLUX_SECRET_KEY is still the development default; "
                "set a real one before exposing this deployment"
            )
        try:
            with session_scope() as session:
                build_services(session).auth.ensure_bootstrap_admin()
        except Exception:
            logger.exception("could not create the bootstrap administrator")

    if settings.seed_on_startup:
        try:
            from app.core.seed import seed_all

            with session_scope() as session:
                seed_all(session)
        except Exception:  # seeding must never block the API from starting
            logger.exception("seeding failed; continuing without seed data")

    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="flux-data-engine",
        description=__doc__,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        ObservabilityMiddleware, enabled=settings.metrics_enabled
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
