"""Authentication and role-based authorisation.

The guards are wired by (module, HTTP method), so these tests check the wiring
once per role rather than once per endpoint.
"""

from __future__ import annotations

import pytest

from .conftest import ADMIN_EMAIL, ADMIN_PASSWORD


# --------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------
def test_login_returns_a_token_and_the_account(anonymous, api):
    response = anonymous.post(
        f"{api}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["role"] == "admin"
    assert "platform:admin" in body["user"]["permissions"]


def test_login_is_case_insensitive_on_the_email(anonymous, api):
    response = anonymous.post(
        f"{api}/auth/login",
        json={"email": ADMIN_EMAIL.upper(), "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [
        (ADMIN_EMAIL, "wrong-password"),
        ("nobody@test.local", ADMIN_PASSWORD),
    ],
)
def test_bad_credentials_are_refused_the_same_way(anonymous, api, email, password):
    response = anonymous.post(
        f"{api}/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 401
    #  The same message either way, so the endpoint does not reveal which
    #  addresses have accounts.
    assert response.json()["message"] == "incorrect email or password"


def test_endpoints_require_a_token(anonymous, api):
    response = anonymous.get(f"{api}/models")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


def test_a_forged_token_is_refused(anonymous, api, admin_token):
    head, payload, _ = admin_token.split(".")
    forged = f"{head}.{payload}.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    response = anonymous.get(f"{api}/models", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_me_reports_the_signed_in_account(client, api):
    body = client.get(f"{api}/auth/me").json()
    assert body["email"] == ADMIN_EMAIL
    assert body["role"] == "admin"


def test_auth_config_is_public(anonymous, api):
    body = anonymous.get(f"{api}/auth/config").json()
    assert body["auth_enabled"] is True
    assert set(body["roles"]) == {"admin", "editor", "viewer"}


# --------------------------------------------------------------------------
# authorisation
# --------------------------------------------------------------------------
def test_a_viewer_may_read(viewer_client, api):
    assert viewer_client.get(f"{api}/models").status_code == 200
    assert viewer_client.get(f"{api}/datasets").status_code == 200


def test_a_viewer_may_not_write(viewer_client, api):
    response = viewer_client.post(
        f"{api}/models",
        json={
            "name": "Viewer should not create this",
            "provider": "formula",
            "configuration": {"expressions": {"y": "1"}},
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "forbidden"
    assert body["details"]["required"] == "model:write"
    assert body["details"]["role"] == "viewer"


def test_a_viewer_may_not_run_executions(viewer_client, api):
    response = viewer_client.post(f"{api}/executions", json={"model_id": "whatever"})
    assert response.status_code == 403
    assert response.json()["details"]["required"] == "execution:run"


def test_an_editor_may_write_but_not_administer_users(editor_client, api):
    created = editor_client.post(
        f"{api}/models",
        json={
            "name": "Editor formula",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "x * 2"}},
        },
    )
    assert created.status_code == 201, created.text

    refused = editor_client.get(f"{api}/users")
    assert refused.status_code == 403
    assert refused.json()["details"]["required"] == "platform:admin"


def test_only_an_admin_creates_accounts(editor_client, api):
    response = editor_client.post(
        f"{api}/users", json={"email": "sneaky@test.local", "password": "password123"}
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------
# account management
# --------------------------------------------------------------------------
def test_admin_can_create_update_and_delete_an_account(client, api):
    created = client.post(
        f"{api}/users",
        json={
            "email": "temporary@test.local",
            "password": "temporary-password",
            "role": "viewer",
            "display_name": "Temp",
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()
    assert user["role"] == "viewer"
    assert user["display_name"] == "Temp"

    promoted = client.patch(f"{api}/users/{user['id']}", json={"role": "editor"}).json()
    assert promoted["role"] == "editor"
    assert "model:write" in promoted["permissions"]

    assert client.delete(f"{api}/users/{user['id']}").status_code == 204


def test_duplicate_accounts_are_refused(client, api):
    payload = {"email": "duplicate@test.local", "password": "password123"}
    assert client.post(f"{api}/users", json=payload).status_code == 201
    assert client.post(f"{api}/users", json=payload).status_code == 409


def test_short_passwords_are_refused(client, api):
    response = client.post(
        f"{api}/users", json={"email": "weak@test.local", "password": "short"}
    )
    assert response.status_code == 422


def test_the_last_administrator_cannot_be_deleted(client, api):
    admins = [u for u in client.get(f"{api}/users").json() if u["role"] == "admin"]
    assert len(admins) == 1, "this test assumes a single administrator"
    response = client.delete(f"{api}/users/{admins[0]['id']}")
    #  Refused because it is both the caller's own account and the last admin.
    assert response.status_code == 422


def test_password_change_requires_the_current_password(client, api):
    response = client.post(
        f"{api}/auth/password",
        json={"current_password": "not-the-password", "new_password": "brand-new-password"},
    )
    assert response.status_code == 401


def test_a_disabled_account_cannot_sign_in(client, anonymous, api):
    created = client.post(
        f"{api}/users", json={"email": "disabled@test.local", "password": "password123"}
    ).json()
    client.patch(f"{api}/users/{created['id']}", json={"is_active": False})

    response = anonymous.post(
        f"{api}/auth/login",
        json={"email": "disabled@test.local", "password": "password123"},
    )
    assert response.status_code == 401
    assert "disabled" in response.json()["message"]
