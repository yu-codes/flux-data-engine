"""Test fixtures: an isolated database, storage root and signed-in clients."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TMP_ROOT = Path(tempfile.mkdtemp(prefix="flux-tests-"))
os.environ.setdefault("FLUX_DATABASE_URL", f"sqlite+pysqlite:///{TMP_ROOT / 'test.db'}")
os.environ.setdefault("FLUX_STORAGE_ROOT", str(TMP_ROOT / "storage"))
os.environ.setdefault("FLUX_SEED_ON_STARTUP", "false")
#  Auth is exercised for real: the suite signs in like any other client.
os.environ.setdefault("FLUX_AUTH_ENABLED", "true")
os.environ.setdefault("FLUX_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("FLUX_BOOTSTRAP_ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("FLUX_BOOTSTRAP_ADMIN_PASSWORD", "test-admin-password")
#  The worker is not running under test, so executions must run in-process.
os.environ.setdefault("FLUX_EXECUTION_MODE", "inline")
os.environ.setdefault("FLUX_SCHEDULER_ENABLED", "false")

ADMIN_EMAIL = os.environ["FLUX_BOOTSTRAP_ADMIN_EMAIL"]
ADMIN_PASSWORD = os.environ["FLUX_BOOTSTRAP_ADMIN_PASSWORD"]


@pytest.fixture(scope="session")
def app():
    from app.core.database import Base, engine, import_all_orm_models
    from app.main import create_app
    from app.plugins.bootstrap import register_builtin_plugins

    import_all_orm_models()
    Base.metadata.create_all(engine)
    register_builtin_plugins()
    return create_app()


@pytest.fixture(scope="session")
def api() -> str:
    from app.core.config import get_settings

    return get_settings().api_prefix


@pytest.fixture(scope="session")
def anonymous(app):
    """A client with no credentials, for testing the guards themselves."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def admin_token(anonymous, api) -> str:
    response = anonymous.post(
        f"{api}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="session")
def client(app, admin_token):
    """The default client: signed in as the bootstrap administrator."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        test_client.headers["Authorization"] = f"Bearer {admin_token}"
        yield test_client


def _client_for(app, api, anonymous, email: str, password: str, role: str):
    """Create an account with the given role and return a client signed in as it."""
    from fastapi.testclient import TestClient

    existing = anonymous.post(
        f"{api}/auth/login", json={"email": email, "password": password}
    )
    if existing.status_code != 200:
        raise AssertionError(f"could not sign in as {email}: {existing.text}")
    token = existing.json()["access_token"]
    test_client = TestClient(app)
    test_client.headers["Authorization"] = f"Bearer {token}"
    return test_client


@pytest.fixture(scope="session")
def viewer_client(app, api, client, anonymous):
    """A read-only account, for checking that write permissions are enforced."""
    email, password = "viewer@test.local", "viewer-password"
    created = client.post(
        f"{api}/users",
        json={"email": email, "password": password, "role": "viewer"},
    )
    assert created.status_code in (201, 409), created.text
    return _client_for(app, api, anonymous, email, password, "viewer")


@pytest.fixture(scope="session")
def editor_client(app, api, client, anonymous):
    """An account that can build and run but not administer users."""
    email, password = "editor@test.local", "editor-password"
    created = client.post(
        f"{api}/users",
        json={"email": email, "password": password, "role": "editor"},
    )
    assert created.status_code in (201, 409), created.text
    return _client_for(app, api, anonymous, email, password, "editor")
