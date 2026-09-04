"""Application settings, read from the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    #  Only backend/.env is read here. The repository-root .env belongs to
    #  Docker Compose, whose values are container paths; letting both feed the
    #  same settings object meant a compose file could silently break a local
    #  run. Compose passes what the container needs as real environment
    #  variables, which still take priority over anything in this file.
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_prefix="FLUX_",
        extra="ignore",
    )

    # -- service -----------------------------------------------------------
    app_name: str = "flux-data-engine"
    api_prefix: str = "/api/v1"
    debug: bool = False
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3001",
            "http://localhost:5173",
            "http://127.0.0.1:3001",
        ]
    )

    # -- persistence -------------------------------------------------------
    database_url: str = "postgresql+psycopg://flux:flux@localhost:35432/flux"

    # -- storage -----------------------------------------------------------
    #  "local" writes under storage_root; "s3" talks to S3 or MinIO.
    storage_backend: str = "local"
    storage_root: Path = PROJECT_ROOT / "var" / "storage"
    data_root: Path = PROJECT_ROOT / "data"
    s3_bucket: str = "flux"
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    # -- execution ---------------------------------------------------------
    #  "inline" runs executions synchronously in the request; "queue" hands
    #  them to the background worker over Redis.
    execution_mode: str = "inline"
    redis_url: str = "redis://localhost:36379/0"
    queue_name: str = "flux:executions"
    #  How long a worker waits on the queue before looping (seconds).
    worker_poll_seconds: int = 5
    #  How long a RUNNING execution may go without its worker saying it is
    #  alive before another worker declares it lost. Longer than the slowest
    #  execution the deployment expects: reclaiming a healthy run is worse
    #  than leaving a dead one for another minute.
    execution_lease_seconds: int = 600
    #  How often a worker refreshes the heartbeat of what it is running.
    heartbeat_interval_seconds: int = 30
    #  How long one execution may run before it is asked to stop. Advisory:
    #  a plugin is a plain function call, so this is a deadline it can check
    #  rather than a signal that interrupts it. The lease above is the
    #  backstop for one that never looks.
    execution_timeout_seconds: int = 900

    #  "text" reads well at a terminal; "json" is what a log aggregator can
    #  index without parsing a line back into the fields it came from.
    log_format: str = "text"
    #  How many times a stranded execution is re-run before the platform gives
    #  up on it. Without a cap, an execution that never reaches a terminal
    #  state is retried by every recovery sweep, for ever.
    execution_max_attempts: int = 3
    #  How many steps of one pipeline may run at once. Steps that do not read
    #  from each other have no reason to wait for each other, and a wide
    #  pipeline spent most of its wall clock doing exactly that. 1 turns
    #  parallelism off, which is what a deployment on a database that dislikes
    #  concurrent writers should choose.
    pipeline_max_parallel_steps: int = 4
    #  How often the worker checks for due schedules (seconds).
    scheduler_interval_seconds: int = 30
    scheduler_enabled: bool = True

    # -- security ----------------------------------------------------------
    auth_enabled: bool = True
    #  Signing key for access tokens. MUST be overridden outside development.
    secret_key: str = "dev-only-insecure-key-change-me"
    access_token_minutes: int = 720
    #  The first-run administrator. The password is only used when the user
    #  does not already exist.
    bootstrap_admin_email: str = "admin@flux.local"
    bootstrap_admin_password: str = "flux-admin"

    #  Outbound connections (REST and database sources). Private addresses are
    #  refused by default: a data source is a URL a user supplies and the
    #  server fetches, so without this the platform is a proxy into its own
    #  network. A deployment whose data really is internal opts in here.
    outbound_allow_private: bool = False
    outbound_allowed_hosts: list[str] = Field(default_factory=list)

    # -- language models ---------------------------------------------------
    #  An OpenAI-compatible chat-completions endpoint. Empty is the default and
    #  is not a degraded mode: a provider that reasons over evidence must be
    #  able to answer without one, or the platform would depend on a network
    #  service to explain its own conclusions.
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 60
    #  Reasoning is grounded in evidence the platform assembled, so a long
    #  answer is a sign of invention rather than of thoroughness.
    llm_max_tokens: int = 1200

    # -- observability -----------------------------------------------------
    metrics_enabled: bool = True
    audit_enabled: bool = True

    # -- seeding -----------------------------------------------------------
    seed_on_startup: bool = True

    @property
    def network_policy(self):
        """Where outbound readers may connect."""
        from app.shared.outbound import NetworkPolicy

        return NetworkPolicy(
            allow_private=self.outbound_allow_private,
            allowed_hosts=tuple(self.outbound_allowed_hosts),
        )

    @property
    def uses_queue(self) -> bool:
        return self.execution_mode == "queue"

    @property
    def steps_may_run_in_parallel(self) -> bool:
        """Whether two steps of one pipeline may write at the same time.

        SQLite takes one writer at a time and makes the second one wait, so
        running steps in threads there buys nothing and risks "database is
        locked" on work that would otherwise have finished. The setting is
        still honoured wherever concurrent writers are - which is every
        deployment that runs on PostgreSQL.
        """
        return (
            self.pipeline_max_parallel_steps > 1
            and not self.database_url.startswith("sqlite")
        )

    @property
    def is_default_secret(self) -> bool:
        return self.secret_key == "dev-only-insecure-key-change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()
