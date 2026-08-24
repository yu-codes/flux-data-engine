"""A provider reads the platform's data, not the platform's disk.

The typhoon engine used to open `data/typhoon/preprocessed/*.json` directly.
Its executions went through the front door - Execution, Result, lineage - while
its data went through the side one: not versioned, not traceable, and only
replaceable by changing a volume mount and restarting.

The interesting property is not that the plumbing exists but that it carries
the data: if the declared dataset is genuinely what the model reads, then
giving it a different dataset must give a different answer. That is what these
tests check, because a resolution that quietly falls back to the file would
otherwise look identical from the outside.
"""

from __future__ import annotations

import pytest

from app.plugins.bootstrap import register_builtin_plugins

REGISTRY = register_builtin_plugins()


# --------------------------------------------------------------------------
# the declaration
# --------------------------------------------------------------------------
def test_a_provider_can_state_the_data_it_needs():
    descriptor = REGISTRY.get("typhoon-analog").describe()
    required = {d.key: d for d in descriptor.required_datasets}
    assert "catalogue" in required
    assert required["catalogue"].name == "Typhoon catalogue"
    assert required["catalogue"].description


def test_the_declaration_is_published_to_callers(client, api):
    """The UI has to be able to say what a model depends on."""
    body = client.get(f"{api}/model-providers").json()
    providers = {p["key"]: p for p in body["providers"]}
    declared = providers["typhoon-analog"]["required_datasets"]
    assert [d["name"] for d in declared] == ["Typhoon catalogue"]


def test_a_provider_that_needs_nothing_declares_nothing():
    """The field costs nothing for the providers that do not use it."""
    assert REGISTRY.get("formula").describe().required_datasets == ()


# --------------------------------------------------------------------------
# the resolution
# --------------------------------------------------------------------------
ROWS = [
    {"city": "Taipei", "value": 1},
    {"city": "Tainan", "value": 2},
]


@pytest.fixture
def demo_dataset(client, api) -> str:
    source = client.post(
        f"{api}/sources",
        json={
            "name": "required dataset rows",
            "type": "inline",
            "connection": {"rows": ROWS},
        },
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Declared dependency", "source_id": source.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text
    return dataset.json()["id"]


def test_a_declared_dataset_is_read_and_handed_over(client, api, demo_dataset):
    """The service resolves the declaration into a table the plugin receives."""
    from app.core.container import build_services
    from app.core.database import session_scope
    from app.modules.model.domain.plugin import RequiredDataset
    from app.modules.platform.domain.workspaces import DEFAULT_WORKSPACE_ID
    from app.shared.scoping import WorkspaceScope

    class Descriptor:
        required_datasets = (
            RequiredDataset(key="rows", name="Declared dependency"),
        )

    with session_scope() as session:
        services = build_services(
            session, scope=WorkspaceScope(workspace_id=DEFAULT_WORKSPACE_ID)
        )
        resolved = services.executions._required_datasets(Descriptor())

    assert "rows" in resolved
    assert resolved["rows"].num_rows == len(ROWS)
    assert set(resolved["rows"].columns) == {"city", "value"}


def test_a_missing_required_dataset_is_reported_before_the_plugin_runs():
    """The error names the dataset, not a file path buried in a provider."""
    from app.core.container import build_services
    from app.core.database import session_scope
    from app.modules.model.domain.plugin import RequiredDataset
    from app.shared.errors import NotFoundError

    class Descriptor:
        required_datasets = (
            RequiredDataset(key="nope", name="No such dataset here"),
        )

    with session_scope() as session:
        services = build_services(session)
        with pytest.raises(NotFoundError) as raised:
            services.executions._required_datasets(Descriptor())
    assert "No such dataset here" in str(raised.value)


def test_a_missing_optional_dataset_is_simply_absent():
    from app.core.container import build_services
    from app.core.database import session_scope
    from app.modules.model.domain.plugin import RequiredDataset

    class Descriptor:
        required_datasets = (
            RequiredDataset(key="maybe", name="Absent", required=False),
        )

    with session_scope() as session:
        resolved = build_services(session).executions._required_datasets(Descriptor())
    assert resolved == {}


# --------------------------------------------------------------------------
# the data really is the data
# --------------------------------------------------------------------------
def test_the_engine_reads_the_records_it_is_given():
    """Two different records, two different answers.

    The point of the whole exercise: if this passed whatever was handed over,
    the platform's dataset would be decorative and the file would still be the
    real source.
    """
    from app.plugins.typhoon_analog.algorithms.loader import DataLoader

    def record(typhoon_id: str, category: str) -> dict:
        return {
            "typhoon_id": typhoon_id,
            "year": 2001,
            "name_zh": typhoon_id,
            "name_en": typhoon_id,
            "taiwan_track_category": category,
            "genesis_longitude": 130.0,
            "genesis_latitude": 20.0,
            "path": {
                "position_intensity": [
                    {"longitude": 130.0, "latitude": 20.0},
                    {"longitude": 125.0, "latitude": 22.0},
                    {"longitude": 121.0, "latitude": 24.0},
                ]
            },
        }

    loaded = DataLoader().load_records([record("A", "1"), record("B", "2")])
    assert {r.typhoon_id for r in loaded.records} == {"A", "B"}
    assert {r.taiwan_track_category for r in loaded.records} == {"1", "2"}

    #  One fewer record in, one fewer record out.
    fewer = DataLoader().load_records([record("A", "1")])
    assert len(fewer.records) == 1


def test_records_survive_a_trip_through_a_table():
    """A nested field becomes JSON text when it passes through a Dataset.

    A loader that could not read it back would silently drop every track and
    report zero usable typhoons, which looks like missing data rather than a
    parsing problem.
    """
    import json

    from app.plugins.typhoon_analog.algorithms.loader import DataLoader
    from app.shared.tabular import Table

    raw = {
        "typhoon_id": "T1",
        "year": 2001,
        "name_zh": "T1",
        "name_en": "T1",
        "taiwan_track_category": "3",
        "genesis_longitude": 130.0,
        "genesis_latitude": 20.0,
        "path": {
            "position_intensity": [
                {"longitude": 130.0, "latitude": 20.0},
                {"longitude": 121.0, "latitude": 24.0},
            ]
        },
    }
    #  What ingestion does to it: the nested object becomes a string.
    flattened = {**raw, "path": json.dumps(raw["path"])}
    through_a_table = Table.from_rows([flattened]).to_rows()

    loaded = DataLoader().load_records(through_a_table)
    assert len(loaded.records) == 1
    assert len(loaded.records[0].track) == 2
