"""An application somebody outside can open.

This is the one unauthenticated surface in the platform, so it is worth testing
suspiciously. A share link is a capability - holding the URL is the permission -
which means the questions that matter are what it grants, what it refuses, and
what happens when it is taken away.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def application(client, api) -> dict:
    """A published application with a dashboard in it."""
    source = client.post(
        f"{api}/sources",
        json={
            "name": "sharing rows",
            "type": "inline",
            "connection": {
                "rows": [{"city": "Taipei", "n": 3}, {"city": "Tainan", "n": 5}]
            },
        },
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Sharing data", "source_id": source.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text

    visualization = client.post(
        f"{api}/visualizations",
        json={
            "name": "Sharing chart",
            "dataset_id": dataset.json()["id"],
            "spec": {"chart_type": "bar", "x": "city", "y": ["n"], "aggregation": "sum"},
        },
    )
    assert visualization.status_code == 201, visualization.text

    dashboard = client.post(f"{api}/dashboards", json={"name": "Sharing dashboard"})
    assert dashboard.status_code == 201, dashboard.text
    added = client.post(
        f"{api}/dashboards/{dashboard.json()['id']}/tiles",
        json={"visualization_id": visualization.json()["id"]},
    )
    assert added.status_code in (200, 201), added.text

    created = client.post(
        f"{api}/applications",
        json={
            "name": "Shared analytics",
            "description": "Something to hand to somebody outside.",
            "entrypoint": "/dashboards",
            "dashboard_ids": [dashboard.json()["id"]],
            "dataset_ids": [dataset.json()["id"]],
        },
    )
    assert created.status_code == 201, created.text
    published = client.post(f"{api}/applications/{created.json()['id']}/publish")
    assert published.status_code == 200, published.text
    return published.json()


# --------------------------------------------------------------------------
# what a link grants
# --------------------------------------------------------------------------
def test_a_published_application_can_be_shared(client, api, application):
    response = client.post(f"{api}/applications/{application['id']}/share")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["token"]
    assert body["share_url"].endswith(body["token"])
    assert body["visibility"] == "link"
    #  Long enough that guessing is not a strategy.
    assert len(body["token"]) >= 32


def test_the_link_opens_without_any_credential(anonymous, client, api, application):
    token = client.post(f"{api}/applications/{application['id']}/share").json()["token"]

    response = anonymous.get(f"{api}/public/applications/{token}")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["name"] == "Shared analytics"
    assert body["built_from"]["dashboards"] == 1
    #  The dashboard is rendered, not referenced: a reader with only a link has
    #  no way to fetch anything by id.
    assert body["dashboards"], "no dashboard was rendered"
    assert body["dashboards"][0]["tiles"]


def test_sharing_twice_keeps_the_same_link(client, api, application):
    """A link already sent to somebody must not stop working."""
    first = client.post(f"{api}/applications/{application['id']}/share").json()["token"]
    second = client.post(f"{api}/applications/{application['id']}/share").json()["token"]
    assert first == second


# --------------------------------------------------------------------------
# what it refuses
# --------------------------------------------------------------------------
def test_a_draft_cannot_be_shared(client, api):
    draft = client.post(
        f"{api}/applications",
        json={"name": "Unfinished thing", "entrypoint": "/dashboards"},
    )
    assert draft.status_code == 201, draft.text

    response = client.post(f"{api}/applications/{draft.json()['id']}/share")
    assert response.status_code == 422, response.text
    assert "publish" in response.text


def test_an_invalid_link_looks_the_same_as_one_that_never_existed(anonymous, api):
    """A probe should learn nothing about what exists."""
    response = anonymous.get(f"{api}/public/applications/not-a-real-token")
    assert response.status_code == 404, response.text


def test_the_public_route_offers_nothing_but_reading(anonymous, client, api, application):
    token = client.post(f"{api}/applications/{application['id']}/share").json()["token"]
    #  No verb but GET, and no id the caller can substitute.
    assert anonymous.post(f"{api}/public/applications/{token}").status_code == 405
    assert anonymous.delete(f"{api}/public/applications/{token}").status_code == 405


def test_a_link_does_not_open_the_rest_of_the_platform(anonymous, client, api, application):
    """Holding a share link is not holding an account."""
    client.post(f"{api}/applications/{application['id']}/share")
    for path in ("/models", "/datasets", "/applications", "/executions"):
        assert anonymous.get(f"{api}{path}").status_code == 401, path


# --------------------------------------------------------------------------
# taking it away
# --------------------------------------------------------------------------
def test_revoking_kills_the_old_link(anonymous, client, api, application):
    """Shares again first, because an earlier test may have revoked it."""
    token = client.post(f"{api}/applications/{application['id']}/share").json()["token"]
    assert anonymous.get(f"{api}/public/applications/{token}").status_code == 200

    revoked = client.delete(f"{api}/applications/{application['id']}/share")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["is_shared"] is False

    #  Dead, not merely unadvertised.
    assert anonymous.get(f"{api}/public/applications/{token}").status_code == 404


def test_unpublishing_also_revokes_the_link(anonymous, client, api, application):
    """Otherwise withdrawing it from everybody inside would leave it open outside."""
    token = client.post(f"{api}/applications/{application['id']}/share").json()["token"]
    assert anonymous.get(f"{api}/public/applications/{token}").status_code == 200

    client.post(f"{api}/applications/{application['id']}/unpublish")
    assert anonymous.get(f"{api}/public/applications/{token}").status_code == 404


def test_sharing_state_is_visible_to_the_owner(client, api, application):
    #  Republished first: an earlier test withdraws it, and a test that only
    #  passes after its neighbours is not testing what it says it is.
    client.post(f"{api}/applications/{application['id']}/publish")
    client.post(f"{api}/applications/{application['id']}/share")
    listed = client.get(f"{api}/applications").json()
    mine = next(a for a in listed if a["id"] == application["id"])
    assert mine["is_shared"] is True
    assert mine["visibility"] == "link"
    #  The token itself is not in the listing: it is a credential, and a
    #  listing is not where credentials belong.
    assert "share_token" not in mine
