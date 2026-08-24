"""A workspace is a boundary, not a label.

Three things have to be true or the feature is decorative: two workspaces can
hold resources with the same name, neither can see the other's, and holding an
id from one does not let you read it from the other. The third is the one that
is easy to get wrong - filtering a list is obvious, refusing a direct lookup is
not - so it is checked for every resource kind rather than one.
"""

from __future__ import annotations

import pytest

INLINE_ROWS = [{"city": "Taipei", "n": 1}, {"city": "Tainan", "n": 2}]


@pytest.fixture(scope="module")
def other(client, api) -> dict:
    created = client.post(
        f"{api}/workspaces",
        json={"name": "Second workspace", "description": "For isolation tests."},
    )
    assert created.status_code == 201, created.text
    return created.json()


def in_workspace(client, workspace_id: str):
    """Ask as though acting inside a particular workspace."""
    return {"X-Workspace": workspace_id}


# --------------------------------------------------------------------------
# the default
# --------------------------------------------------------------------------
def test_an_installation_has_a_default_workspace(client, api):
    listed = client.get(f"{api}/workspaces")
    assert listed.status_code == 200, listed.text
    defaults = [w for w in listed.json() if w["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["name"] == "Default workspace"


def test_the_default_workspace_cannot_be_deleted(client, api):
    """Something has to own the resources that name no workspace."""
    default = next(w for w in client.get(f"{api}/workspaces").json() if w["is_default"])
    response = client.delete(f"{api}/workspaces/{default['id']}")
    assert response.status_code == 422, response.text
    assert "cannot be deleted" in response.text


def test_a_request_that_names_no_workspace_lands_in_the_default(client, api):
    """Every existing client keeps working without knowing workspaces exist."""
    created = client.post(
        f"{api}/sources",
        json={
            "name": "unspecified workspace source",
            "type": "inline",
            "connection": {"rows": INLINE_ROWS},
        },
    )
    assert created.status_code == 201, created.text
    default = next(w for w in client.get(f"{api}/workspaces").json() if w["is_default"])
    listed = client.get(f"{api}/sources", headers=in_workspace(client, default["id"]))
    assert created.json()["id"] in {s["id"] for s in listed.json()}


# --------------------------------------------------------------------------
# the same name, twice
# --------------------------------------------------------------------------
def test_two_workspaces_can_hold_a_model_with_the_same_name(client, api, other):
    """The headline: a name is unique inside a workspace, not across one."""
    body = {
        "name": "Revenue",
        "provider": "formula",
        "configuration": {"expressions": {"revenue": "price * quantity"}},
    }
    first = client.post(f"{api}/models", json=body)
    assert first.status_code == 201, first.text

    #  The same name again in the same workspace is still a conflict.
    again = client.post(f"{api}/models", json=body)
    assert again.status_code == 409, again.text

    #  In another workspace it is simply another model.
    second = client.post(
        f"{api}/models", json=body, headers=in_workspace(client, other["id"])
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]


def test_two_workspaces_can_hold_a_dataset_with_the_same_name(client, api, other):
    for workspace in (None, other["id"]):
        headers = in_workspace(client, workspace) if workspace else {}
        source = client.post(
            f"{api}/sources",
            json={
                "name": "same name rows",
                "type": "inline",
                "connection": {"rows": INLINE_ROWS},
            },
            headers=headers,
        )
        assert source.status_code == 201, source.text
        dataset = client.post(
            f"{api}/datasets",
            json={"name": "Shared name", "source_id": source.json()["id"]},
            headers=headers,
        )
        assert dataset.status_code == 201, dataset.text


# --------------------------------------------------------------------------
# isolation
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def in_other(client, api, other) -> dict:
    """One resource of each kind, created inside the second workspace."""
    headers = in_workspace(client, other["id"])
    made: dict[str, str] = {}

    source = client.post(
        f"{api}/sources",
        json={
            "name": "second workspace rows",
            "type": "inline",
            "connection": {"rows": INLINE_ROWS},
        },
        headers=headers,
    )
    assert source.status_code == 201, source.text
    made["sources"] = source.json()["id"]

    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Second workspace data", "source_id": made["sources"]},
        headers=headers,
    )
    assert dataset.status_code == 201, dataset.text
    made["datasets"] = dataset.json()["id"]

    model = client.post(
        f"{api}/models",
        json={
            "name": "Second workspace model",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "n * 2"}},
        },
        headers=headers,
    )
    assert model.status_code == 201, model.text
    made["models"] = model.json()["id"]

    execution = client.post(
        f"{api}/executions",
        json={"model_id": made["models"], "kind": "calculation", "input": {"n": 2}},
        headers=headers,
    )
    assert execution.status_code == 201, execution.text
    made["executions"] = execution.json()["id"]

    experiment = client.post(
        f"{api}/experiments",
        json={
            "name": "Second workspace experiment",
            "trials": [{"model_id": made["models"], "label": "only"}],
        },
        headers=headers,
    )
    assert experiment.status_code == 201, experiment.text
    made["experiments"] = experiment.json()["id"]

    return made


@pytest.mark.parametrize(
    "collection", ["sources", "datasets", "models", "executions", "experiments"]
)
def test_one_workspace_does_not_list_anothers_resources(client, api, in_other, collection):
    default = next(w for w in client.get(f"{api}/workspaces").json() if w["is_default"])
    listed = client.get(
        f"{api}/{collection}", headers=in_workspace(client, default["id"])
    )
    assert listed.status_code == 200, listed.text
    assert in_other[collection] not in {item["id"] for item in listed.json()}


@pytest.mark.parametrize(
    "collection", ["sources", "datasets", "models", "executions", "experiments"]
)
def test_holding_an_id_from_another_workspace_is_not_enough(
    client, api, in_other, collection
):
    """The part that is easy to miss.

    Filtering a list is the obvious half. Refusing a direct lookup is the half
    that decides whether a workspace is a boundary or a display preference.
    """
    default = next(w for w in client.get(f"{api}/workspaces").json() if w["is_default"])
    response = client.get(
        f"{api}/{collection}/{in_other[collection]}",
        headers=in_workspace(client, default["id"]),
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(
    "collection", ["sources", "datasets", "models", "executions", "experiments"]
)
def test_the_owning_workspace_can_read_its_own(client, api, other, in_other, collection):
    response = client.get(
        f"{api}/{collection}/{in_other[collection]}",
        headers=in_workspace(client, other["id"]),
    )
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------
# ownership
# --------------------------------------------------------------------------
def test_a_resource_records_who_created_it(client, api):
    """"Who changed this" stops being a question only the audit log can answer."""
    created = client.post(
        f"{api}/models",
        json={
            "name": "Ownership model",
            "provider": "formula",
            "configuration": {"expressions": {"x": "1"}},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["created_by"], "no creator recorded"

    me = client.get(f"{api}/auth/me").json()
    assert created.json()["created_by"] == me["id"]


# --------------------------------------------------------------------------
# membership
# --------------------------------------------------------------------------
def test_someone_who_is_not_a_member_is_refused(editor_client, api, other):
    """Naming a workspace you are not in fails rather than quietly showing you
    the default one - being shown the wrong workspace is worse than a refusal."""
    response = editor_client.get(
        f"{api}/models", headers={"X-Workspace": other["id"]}
    )
    assert response.status_code == 403, response.text


def test_adding_someone_lets_them_in(client, editor_client, api, other):
    me = editor_client.get(f"{api}/auth/me").json()
    added = client.post(
        f"{api}/workspaces/{other['id']}/members",
        json={"user_id": me["id"], "role": "editor"},
    )
    assert added.status_code == 201, added.text

    allowed = editor_client.get(f"{api}/models", headers={"X-Workspace": other["id"]})
    assert allowed.status_code == 200, allowed.text

    #  And removing them shuts the door again.
    removed = client.delete(f"{api}/workspaces/{other['id']}/members/{me['id']}")
    assert removed.status_code == 204, removed.text
    assert editor_client.get(
        f"{api}/models", headers={"X-Workspace": other["id"]}
    ).status_code == 403


def test_an_administrator_can_reach_every_workspace(client, api, other):
    """Somebody has to be able to find a workspace whose members have all left."""
    response = client.get(f"{api}/models", headers=in_workspace(client, other["id"]))
    assert response.status_code == 200, response.text


def test_naming_a_workspace_that_does_not_exist_is_a_404(client, api):
    response = client.get(f"{api}/models", headers={"X-Workspace": "ws_nope"})
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------
# lineage crosses modules, so it must not cross workspaces
# --------------------------------------------------------------------------
def test_a_lineage_walk_stays_inside_its_workspace(client, api, other, in_other):
    """The graph reads six modules at once, which is six chances to leak.

    Every other isolation test covers one collection's own endpoints. This one
    covers a reader that goes through all of them at once, because a walk that
    followed an id across the boundary would hand somebody a picture of another
    team's platform without ever calling that team's endpoints.
    """
    #  Asking about somebody else's dataset by id is a 404, not a graph.
    denied = client.get(f"{api}/lineage/dataset/{in_other['datasets']}")
    assert denied.status_code == 404, denied.text

    #  And from inside the owning workspace it answers normally.
    allowed = client.get(
        f"{api}/lineage/dataset/{in_other['datasets']}",
        headers=in_workspace(client, other["id"]),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["nodes"]


def test_a_walk_never_names_a_resource_from_another_workspace(
    client, api, other, in_other
):
    """Nothing from the other side may appear, even as a stub."""
    mine = client.post(
        f"{api}/sources",
        json={
            "name": "isolation probe rows",
            "type": "inline",
            "connection": {"rows": INLINE_ROWS},
        },
    )
    assert mine.status_code == 201, mine.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Isolation probe data", "source_id": mine.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text

    graph = client.get(
        f"{api}/lineage/dataset/{dataset.json()['id']}", params={"direction": "down"}
    ).json()

    ids = {node["id"] for node in graph["nodes"]}
    assert in_other["datasets"] not in ids
    assert in_other["sources"] not in ids
