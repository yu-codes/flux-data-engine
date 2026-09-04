"""A project files work; it does not fence it off.

The distinction this file pins is the one the whole feature turns on:

* **The workspace is a boundary.** A row from another workspace cannot be read
  even with its id in hand. That is tested elsewhere and is not weakened here.
* **The project is a filing system.** Listing filters by it. A lookup by id
  does not refuse, because a report cites a dataset, lineage walks into one and
  an application bundles several — and breaking those would buy no safety.

Also pinned: everything a run leaves behind is filed where the run was. A
result dataset that comes back unfiled would show up under every project, which
is the failure mode that makes the filing worthless the moment the platform is
busy.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.core.config import get_settings

ROWS = [
    {"asset": "P-01", "reading": "12.5", "state": "running"},
    {"asset": "P-02", "reading": "18.0", "state": "running"},
    {"asset": "P-03", "reading": "4.5", "state": "idle"},
]


def _headers(project_id: str | None) -> dict[str, str]:
    return {"X-Project": project_id} if project_id else {}


@pytest.fixture(scope="module")
def alpha(client, api) -> dict:
    created = client.post(
        f"{api}/projects",
        json={"name": "Scope alpha", "description": "one piece of work"},
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture(scope="module")
def beta(client, api) -> dict:
    created = client.post(
        f"{api}/projects",
        json={"name": "Scope beta", "description": "a different piece of work"},
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_the_default_project_exists_and_is_named(client, api):
    """A fresh install has somewhere to put things without being asked."""
    listed = client.get(f"{api}/projects")
    assert listed.status_code == 200, listed.text
    defaults = [row for row in listed.json() if row["is_default"]]
    assert len(defaults) == 1, "exactly one default, or the switcher has no home"
    assert defaults[0]["name"] == "Demo"


def test_a_project_gets_a_directory_under_the_data_root(alpha):
    """Source files land somewhere predictable, and the API says where."""
    assert alpha["directory"] == "Scope-alpha", "a name with a space, a directory without"
    assert alpha["sources_path"].endswith("Scope-alpha/sources")
    assert alpha["uploads_path"].endswith("Scope-alpha/uploads")
    assert (Path(get_settings().data_root) / alpha["directory"] / "sources").is_dir()


def test_a_name_cannot_be_used_twice(client, api, alpha):
    again = client.post(f"{api}/projects", json={"name": alpha["name"]})
    assert again.status_code == 409, again.text


@pytest.fixture(scope="module")
def filed(client, api, alpha) -> dict[str, str]:
    """A source and a dataset created while standing in `alpha`."""
    relative = f"{alpha['directory']}/sources/readings.csv"
    path = Path(get_settings().data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)

    headers = _headers(alpha["id"])
    source = client.post(
        f"{api}/sources",
        json={"name": "alpha readings", "type": "csv", "connection": {"path": relative}},
        headers=headers,
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Alpha readings", "source_id": source.json()["id"]},
        headers=headers,
    )
    assert dataset.status_code == 201, dataset.text
    return {"source_id": source.json()["id"], "dataset_id": dataset.json()["id"]}


def test_what_is_created_in_a_project_is_filed_there(client, api, alpha, filed):
    here = _headers(alpha["id"])
    source = client.get(f"{api}/sources/{filed['source_id']}", headers=here)
    assert source.json()["project_id"] == alpha["id"]
    dataset = client.get(f"{api}/datasets/{filed['dataset_id']}", headers=here)
    assert dataset.json()["project_id"] == alpha["id"]


def test_another_project_does_not_list_it(client, api, beta, filed):
    """The whole point: switching project changes what a page shows."""
    listed = client.get(f"{api}/datasets", headers=_headers(beta["id"]))
    assert listed.status_code == 200, listed.text
    assert filed["dataset_id"] not in {row["id"] for row in listed.json()}

    sources = client.get(f"{api}/sources", headers=_headers(beta["id"]))
    assert filed["source_id"] not in {row["id"] for row in sources.json()}


def test_the_project_it_belongs_to_does_list_it(client, api, alpha, filed):
    listed = client.get(f"{api}/datasets", headers=_headers(alpha["id"]))
    assert filed["dataset_id"] in {row["id"] for row in listed.json()}


def test_a_lookup_by_id_is_not_refused_from_another_project(client, api, beta, filed):
    """A filing system, not a boundary. Reports and lineage depend on this."""
    elsewhere = _headers(beta["id"])
    fetched = client.get(f"{api}/datasets/{filed['dataset_id']}", headers=elsewhere)
    assert fetched.status_code == 200, fetched.text


def test_an_unknown_project_header_is_ignored_rather_than_refused(client, api, filed):
    """A stale id in somebody's browser must not 400 every page they open."""
    listed = client.get(f"{api}/datasets", headers={"X-Project": "no-such-project"})
    assert listed.status_code == 200, listed.text


def test_an_unfiled_resource_shows_under_every_project(client, api, alpha, beta):
    """`project_id IS NULL` means "shared", not "hidden".

    A model definition written to be reused belongs to no one project, and the
    library must still offer it wherever you are standing.
    """
    created = client.post(
        f"{api}/models",
        json={
            "name": "Shared doubling formula",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "1 * 2"}},
        },
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    #  Created with no project header, so it is filed nowhere.
    assert created.json()["project_id"] is None

    for project in (alpha, beta):
        listed = client.get(f"{api}/models", headers=_headers(project["id"]))
        assert model_id in {row["id"] for row in listed.json()}, project["name"]


def test_a_run_files_everything_it_leaves_behind(client, api, alpha, filed):
    """An execution, its result, and the dataset the result became.

    All three belong to the project the input dataset belongs to — not to
    wherever the caller happened to be standing, and certainly not nowhere.
    """
    headers = _headers(alpha["id"])
    model = client.post(
        f"{api}/models",
        json={
            "name": "Alpha reading doubler",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "reading * 2"}},
        },
        headers=headers,
    )
    assert model.status_code == 201, model.text

    run = client.post(
        f"{api}/executions",
        json={"model_id": model.json()["id"], "dataset_id": filed["dataset_id"]},
        headers=headers,
    )
    assert run.status_code in (200, 201), run.text
    execution = run.json()
    assert execution["status"] == "succeeded", execution.get("error")
    assert execution["project_id"] == alpha["id"]

    #  The execution names its own result; `/results` has no execution filter,
    #  and asking for the whole list would sweep in every other test's.
    assert execution["result_id"], "the run recorded no result"
    result = client.get(f"{api}/results/{execution['result_id']}", headers=headers).json()
    assert result["project_id"] == alpha["id"], result
    assert result["dataset_id"], "a table result should have become a dataset"
    dataset = client.get(f"{api}/datasets/{result['dataset_id']}", headers=headers)
    assert dataset.json()["project_id"] == alpha["id"], dataset.json()


def test_a_pipeline_run_files_its_output_dataset(client, api, alpha, filed):
    """The same invariant, along the path that produces most datasets."""
    headers = _headers(alpha["id"])
    pipeline = client.post(
        f"{api}/pipelines",
        json={
            "name": "Alpha readings pipeline",
            "input_dataset_id": filed["dataset_id"],
            "steps": [
                {
                    "name": "parse",
                    "provider": "python-transform",
                    "configuration": {
                        "transform": "parse_numeric",
                        "options": {"column": "reading", "output": "reading_value"},
                    },
                }
            ],
        },
        headers=headers,
    )
    assert pipeline.status_code == 201, pipeline.text
    assert pipeline.json()["project_id"] == alpha["id"]

    pipeline_id = pipeline.json()["id"]
    run = client.post(f"{api}/pipelines/{pipeline_id}/run", json={}, headers=headers)
    assert run.status_code in (200, 201), run.text
    record = run.json()
    assert record["status"] == "succeeded", record.get("error")
    assert record["output_dataset_ids"], "the run produced no output dataset"
    for dataset_id in record["output_dataset_ids"]:
        dataset = client.get(f"{api}/datasets/{dataset_id}", headers=headers)
        assert dataset.json()["project_id"] == alpha["id"], dataset.json()["name"]


def test_a_model_definition_can_be_filed_and_shared(client, api, alpha, beta):
    """Requirement 4, in one test: the library keeps its generality.

    A definition made inside one project can be shared across all of them, and
    a shared one can be pulled into a project. Neither is a listing trick — the
    stored filing changes, and the listings follow.
    """
    created = client.post(
        f"{api}/models",
        json={
            "name": "Filing subject",
            "provider": "formula",
            "configuration": {"expressions": {"tripled": "1 * 3"}},
        },
        headers=_headers(alpha["id"]),
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    assert created.json()["project_id"] == alpha["id"]

    #  Not in beta while it is filed under alpha.
    listed = client.get(f"{api}/models", headers=_headers(beta["id"]))
    assert model_id not in {row["id"] for row in listed.json()}

    shared = client.post(f"{api}/models/{model_id}/project", json={"project_id": None})
    assert shared.status_code == 200, shared.text
    assert shared.json()["project_id"] is None
    for project in (alpha, beta):
        listed = client.get(f"{api}/models", headers=_headers(project["id"]))
        assert model_id in {row["id"] for row in listed.json()}, project["name"]

    refiled = client.post(
        f"{api}/models/{model_id}/project", json={"project_id": beta["id"]}
    )
    assert refiled.status_code == 200, refiled.text
    assert refiled.json()["project_id"] == beta["id"]
    listed = client.get(f"{api}/models", headers=_headers(alpha["id"]))
    assert model_id not in {row["id"] for row in listed.json()}


def test_filing_under_a_project_that_does_not_exist_is_refused(client, api, alpha):
    """Unlike the header, an explicit instruction with a bad id is an error."""
    created = client.post(
        f"{api}/models",
        json={
            "name": "Filing subject two",
            "provider": "formula",
            "configuration": {"expressions": {"tripled": "1 * 3"}},
        },
        headers=_headers(alpha["id"]),
    )
    assert created.status_code == 201, created.text
    refused = client.post(
        f"{api}/models/{created.json()['id']}/project",
        json={"project_id": "proj_does_not_exist"},
    )
    assert refused.status_code == 404, refused.text


def test_a_name_still_identifies_a_resource_from_any_project(client, api, beta, filed):
    """A project files; it is not a namespace.

    Uniqueness stays `(workspace, name)`, so a by-name lookup must reach across
    projects. Two things depend on it: the check every service runs before
    creating something, and every application page — which asks for the
    datasets it was built on and must find them whatever project the person
    looking at it happens to be standing in. Filtering this by project made
    the maintenance application answer 404 from the default project.
    """
    #  The name is taken, from somewhere else entirely.
    clash = client.post(
        f"{api}/datasets",
        json={"name": "Alpha readings", "source_id": filed["source_id"]},
        headers=_headers(beta["id"]),
    )
    assert clash.status_code == 409, clash.text


def test_an_application_page_works_from_any_project(client, api, beta):
    """The end of the same rope, through a real application's own routes."""
    route = f"{api}/applications/asset-maintenance/fleet"
    fleet = client.get(route, headers=_headers(beta["id"]))
    #  This suite does not seed the maintenance example, so the honest 404 —
    #  "the platform does not have this dataset at all" — is a skip. What the
    #  test is here to catch is the other 404: the data exists, but the caller
    #  is standing in a project that does not list it. The by-name resolution
    #  itself is pinned unconditionally by the test above.
    if fleet.status_code == 404 and "which is produced by" in fleet.text:
        pytest.skip("the maintenance example is not seeded in this database")
    assert fleet.status_code == 200, fleet.text


def test_a_project_holding_things_refuses_to_be_deleted(client, api, alpha):
    holdings = client.get(f"{api}/projects/{alpha['id']}/holdings")
    assert holdings.status_code == 200, holdings.text
    holds = holdings.json()["holds"]
    assert holds.get("datasets", 0) > 0, holds

    refused = client.delete(f"{api}/projects/{alpha['id']}")
    assert refused.status_code == 409, refused.text


def test_the_default_project_cannot_be_deleted(client, api):
    listed = client.get(f"{api}/projects").json()
    default = next(row for row in listed if row["is_default"])
    refused = client.delete(f"{api}/projects/{default['id']}")
    assert refused.status_code == 409, refused.text


def test_an_empty_project_can_be_deleted(client, api):
    created = client.post(f"{api}/projects", json={"name": "Scope throwaway"})
    assert created.status_code == 201, created.text
    removed = client.delete(f"{api}/projects/{created.json()['id']}")
    assert removed.status_code == 204, removed.text
    remaining = {row["name"] for row in client.get(f"{api}/projects").json()}
    assert "Scope throwaway" not in remaining
