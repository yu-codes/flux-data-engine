"""An application is either offered or it is not.

There used to be a Deployment beside the Application, carrying its own status,
environment and endpoint. Nothing was ever stood up - no process started, no
address bound - so a "running deployment" and a "published application" were
two names for one fact, and they could disagree. The lifecycle now lives on the
application: draft -> published -> draft.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def application(client, api) -> dict:
    created = client.post(
        f"{api}/applications",
        json={
            "name": "Lifecycle demo",
            "description": "An application used to exercise publishing.",
            "entrypoint": "/apps/lifecycle-demo",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture(scope="module")
def dashboard(client, api) -> str:
    """A dashboard with something drawable on it, to bundle into applications."""
    source = client.post(
        f"{api}/sources",
        json={
            "name": "application rows",
            "type": "inline",
            "connection": {
                "rows": [{"city": "Taipei", "n": 3}, {"city": "Tainan", "n": 5}]
            },
        },
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Application data", "source_id": source.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text
    visualization = client.post(
        f"{api}/visualizations",
        json={
            "name": "Application chart",
            "dataset_id": dataset.json()["id"],
            "spec": {"chart_type": "bar", "x": "city", "y": ["n"], "aggregation": "sum"},
        },
    )
    assert visualization.status_code == 201, visualization.text
    created = client.post(
        f"{api}/dashboards",
        json={
            "name": "Application board",
            "tiles": [{"visualization_id": visualization.json()["id"], "width": 6}],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_a_new_application_starts_as_a_draft(application):
    assert application["status"] == "draft"
    assert application["slug"] == "lifecycle-demo"


def test_publishing_and_unpublishing_move_one_status(client, api, application):
    published = client.post(f"{api}/applications/{application['id']}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"

    withdrawn = client.post(f"{api}/applications/{application['id']}/unpublish")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "draft"

    #  The application itself survives being withdrawn: what it bundles is
    #  untouched, only whether it is offered has changed.
    detail = client.get(f"{api}/applications/{application['id']}").json()
    assert detail["entrypoint"] == "/apps/lifecycle-demo"


def test_publishing_something_with_nothing_in_it_is_refused(client, api):
    created = client.post(
        f"{api}/applications",
        json={"name": "Nowhere to go", "description": "Nothing in it."},
    )
    assert created.status_code == 201, created.text

    response = client.post(f"{api}/applications/{created.json()['id']}/publish")
    #  Publishing something unreachable would put it in a list of things people
    #  can open, where opening it does nothing.
    assert response.status_code == 422, response.text
    assert "nothing to open" in response.text


def test_an_application_with_a_dashboard_publishes_without_an_entrypoint(
    client, api, dashboard
):
    """The rule is "must not be unreachable", not "must name a route".

    Demanding an entrypoint made every composed application borrow a route
    somebody had written by hand, which is why the only publishable kind was
    the one compiled into the frontend.
    """
    created = client.post(
        f"{api}/applications",
        json={"name": "Opens on its own page", "dashboard_ids": [dashboard]},
    )
    assert created.status_code == 201, created.text

    published = client.post(f"{api}/applications/{created.json()['id']}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"


def test_a_built_in_application_still_needs_its_route(client, api):
    """A built-in application *is* a page, so it has to say which one."""
    created = client.post(
        f"{api}/applications",
        json={"name": "Built in, nowhere", "kind": "builtin"},
    )
    assert created.status_code == 201, created.text

    response = client.post(f"{api}/applications/{created.json()['id']}/publish")
    assert response.status_code == 422, response.text
    assert "entrypoint" in response.text


def test_an_application_can_be_opened(client, api, dashboard):
    """A composed application had no page of its own until it had this one."""
    created = client.post(
        f"{api}/applications",
        json={
            "name": "Openable",
            "description": "Has something to show.",
            "dashboard_ids": [dashboard],
        },
    )
    assert created.status_code == 201, created.text

    view = client.get(f"{api}/applications/{created.json()['id']}/view")
    assert view.status_code == 200, view.text
    body = view.json()

    assert body["name"] == "Openable"
    assert body["built_from"]["dashboards"] == 1
    assert len(body["dashboards"]) == 1
    assert body["dashboards"][0]["tiles"], "the dashboard came back with no tiles"


def test_deployments_are_gone(client, api, application):
    """The second lifecycle is not merely hidden from the UI - it is removed."""
    for path in (
        f"{api}/deployments",
        f"{api}/applications/{application['id']}/deployments",
    ):
        assert client.get(path).status_code == 404, path


# --------------------------------------------------------------------------
# an application you can use, not only read
# --------------------------------------------------------------------------
def test_an_application_offers_its_models_as_tools(client, api, dashboard):
    """`model_ids` was a list nothing rendered.

    The platform's whole proposition is "give it input, it runs a model, you
    get an answer", and that was available to whoever built the model and to
    nobody else. An application that bundles models should hand them to the
    person who opens it - the contracts needed to build the form travel with
    the model already.
    """
    model = client.post(
        f"{api}/models",
        json={
            "name": "Application tool",
            "provider": "formula",
            "configuration": {"expressions": {"revenue": "price * quantity"}},
        },
    )
    assert model.status_code == 201, model.text

    created = client.post(
        f"{api}/applications",
        json={
            "name": "Usable application",
            "model_ids": [model.json()["id"]],
            "dashboard_ids": [dashboard],
        },
    )
    assert created.status_code == 201, created.text

    view = client.get(f"{api}/applications/{created.json()['id']}/view")
    assert view.status_code == 200, view.text
    tools = view.json()["tools"]

    assert len(tools) == 1
    tool = tools[0]
    assert tool["model_id"] == model.json()["id"]
    assert tool["name"] == "Application tool"
    #  Enough to build a form from, without asking the registry.
    assert "parameter_contract" in tool and "input_contract" in tool
    assert "calculation" in tool["kinds"]


def test_a_tool_that_lost_its_model_does_not_break_the_page(client, api):
    """A deleted model should cost that tool, not the application."""
    model = client.post(
        f"{api}/models",
        json={
            "name": "Doomed tool",
            "provider": "formula",
            "configuration": {"expressions": {"x": "1"}},
        },
    )
    created = client.post(
        f"{api}/applications",
        json={"name": "Outlives its model", "model_ids": [model.json()["id"]],
              "entrypoint": "/dashboards"},
    )
    assert created.status_code == 201, created.text
    assert client.delete(f"{api}/models/{model.json()['id']}").status_code in (204, 200)

    view = client.get(f"{api}/applications/{created.json()['id']}/view")
    assert view.status_code == 200, view.text
    assert view.json()["tools"] == []


def test_a_shared_link_does_not_hand_out_the_tools(client, api, dashboard):
    """A link holder gets to read, not to spend the platform's compute.

    Running a model from a tokenless page would turn a share link into an
    unauthenticated compute endpoint, which is a different thing from the
    read-only capability the link is documented to be.
    """
    model = client.post(
        f"{api}/models",
        json={"name": "Not for strangers", "provider": "formula",
              "configuration": {"expressions": {"x": "1"}}},
    )
    created = client.post(
        f"{api}/applications",
        json={
            "name": "Shared but not runnable",
            "model_ids": [model.json()["id"]],
            "dashboard_ids": [dashboard],
        },
    )
    published = client.post(f"{api}/applications/{created.json()['id']}/publish")
    assert published.status_code == 200, published.text
    shared = client.post(f"{api}/applications/{created.json()['id']}/share")
    assert shared.status_code == 200, shared.text
    token = shared.json()["token"]

    public = client.get(f"{api}/public/applications/{token}")
    assert public.status_code == 200, public.text
    assert public.json()["tools"] == []


def test_a_tool_is_offered_the_applications_datasets_not_the_platforms(
    client, api, dashboard
):
    """An application bundles datasets on purpose.

    The page loaded every dataset in the workspace, which makes the bundling
    meaningless: a tool inside an application about sales should not offer to
    run against somebody else's typhoon catalogue.
    """
    source = client.post(
        f"{api}/sources",
        json={
            "name": "bundled rows",
            "type": "inline",
            "connection": {"rows": [{"price": 2, "quantity": 2}]},
        },
    )
    bundled = client.post(
        f"{api}/datasets",
        json={"name": "Bundled data", "source_id": source.json()["id"]},
    )
    assert bundled.status_code == 201, bundled.text

    #  A second dataset the application does not bundle.
    other_source = client.post(
        f"{api}/sources",
        json={
            "name": "unbundled rows",
            "type": "inline",
            "connection": {"rows": [{"price": 9, "quantity": 9}]},
        },
    )
    unbundled = client.post(
        f"{api}/datasets",
        json={"name": "Unbundled data", "source_id": other_source.json()["id"]},
    )
    assert unbundled.status_code == 201, unbundled.text

    model = client.post(
        f"{api}/models",
        json={"name": "Bundled tool", "provider": "formula",
              "configuration": {"expressions": {"revenue": "price * quantity"}}},
    )
    created = client.post(
        f"{api}/applications",
        json={
            "name": "Bundles one dataset",
            "model_ids": [model.json()["id"]],
            "dataset_ids": [bundled.json()["id"]],
            "dashboard_ids": [dashboard],
        },
    )
    assert created.status_code == 201, created.text

    view = client.get(f"{api}/applications/{created.json()['id']}/view").json()
    offered = {d["name"] for d in view["datasets"]}

    assert offered == {"Bundled data"}
    assert "Unbundled data" not in offered
