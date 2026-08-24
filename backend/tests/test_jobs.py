"""Work that outlives the request that asked for it.

A pipeline run, an experiment run and a report export used to happen inside the
HTTP call. That is a request timeout waiting to happen, and in queue mode there
was nothing at all to tell the page that the work had finished.

These tests cover the three things that make a background job trustworthy: it
records what happened even when it fails, it can be stopped, and something can
watch it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from app.modules.jobs.api.stream import sse, watch
from app.modules.jobs.application.services import JobService
from app.modules.jobs.domain.entities import Job, JobStatus
from app.shared.errors import UnsupportedError, ValidationError
from app.shared.ids import utcnow


class FakeRepository:
    """In memory, so the service can be tested without a database."""

    def __init__(self):
        self.rows: dict[str, Job] = {}

    def add(self, job: Job) -> Job:
        self.rows[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self.rows.get(job_id)

    def update(self, job: Job) -> Job:
        self.rows[job.id] = job
        return job

    def claim(self, job_id: str) -> Job | None:
        """The same conditional the SQL version does, in one place.

        A double that always succeeds would let the service look correct here
        and lose races in production, which is the failure this method exists
        to prevent.
        """
        job = self.rows.get(job_id)
        if job is None or job.status is not JobStatus.PENDING:
            return None
        job.mark_running()
        return job

    def list(self, *, kind=None, target_id=None, status=None, limit=100):
        found = [
            j for j in self.rows.values()
            if (kind is None or j.kind == kind)
            and (target_id is None or j.target_id == target_id)
            and (status is None or j.status.value == status)
        ]
        return found[:limit]


def service(handlers=None) -> JobService:
    return JobService(FakeRepository(), handlers=handlers or {})


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------
def test_a_job_records_what_its_handler_produced():
    jobs = service({"demo": lambda job: {"rows": 7, "target": job.target_id}})
    job = jobs.submit(kind="demo", target_id="thing_1")

    assert job.status is JobStatus.SUCCEEDED
    assert job.outcome == {"rows": 7, "target": "thing_1"}
    assert job.attempts == 1
    assert job.duration_seconds is not None


def test_a_handler_that_raises_becomes_a_failed_job_not_an_exception():
    """A worker has nowhere to propagate to, and silent loss is worse."""
    def explode(_job):
        raise RuntimeError("the pipeline fell over")

    jobs = service({"demo": explode})
    job = jobs.submit(kind="demo", target_id="thing_1")

    assert job.status is JobStatus.FAILED
    assert "the pipeline fell over" in (job.error or "")
    #  The traceback is kept, because "it failed" without a reason is not a
    #  report, it is a shrug.
    assert "Traceback" in job.outcome.get("traceback", "")


def test_an_unknown_kind_is_refused_at_submission():
    jobs = service({"demo": lambda job: {}})
    with pytest.raises(UnsupportedError) as raised:
        jobs.submit(kind="not_a_thing", target_id="x")
    assert "demo" in str(raised.value.details)


def test_the_handlers_a_build_offers_are_discoverable():
    jobs = service({"b": lambda j: {}, "a": lambda j: {}})
    assert jobs.kinds() == ["a", "b"]


def test_a_job_that_already_finished_is_not_run_twice():
    calls = []
    jobs = service({"demo": lambda job: calls.append(1) or {}})
    job = jobs.submit(kind="demo", target_id="x")
    jobs.run(job.id)
    assert len(calls) == 1


# --------------------------------------------------------------------------
# stopping
# --------------------------------------------------------------------------
def test_a_pending_job_cancels_outright():
    jobs = service({"demo": lambda job: {}})
    job = jobs.repository.add(Job(kind="demo", target_id="x"))
    cancelled = jobs.cancel(job.id)
    assert cancelled.status is JobStatus.CANCELLED


def test_a_running_job_is_asked_rather_than_declared_stopped():
    jobs = service({"demo": lambda job: {}})
    job = jobs.repository.add(Job(kind="demo", target_id="x"))
    job.mark_running()
    jobs.repository.update(job)

    asked = jobs.cancel(job.id)
    assert asked.status is JobStatus.RUNNING
    assert asked.cancel_requested is True


def test_a_job_cancelled_before_it_starts_never_calls_its_handler():
    calls = []
    jobs = service({"demo": lambda job: calls.append(1) or {}})
    job = jobs.repository.add(Job(kind="demo", target_id="x"))
    jobs.cancel(job.id)
    ran = jobs.run(job.id)

    assert ran.status is JobStatus.CANCELLED
    assert calls == []


def test_a_job_cancelled_while_running_does_not_report_success():
    """The handler finished, but nobody wanted the answer any more."""
    jobs = service()

    def slow(job):
        #  Somebody presses cancel while this is working.
        current = jobs.repository.get(job.id)
        current.request_cancel()
        jobs.repository.update(current)
        return {"rows": 3}

    jobs.handlers["demo"] = slow
    job = jobs.submit(kind="demo", target_id="x")
    assert job.status is JobStatus.CANCELLED
    assert job.outcome == {}


def test_cancelling_a_finished_job_is_refused():
    jobs = service({"demo": lambda job: {}})
    job = jobs.submit(kind="demo", target_id="x")
    with pytest.raises(ValidationError):
        jobs.cancel(job.id)


# --------------------------------------------------------------------------
# abandoned
# --------------------------------------------------------------------------
def test_a_job_whose_worker_went_quiet_is_failed():
    jobs = service({"demo": lambda job: {}})
    job = jobs.repository.add(Job(kind="demo", target_id="x"))
    job.mark_running()
    job.heartbeat_at = utcnow() - timedelta(hours=2)
    jobs.repository.update(job)

    reclaimed = jobs.reclaim_stale(after_seconds=600)
    assert [j.id for j in reclaimed] == [job.id]
    assert jobs.get(job.id).status is JobStatus.FAILED


def test_a_job_that_is_still_beating_is_left_alone():
    jobs = service({"demo": lambda job: {}})
    job = jobs.repository.add(Job(kind="demo", target_id="x"))
    job.mark_running()
    jobs.repository.update(job)
    assert jobs.reclaim_stale(after_seconds=600) == []


# --------------------------------------------------------------------------
# watching
# --------------------------------------------------------------------------
def _collect(agen) -> list[str]:
    async def drain():
        return [message async for message in agen]

    return asyncio.run(drain())


def test_the_stream_reports_a_change_once_and_stops_when_it_finishes():
    states = [
        ({"status": "pending"}, False),
        ({"status": "running"}, False),
        ({"status": "running"}, False),   # unchanged: must not be re-sent
        ({"status": "succeeded"}, True),
    ]
    steps = iter(states)
    messages = _collect(watch(lambda: next(steps), poll_seconds=0))

    assert len(messages) == 3
    assert '"status": "pending"' in messages[0]
    assert '"status": "succeeded"' in messages[-1]
    assert all(m.startswith("event: status\n") for m in messages)


def test_the_stream_gives_up_rather_than_holding_a_connection_for_ever():
    messages = _collect(
        watch(
            lambda: ({"status": "running"}, False),
            poll_seconds=0,
            timeout_seconds=3,
        )
    )
    assert messages[-1].startswith("event: timeout")
    assert "stream expired" in messages[-1]


def test_an_event_is_framed_as_a_client_expects():
    message = sse("status", {"b": 2, "a": 1})
    assert message.startswith("event: status\ndata: ")
    assert message.endswith("\n\n")
    #  Keys sorted so an unchanged job never looks changed.
    assert json.loads(message.split("data: ", 1)[1]) == {"a": 1, "b": 2}


# --------------------------------------------------------------------------
# through the API, against the real container
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pipeline(client, api) -> dict:
    source = client.post(
        f"{api}/sources",
        json={
            "name": "jobs inline source",
            "type": "inline",
            "connection": {
                "rows": [{"city": "Taipei", "n": 3}, {"city": "Tainan", "n": 5}]
            },
        },
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Jobs sample", "source_id": source.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text

    model = client.post(
        f"{api}/models",
        json={
            "name": "Jobs step",
            "provider": "python-transform",
            "configuration": {
                "transform": "select_columns",
                "options": {"columns": ["city"]},
            },
        },
    )
    assert model.status_code == 201, model.text

    created = client.post(
        f"{api}/pipelines",
        json={
            "name": "Jobs pipeline",
            "input_dataset_id": dataset.json()["id"],
            "steps": [{"name": "narrow", "model_id": model.json()["id"]}],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_a_pipeline_can_be_asked_for_in_the_background(client, api, pipeline):
    response = client.post(f"{api}/pipelines/{pipeline['id']}/run?background=true", json={})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    job = client.get(f"{api}/jobs/{job_id}")
    assert job.status_code == 200, job.text
    body = job.json()
    assert body["kind"] == "pipeline_run"
    assert body["target_id"] == pipeline["id"]
    #  Inline dispatch in tests means it is already done; queue mode would
    #  report pending here and the stream would carry it to done.
    assert body["status"] in ("pending", "running", "succeeded")
    if body["status"] == "succeeded":
        assert body["outcome"]["pipeline_run_id"]
        assert body["outcome"]["steps"] == 1


def test_running_a_pipeline_in_the_foreground_still_works(client, api, pipeline):
    """Background is an option, not a replacement."""
    response = client.post(f"{api}/pipelines/{pipeline['id']}/run", json={})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "succeeded"


def test_the_api_lists_the_kinds_of_work_this_build_runs(client, api):
    kinds = client.get(f"{api}/job-kinds").json()["kinds"]
    assert {"pipeline_run", "experiment_run", "report_export"} <= set(kinds)


def test_retrying_creates_a_new_job_and_leaves_the_old_one(client, api, pipeline):
    first = client.post(f"{api}/pipelines/{pipeline['id']}/run?background=true", json={})
    job_id = first.json()["job_id"]

    retried = client.post(f"{api}/jobs/{job_id}/retry")
    assert retried.status_code == 201, retried.text
    assert retried.json()["id"] != job_id
    #  The original is still on the record: what was tried, and when.
    assert client.get(f"{api}/jobs/{job_id}").status_code == 200


def test_jobs_can_be_filtered_by_what_they_were_working_on(client, api, pipeline):
    response = client.get(f"{api}/jobs?target_id={pipeline['id']}")
    assert response.status_code == 200, response.text
    listed = response.json()
    assert listed, response.text
    assert all(job["target_id"] == pipeline["id"] for job in listed)


def test_the_event_stream_ends_when_the_job_is_already_finished(client, api, pipeline):
    response = client.post(f"{api}/pipelines/{pipeline['id']}/run?background=true", json={})
    job_id = response.json()["job_id"]

    with client.stream("GET", f"{api}/jobs/{job_id}/events") as stream:
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        body = "".join(stream.iter_text())

    assert "event: status" in body
    assert f'"id": "{job_id}"' in body


def test_streaming_a_job_that_does_not_exist_is_a_404(client, api):
    response = client.get(f"{api}/jobs/job_nope/events")
    assert response.status_code == 404
