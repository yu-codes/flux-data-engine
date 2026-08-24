"""Reports, schedules, the audit trail and metrics."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import get_settings


@pytest.fixture(scope="module")
def workspace(client, api) -> dict:
    """A dataset, a model and one succeeded execution to report on."""
    relative = "samples/test_reports.csv"
    path = Path(get_settings().data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["region", "price", "quantity"])
        writer.writeheader()
        writer.writerows(
            [
                {"region": "north", "price": 10.0, "quantity": 3},
                {"region": "south", "price": 20.0, "quantity": 5},
                {"region": "north", "price": 15.0, "quantity": 8},
            ]
        )

    source = client.post(
        f"{api}/sources",
        json={"name": "report sample", "type": "csv", "connection": {"path": relative}},
    ).json()
    dataset = client.post(
        f"{api}/datasets", json={"name": "Report sample", "source_id": source["id"]}
    ).json()
    model = client.post(
        f"{api}/models",
        json={
            "name": "Report revenue",
            "provider": "formula",
            "configuration": {"expressions": {"revenue": "price * quantity"}},
        },
    ).json()
    execution = client.post(
        f"{api}/executions", json={"model_id": model["id"], "dataset_id": dataset["id"]}
    ).json()
    assert execution["status"] == "succeeded", execution["error"]
    return {"dataset": dataset, "model": model, "execution": execution}


# --------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def report(client, api, workspace) -> dict:
    response = client.post(
        f"{api}/reports",
        json={
            "name": "Revenue review",
            "description": "What the revenue model produced this cycle",
            "sections": [
                {"kind": "text", "title": "Summary",
                 "body": "Revenue was computed by a formula model, with no training."},
                {"kind": "model", "title": "The model",
                 "model_id": workspace["model"]["id"]},
                {"kind": "execution", "title": "Provenance",
                 "execution_id": workspace["execution"]["id"]},
                {"kind": "metrics", "title": "Run metrics",
                 "execution_id": workspace["execution"]["id"]},
                {"kind": "table", "title": "Rows",
                 "result_id": workspace["execution"]["result_id"],
                 "options": {"limit": 5}},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_section_without_its_reference_is_refused(client, api):
    response = client.post(
        f"{api}/reports",
        json={
            "name": "Incomplete report",
            "sections": [{"kind": "metrics", "title": "no execution id"}],
        },
    )
    assert response.status_code == 422
    assert "needs a reference id" in response.json()["message"]


def test_rendering_resolves_every_section_against_live_data(client, api, report, workspace):
    rendered = client.get(f"{api}/reports/{report['id']}/render").json()
    assert rendered["name"] == "Revenue review"
    kinds = [section["kind"] for section in rendered["sections"]]
    assert kinds == ["text", "model", "execution", "metrics", "table"]
    assert not any("error" in section for section in rendered["sections"]), rendered

    model_section = rendered["sections"][1]
    assert model_section["model_type"] == "formula"
    assert model_section["trainable"] is False
    #  A resolved field must never shadow the envelope's own "kind".
    assert rendered["sections"][2]["execution_kind"] == "calculation"

    table_section = rendered["sections"][4]
    assert table_section["row_count"] == 3
    assert {"region", "price", "quantity", "revenue"} <= set(table_section["rows"][0])


def test_a_broken_reference_degrades_only_its_own_section(client, api, workspace):
    report = client.post(
        f"{api}/reports",
        json={
            "name": "Report with a dead link",
            "sections": [
                {"kind": "text", "title": "Intro", "body": "still here"},
                {"kind": "metrics", "title": "Missing",
                 "execution_id": "exec_does_not_exist"},
            ],
        },
    ).json()
    rendered = client.get(f"{api}/reports/{report['id']}/render").json()
    assert rendered["sections"][0]["body"] == "still here"
    assert "error" in rendered["sections"][1]
    assert "not found" in rendered["sections"][1]["error"]


@pytest.mark.parametrize(
    ("fmt", "needle", "media"),
    [
        ("markdown", "# Revenue review", "text/markdown"),
        ("html", "<h1>Revenue review</h1>", "text/html"),
        ("json", '"name": "Revenue review"', "application/json"),
    ],
)
def test_export_produces_a_downloadable_document(client, api, report, fmt, needle, media):
    response = client.get(f"{api}/reports/{report['id']}/export?format={fmt}")
    assert response.status_code == 200, response.text
    assert media in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert needle in response.text


def test_export_is_recorded_on_the_report(client, api, report):
    client.get(f"{api}/reports/{report['id']}/export?format=markdown")
    stored = client.get(f"{api}/reports/{report['id']}").json()
    assert stored["last_export_format"] == "markdown"
    assert stored["last_export_uri"]
    assert stored["last_exported_at"]


def test_markdown_export_contains_the_table_rows(client, api, report):
    text = client.get(f"{api}/reports/{report['id']}/export?format=markdown").text
    assert "| region | price | quantity | revenue |" in text
    assert "| north | 10.0 | 3 | 30.0 |" in text


# --------------------------------------------------------------------------
# schedules
# --------------------------------------------------------------------------
def test_a_schedule_needs_exactly_one_trigger(client, api, workspace):
    both = client.post(
        f"{api}/schedules",
        json={
            "name": "Two triggers",
            "target_id": workspace["model"]["id"],
            "interval_seconds": 60,
            "cron": "0 * * * *",
        },
    )
    assert both.status_code == 422
    assert "exactly one trigger" in both.json()["message"]

    neither = client.post(
        f"{api}/schedules",
        json={"name": "No trigger", "target_id": workspace["model"]["id"]},
    )
    assert neither.status_code == 422


def test_an_invalid_cron_is_refused(client, api, workspace):
    response = client.post(
        f"{api}/schedules",
        json={
            "name": "Bad cron",
            "target_id": workspace["model"]["id"],
            "cron": "99 * * * *",
        },
    )
    assert response.status_code == 422
    assert "out of range" in response.json()["message"]


def test_preview_shows_the_next_fire_times(client, api):
    body = client.post(
        f"{api}/schedules/preview", json={"cron": "0 3 * * *", "count": 3}
    ).json()
    moments = [datetime.fromisoformat(m) for m in body["next_runs"]]
    assert len(moments) == 3
    assert all(m.hour == 3 and m.minute == 0 for m in moments)
    #  Consecutive daily runs.
    assert moments[1] - moments[0] == timedelta(days=1)


@pytest.fixture(scope="module")
def schedule(client, api, workspace) -> dict:
    response = client.post(
        f"{api}/schedules",
        json={
            "name": "Nightly revenue",
            "description": "Recompute revenue every night",
            "target_id": workspace["model"]["id"],
            "kind": "calculation",
            "cron": "0 2 * * *",
            "dataset_id": workspace["dataset"]["id"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_new_schedule_has_a_next_run(schedule):
    assert schedule["status"] == "active"
    assert schedule["next_run_at"]
    assert schedule["run_count"] == 0


def test_running_now_submits_an_ordinary_execution(client, api, schedule):
    fired = client.post(f"{api}/schedules/{schedule['id']}/run").json()
    assert fired["run_count"] == 1
    assert fired["last_status"] == "succeeded", fired["last_error"]
    assert fired["last_execution_id"]

    execution = client.get(f"{api}/executions/{fired['last_execution_id']}").json()
    assert execution["status"] == "succeeded"
    #  The schedule stamps itself on the execution, so a run is traceable back.
    assert execution["context"]["schedule_id"] == schedule["id"]


def test_pausing_and_resuming(client, api, schedule):
    paused = client.post(f"{api}/schedules/{schedule['id']}/pause").json()
    assert paused["status"] == "paused"

    resumed = client.post(f"{api}/schedules/{schedule['id']}/resume").json()
    assert resumed["status"] == "active"
    assert resumed["next_run_at"]


def test_due_schedules_fire_and_reschedule(client, api, workspace):
    """The worker's sweep, exercised directly against the service."""
    from app.core.container import build_services
    from app.core.database import session_scope

    created = client.post(
        f"{api}/schedules",
        json={
            "name": "Every 30 seconds",
            "target_id": workspace["model"]["id"],
            "kind": "calculation",
            "interval_seconds": 30,
            "dataset_id": workspace["dataset"]["id"],
        },
    ).json()

    with session_scope() as session:
        services = build_services(session)
        #  A brand-new schedule is not due yet.
        assert not [s for s in services.schedules.run_due() if s.id == created["id"]]

        #  Wind its next run into the past, the way the passage of time would.
        schedule = services.schedules.get(created["id"])
        #  The domain reads naive timestamps as UTC, so write UTC here too.
        schedule.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
        services.schedules.repository.update(schedule)

    with session_scope() as session:
        fired = build_services(session).schedules.run_due()

    assert created["id"] in {s.id for s in fired}
    after = client.get(f"{api}/schedules/{created['id']}").json()
    assert after["run_count"] == 1
    assert after["last_status"] == "succeeded"
    #  Rescheduled into the future, so it does not fire again immediately.
    assert _as_utc(after["next_run_at"]) > datetime.now(UTC)


def test_deleting_a_schedule(client, api, workspace):
    created = client.post(
        f"{api}/schedules",
        json={
            "name": "Disposable schedule",
            "target_id": workspace["model"]["id"],
            "interval_seconds": 3600,
        },
    ).json()
    assert client.delete(f"{api}/schedules/{created['id']}").status_code == 204
    assert client.get(f"{api}/schedules/{created['id']}").status_code == 404


# --------------------------------------------------------------------------
# audit and metrics
# --------------------------------------------------------------------------
def test_writes_are_recorded_in_the_audit_trail(client, api, report):
    entries = client.get(f"{api}/audit?limit=200").json()
    actions = {entry["action"] for entry in entries}
    assert "auth.login" in actions
    assert "report.create" in actions
    assert "schedule.create" in actions

    report_entries = [e for e in entries if e["action"] == "report.create"]
    assert any(e["resource_id"] == report["id"] for e in report_entries)
    assert all(e["actor_email"] for e in report_entries)


def test_the_audit_trail_can_be_filtered(client, api):
    entries = client.get(f"{api}/audit?resource_type=schedule&limit=50").json()
    assert entries
    assert all(entry["resource_type"] == "schedule" for entry in entries)


def test_metrics_are_exposed_in_prometheus_format(client, api):
    response = client.get(f"{api}/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "# TYPE flux_http_requests_total counter" in body
    assert "flux_http_requests_total{" in body
    assert "flux_http_request_duration_seconds_bucket{" in body


def test_metrics_summary_counts_requests(client, api):
    body = client.get(f"{api}/metrics/summary").json()
    assert body["requests_total"] > 0
    assert body["uptime_seconds"] >= 0


def test_responses_carry_a_correlation_id(client, api):
    response = client.get(f"{api}/info")
    assert response.headers["x-request-id"]
    assert response.headers["x-response-time"].endswith("ms")


def _as_utc(value: str) -> datetime:
    """Parse an API timestamp, reading a naive one as UTC like the domain does."""
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# --------------------------------------------------------------------------
# scheduling something that is not a model
# --------------------------------------------------------------------------
def test_a_pipeline_can_be_scheduled(client, api, workspace):
    """The most ordinary recurring job a data platform is asked for.

    A schedule named a model and nothing else, so "re-run this pipeline every
    morning" could not be said at all - the trigger, the cadence and the
    bookkeeping were already right, only the verb was missing.
    """
    dataset = workspace["dataset"]
    pipeline = client.post(
        f"{api}/pipelines",
        json={
            "name": "Nightly reshape",
            "input_dataset_id": dataset["id"],
            "steps": [
                {
                    "name": "keep the named ones",
                    "provider": "python-transform",
                    "configuration": {
                        "transform": "filter_rows",
                        "options": {"column": "city", "op": "not_empty"},
                    },
                }
            ],
        },
    )
    assert pipeline.status_code == 201, pipeline.text

    created = client.post(
        f"{api}/schedules",
        json={
            "name": "Nightly reshape at 3am",
            "target_id": pipeline.json()["id"],
            "target_type": "pipeline",
            "cron": "0 3 * * *",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["target_type"] == "pipeline"

    #  Firing it submits a job rather than an execution: a pipeline run is not
    #  one execution, and the scheduler loop must not sit inside it.
    fired = client.post(f"{api}/schedules/{created.json()['id']}/run")
    assert fired.status_code == 200, fired.text
    body = fired.json()
    assert body["last_status"] in {"pending", "running", "succeeded"}
    assert body["last_execution_id"], "nothing was recorded as having been fired"

    job = client.get(f"{api}/jobs/{body['last_execution_id']}")
    assert job.status_code == 200, job.text
    assert job.json()["kind"] == "pipeline_run"


def test_scheduling_something_that_does_not_exist_is_refused(client, api):
    response = client.post(
        f"{api}/schedules",
        json={
            "name": "Ghost pipeline",
            "target_id": "pipe_nope",
            "target_type": "pipeline",
            "cron": "0 3 * * *",
        },
    )
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------
# what an operator can see
# --------------------------------------------------------------------------
def test_executions_are_measured_not_just_http_requests(client, api, workspace):
    """For a platform about executions, HTTP latency is the wrong histogram.

    Most runs happen in a worker, where there is no request to time. "Which
    provider is slow" and "is the failure rate moving" were both unanswerable
    from the metrics endpoint.
    """
    client.post(
        f"{api}/executions",
        json={
            "model_id": workspace["model"]["id"],
            "dataset_id": workspace["dataset"]["id"],
        },
    )

    exposition = client.get(f"{api}/metrics").text
    assert "flux_execution_duration_seconds_bucket" in exposition
    assert 'provider="formula"' in exposition
    assert 'status="succeeded"' in exposition

    summary = client.get(f"{api}/metrics/summary").json()
    assert summary["executions_total"] >= 1
    assert "executions_failed_total" in summary
