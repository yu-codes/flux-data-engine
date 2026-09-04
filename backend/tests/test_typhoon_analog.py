"""The preserved typhoon analog algorithms, exercised through the platform.

These tests are the safety net for the rehomed research code: Coastline's
absolute-position Chamfer distance and the Coastline-RRF fusion must behave
exactly as they did before, and must reach the user through the platform's
normal Execution/Result path.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.plugins.typhoon_analog.paths import typhoon_data_dir

pytestmark = pytest.mark.skipif(
    not (typhoon_data_dir() / "typhoons_overview.json").exists(),
    reason="the typhoon dataset is not present under the HydroAnalog project",
)

#  Three canonical CWA track shapes. Each should recover its own category from
#  geographically neighbouring analogs - the whole promise of the coastline
#  methods is that "close on the map" means "close in the ranking".
#  A westward track passing just north of Taiwan - CWA category 1/2 shape.
NORTHERN_TRACK = [
    {"latitude": 22.0, "longitude": 132.0, "wind_kt": 45, "pressure_mb": 990},
    {"latitude": 23.2, "longitude": 128.5, "wind_kt": 65, "pressure_mb": 970},
    {"latitude": 24.4, "longitude": 125.0, "wind_kt": 80, "pressure_mb": 955},
    {"latitude": 25.2, "longitude": 122.0, "wind_kt": 85, "pressure_mb": 950},
    {"latitude": 25.6, "longitude": 119.0, "wind_kt": 70, "pressure_mb": 965},
    {"latitude": 26.0, "longitude": 116.0, "wind_kt": 50, "pressure_mb": 985},
]


#  A westward track passing south of Taiwan - CWA category 4/5 shape.
SOUTHERN_TRACK = [
    {"latitude": 20.5, "longitude": 128.0, "wind_kt": 50, "pressure_mb": 985},
    {"latitude": 21.8, "longitude": 124.0, "wind_kt": 70, "pressure_mb": 965},
    {"latitude": 22.4, "longitude": 121.2, "wind_kt": 75, "pressure_mb": 960},
    {"latitude": 22.6, "longitude": 118.5, "wind_kt": 55, "pressure_mb": 980},
]

#  A northbound track hugging the east coast - CWA category 6.
EAST_COAST_TRACK = [
    {"latitude": 21.0, "longitude": 123.5, "wind_kt": 55, "pressure_mb": 980},
    {"latitude": 23.0, "longitude": 123.0, "wind_kt": 70, "pressure_mb": 965},
    {"latitude": 25.0, "longitude": 123.2, "wind_kt": 65, "pressure_mb": 970},
    {"latitude": 27.0, "longitude": 124.0, "wind_kt": 50, "pressure_mb": 985},
]


# --------------------------------------------------------------------------
# algorithms in isolation
# --------------------------------------------------------------------------
def test_chamfer_distance_is_symmetric_and_positive():
    from app.plugins.typhoon_analog.algorithms.coastline import path_offset_km

    lons_a = np.array([124.0, 122.0, 120.0])
    lats_a = np.array([24.0, 24.5, 25.0])
    lons_b = np.array([124.0, 122.0, 120.0])
    lats_b = np.array([22.0, 22.5, 23.0])

    forward = path_offset_km(lons_a, lats_a, lons_b, lats_b, 500.0)
    backward = path_offset_km(lons_b, lats_b, lons_a, lats_a, 500.0)

    assert forward == pytest.approx(backward, rel=1e-9)
    assert forward > 0  # two distinct paths are never 0 km apart
    #  Roughly two degrees of latitude apart, i.e. a couple of hundred km.
    assert 150 < forward < 300


def test_identical_paths_have_zero_offset():
    from app.plugins.typhoon_analog.algorithms.coastline import path_offset_km

    lons = np.array([124.0, 122.0, 120.5])
    lats = np.array([24.0, 24.5, 25.0])
    assert path_offset_km(lons, lats, lons, lats, 500.0) == pytest.approx(0.0, abs=1e-9)


def test_coastline_buffer_grows_with_radius():
    from app.plugins.typhoon_analog.algorithms import geometry

    small = geometry.buffer_polygon(200.0)
    large = geometry.buffer_polygon(800.0)
    assert len(small) > 3 and len(large) > 3

    def spread(polygon):
        lons = [p["lon"] for p in polygon]
        lats = [p["lat"] for p in polygon]
        return (max(lons) - min(lons)) + (max(lats) - min(lats))

    assert spread(large) > spread(small)


def test_points_inside_taiwan_have_zero_distance_to_coast():
    from app.plugins.typhoon_analog.algorithms.geometry import distances_to_coast_km

    inland = distances_to_coast_km(np.array([121.0]), np.array([23.7]))
    offshore = distances_to_coast_km(np.array([127.0]), np.array([23.7]))
    assert inland[0] == 0.0
    assert offshore[0] > 500


def test_rrf_weighting_favours_the_coastline_ranking():
    """Coastline dominates the fusion, so a top coastline hit stays on top."""
    from app.plugins.typhoon_analog.algorithms.coastline_rrf import (
        CoastlineRRFSimilarity,
    )

    fusion = CoastlineRRFSimilarity(w_coastline=0.80, w_knn=0.20, rrf_k=60)
    fusion._ids = ["A", "B"]
    result = fusion._fuse(
        query_id="query",
        coast_ids=["A", "B"],   # A is closest by absolute position
        knn_ids=["B", "A"],     # B is closest by summary features
        rain_ranks={},
        k=2,
        pool_size=2,
    )
    assert result.similar_ids[0] == "A"


# --------------------------------------------------------------------------
# through the platform
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def typhoon_model(client, api) -> dict:
    response = client.post(
        f"{api}/models",
        json={
            "name": "Typhoon analog (test)",
            "provider": "typhoon-analog",
            "configuration": {"method": "coastline_rrf", "k": 5, "buffer_km": 500.0},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_typhoon_model_is_statistical_and_not_trainable(typhoon_model):
    assert typhoon_model["type"] == "statistical"
    assert typhoon_model["trainable"] is False
    assert typhoon_model["provider"] == "typhoon-analog"


def test_coastline_rrf_prediction_through_an_execution(client, api, typhoon_model):
    run = client.post(
        f"{api}/executions",
        json={
            "model_id": typhoon_model["id"],
            "kind": "prediction",
            "input": {"track": NORTHERN_TRACK},
            "parameters": {"method": "coastline_rrf", "k": 5, "buffer_km": 500.0},
        },
    )
    assert run.status_code == 201, run.text
    execution = run.json()
    assert execution["status"] == "succeeded", execution["error"]
    assert execution["metrics"]["analog_count"] == 5

    payload = client.get(
        f"{api}/results/{execution['result_id']}/payload"
    ).json()["payload"]

    assert payload["method"] == "coastline_rrf"
    assert payload["predicted_category"] in {str(n) for n in range(1, 10)}
    assert 0 < payload["confidence"] <= 1
    assert sum(payload["category_votes"].values()) == pytest.approx(1.0, abs=1e-3)

    analogs = payload["analogs"]
    assert len(analogs) == 5
    #  Distances are reported as an absolute mean path offset in km, ascending
    #  is not guaranteed (ranking is the method's), but every one is finite.
    for analog in analogs:
        assert analog["offset_km"] > 0
        assert 0 < analog["score"] <= 1
        assert analog["track"], "each analog carries its full track for the map"
    assert payload["geometry"]["coastline"]
    assert payload["geometry"]["buffer"]


def test_a_northern_track_finds_northern_analogs(client, api, typhoon_model):
    """The whole point of Coastline: matches should be geographically close."""
    run = client.post(
        f"{api}/executions",
        json={
            "model_id": typhoon_model["id"],
            "input": {"track": NORTHERN_TRACK},
            "parameters": {"method": "coastline", "k": 5},
        },
    ).json()
    assert run["status"] == "succeeded", run["error"]

    payload = client.get(f"{api}/results/{run['result_id']}/payload").json()["payload"]
    #  Every analog's closest approach should be on the northern half of the map.
    for analog in payload["analogs"]:
        near_taiwan = [p for p in analog["track"] if p["in_range"]]
        assert near_taiwan
        assert max(p["lat"] for p in near_taiwan) > 22.0


def test_replaying_a_historical_typhoon(client, api, typhoon_model):
    catalogue = client.get(f"{api}/applications/typhoon/typhoons?limit=1").json()
    assert catalogue["total"] > 100, "the historical dataset should be loaded"
    typhoon_id = catalogue["typhoons"][0]["typhoon_id"]

    run = client.post(
        f"{api}/executions",
        json={
            "model_id": typhoon_model["id"],
            "input": {"typhoon_id": typhoon_id},
            "parameters": {"k": 3},
        },
    ).json()
    assert run["status"] == "succeeded", run["error"]
    payload = client.get(f"{api}/results/{run['result_id']}/payload").json()["payload"]
    assert payload["query"]["typhoon_id"] == typhoon_id
    assert len(payload["analogs"]) == 3


def test_buffer_km_is_validated(client, api, typhoon_model):
    response = client.post(
        f"{api}/executions",
        json={
            "model_id": typhoon_model["id"],
            "input": {"track": NORTHERN_TRACK},
            "parameters": {"buffer_km": 5.0},
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("label", "track", "expected_categories"),
    [
        ("northern westward", NORTHERN_TRACK, {"1", "2"}),
        ("southern westward", SOUTHERN_TRACK, {"4", "5"}),
        ("east-coast northbound", EAST_COAST_TRACK, {"6"}),
    ],
)
def test_canonical_tracks_recover_their_own_category(
    client, api, typhoon_model, label, track, expected_categories
):
    """Geometry drives the ranking, so analogs share the query's track family."""
    run = client.post(
        f"{api}/executions",
        json={
            "model_id": typhoon_model["id"],
            "input": {"track": track},
            "parameters": {"method": "coastline_rrf", "k": 5, "buffer_km": 500},
        },
    ).json()
    assert run["status"] == "succeeded", run["error"]

    payload = client.get(f"{api}/results/{run['result_id']}/payload").json()["payload"]
    assert payload["predicted_category"] in expected_categories, (
        f"{label}: predicted {payload['predicted_category']}, "
        f"expected one of {sorted(expected_categories)}"
    )
    #  A clear majority of the analogs must come from the same family, otherwise
    #  the fusion has stopped being driven by absolute position.
    families = [a["category"] for a in payload["analogs"]]
    in_family = sum(1 for c in families if c in expected_categories)
    assert in_family >= 3, (
        f"{label}: only {in_family}/5 analogs in {sorted(expected_categories)}"
    )


def test_rainfall_signal_can_be_toggled_per_request(client, api, typhoon_model):
    """The rainfall ranking is a per-request switch, not a fixed model property."""
    body = {
        "model_id": typhoon_model["id"],
        "input": {"track": NORTHERN_TRACK},
        "parameters": {
            "method": "coastline_rrf",
            "k": 5,
            "buffer_km": 500,
            "use_rainfall": True,
            "rainfall_region": "tn",
            "expected_rainfall": 150.0,
        },
    }
    run = client.post(f"{api}/executions", json=body).json()
    assert run["status"] == "succeeded", run["error"]
    payload = client.get(f"{api}/results/{run['result_id']}/payload").json()["payload"]
    assert payload["analogs"]
    #  Event-rainfall statistics are reported across whichever analogs were found.
    stations = (payload.get("rainfall") or {}).get("stations", {})
    assert set(stations) <= {"tn", "kh"}


def test_unknown_method_is_rejected(client, api, typhoon_model):
    response = client.post(
        f"{api}/executions",
        json={
            "model_id": typhoon_model["id"],
            "input": {"track": NORTHERN_TRACK},
            "parameters": {"method": "not-a-method"},
        },
    )
    assert response.status_code == 422
    body = response.json()
    #  The parameter contract's enum rejects it before the plugin ever runs.
    assert body["error"] == "validation_failed"
    assert any("not-a-method" in error for error in body["details"]["errors"])


def test_preserved_algorithms_only_import_within_their_package():
    """The rehoming is complete and self-contained.

    Every cross-file import inside `algorithms/` is relative, and no file still
    references the original `data_pipiline` package. Keeping this true is what
    lets the directory stay byte-identical to the research code apart from its
    import lines.
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1] / "app" / "plugins" / "typhoon_analog"
    algorithms = root / "algorithms"
    third_party = {"numpy", "pandas", "sklearn", "scipy", "json", "re", "math",
                   "abc", "dataclasses", "typing", "pathlib", "collections",
                   "__future__", "os", "sys", "warnings", "datetime", "itertools"}

    #  Parsed rather than read line by line: a docstring that happens to begin
    #  with the word "from" is prose, not an import, and a check that cannot
    #  tell the difference fails on writing rather than on the thing it guards.
    import ast

    offenders = []
    for path in sorted(algorithms.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "data_pipiline" in source:
            offenders.append(f"{path.name} still references data_pipiline")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                #  A relative import stays inside the package by definition.
                if node.level:
                    continue
                module = (node.module or "").split(".")[0]
            elif isinstance(node, ast.Import):
                module = node.names[0].name.split(".")[0]
            else:
                continue
            if module == "app":
                offenders.append(
                    f"{path.name}:{node.lineno} reaches back into the platform"
                )
            elif module not in third_party:
                offenders.append(
                    f"{path.name}:{node.lineno} unexpected import: {module}"
                )
    assert not offenders, offenders


def test_preserved_algorithms_do_not_depend_on_the_platform():
    """The engine adapts the algorithms to the platform, never the reverse."""
    from pathlib import Path as _Path

    algorithms = (
        _Path(__file__).resolve().parents[1]
        / "app" / "plugins" / "typhoon_analog" / "algorithms"
    )
    for path in algorithms.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("ExecutionContext", "ResultPayload", "PluginDescriptor"):
            assert forbidden not in text, (
                f"{path.name} knows about the platform's {forbidden}"
            )


@pytest.mark.parametrize(
    "method",
    ["coastline_rrf", "coastline", "combined_rainfall", "knn_optimized",
     "rule_based", "baseline"],
)
def test_every_advertised_method_answers_a_track_query(
    client, api, typhoon_model, method
):
    """Each method listed by the application must actually run.

    The methods take different query paths inside the engine - by track, by
    feature vector, by rule, at random - so this pins the dispatch table.
    """
    run = client.post(
        f"{api}/executions",
        json={
            "model_id": typhoon_model["id"],
            "input": {"track": NORTHERN_TRACK},
            "parameters": {"method": method, "k": 3, "buffer_km": 500},
        },
    )
    assert run.status_code == 201, run.text
    execution = run.json()
    assert execution["status"] == "succeeded", f"{method}: {execution['error']}"

    result_id = execution["result_id"]
    payload = client.get(f"{api}/results/{result_id}/payload").json()["payload"]
    assert payload["method"] == method
    assert len(payload["analogs"]) == 3
    assert payload["predicted_category"] is not None


def test_the_application_advertises_only_working_methods(client, api):
    """The method list the UI renders is the same set the tests above cover."""
    catalogue = client.get(f"{api}/applications/typhoon/methods").json()
    advertised = {m["key"] for m in catalogue["methods"]}
    assert advertised == {
        "coastline_rrf", "coastline", "combined_rainfall",
        "knn_optimized", "rule_based", "baseline",
    }
