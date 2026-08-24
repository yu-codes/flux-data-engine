"""Every value in SourceType must have a reader.

`ModelType` has had this protection since the model abstraction was written,
and `SourceType` did not - which is exactly why `object_storage` sat in the
enum for so long with nothing behind it. An enum value with no implementation
is a promise the API makes and cannot keep: it appears in
`GET /sources/types`, a user picks it, and ingestion fails at the last moment.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.modules.data.domain.entities import SourceType
from app.modules.data.infrastructure.readers import get_reader
from app.shared.errors import UnsupportedError
from app.shared.storage import store_from_settings

ROWS = [
    {"city": "Taipei", "population": 2600000},
    {"city": "Kaohsiung", "population": 2700000},
]


def test_every_source_type_has_a_reader():
    missing = []
    for source_type in SourceType:
        try:
            get_reader(source_type)
        except UnsupportedError:
            missing.append(source_type.value)
    assert not missing, (
        f"SourceType values with no reader: {missing}. Either implement the "
        "reader or remove the value - the API offers this list to users."
    )


def test_the_advertised_list_matches_the_enum(client, api):
    """What the API offers and what the enum declares are the same set."""
    advertised = set(client.get(f"{api}/sources/types").json()["types"])
    assert advertised == {t.value for t in SourceType}


# --------------------------------------------------------------------------
# the reader that was missing
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def stored_objects() -> dict[str, str]:
    """Put one object of each shape into the platform's own store."""
    store = store_from_settings(get_settings())
    scratch = Path(get_settings().data_root) / "samples"
    scratch.mkdir(parents=True, exist_ok=True)

    csv_path = scratch / "object_source.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)

    json_path = scratch / "object_source.json"
    json_path.write_text(json.dumps(ROWS), encoding="utf-8")

    return {
        "csv": store.put_file("tests/object_source.csv", csv_path),
        "json": store.put_file("tests/object_source.json", json_path),
    }


@pytest.mark.parametrize("shape", ["csv", "json"])
def test_an_object_is_read_by_the_reader_for_its_format(client, api, stored_objects, shape):
    """The location is new; the formats are the ones the platform already knows."""
    source = client.post(
        f"{api}/sources",
        json={
            "name": f"object storage {shape}",
            "type": "object_storage",
            "connection": {"uri": stored_objects[shape]},
        },
    )
    assert source.status_code == 201, source.text

    preview = client.get(f"{api}/sources/{source.json()['id']}/preview")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert {row["city"] for row in body["rows"]} == {"Taipei", "Kaohsiung"}


def test_an_object_that_is_not_there_says_so(client, api):
    source = client.post(
        f"{api}/sources",
        json={
            "name": "object storage missing",
            "type": "object_storage",
            "connection": {"uri": "file://tests/not_here.csv"},
        },
    )
    #  Registering may succeed or be rejected at probe time; what must not
    #  happen is a 500 with a stack trace.
    assert source.status_code in (201, 404, 422), source.text
    if source.status_code == 201:
        preview = client.get(f"{api}/sources/{source.json()['id']}/preview")
        assert preview.status_code == 404, preview.text


def test_an_unknown_extension_is_refused(client, api, stored_objects):
    store = store_from_settings(get_settings())
    uri = store.put_bytes("tests/object_source.bin", b"not a table")
    source = client.post(
        f"{api}/sources",
        json={
            "name": "object storage binary",
            "type": "object_storage",
            "connection": {"uri": uri},
        },
    )
    assert source.status_code in (201, 422), source.text
    if source.status_code == 201:
        preview = client.get(f"{api}/sources/{source.json()['id']}/preview")
        assert preview.status_code == 422, preview.text
