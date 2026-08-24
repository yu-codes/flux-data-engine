"""Two workers, one unit of work. Exactly one of them may do it.

Nothing in the suite opened two workers, and the queue can genuinely hand the
same id to two of them: the recovery sweep re-queues anything that has sat in
PENDING for long enough, and "long enough" includes an execution that a worker
picked up and has not marked running yet.

The failure that follows is not a crash. Both workers see PENDING, both mark it
running, both call the plugin, and two Results are written for one submission -
two materialised datasets, two artifacts, two calls to whatever the model talks
to. Nothing anywhere says it happened twice.

So these tests run the race rather than reasoning about it: real threads, real
sessions, one row.
"""

from __future__ import annotations

import threading

import pytest

from app.core.container import build_services
from app.core.database import session_scope
from app.modules.execution.domain.entities import ExecutionStatus
from app.modules.jobs.domain.entities import JobStatus


@pytest.fixture(scope="module")
def model(client, api) -> dict:
    created = client.post(
        f"{api}/models",
        json={
            "name": "Concurrency probe",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "n * 2"}},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _submitted(model_id: str) -> str:
    """An execution sitting in PENDING, as the queue would leave it."""

    class NeverRuns:
        runs_inline = False
        mode = "queue"

        def enqueue(self, execution_id: str) -> None:
            """A worker that is not there."""

    with session_scope() as session:
        services = build_services(session)
        services.executions.dispatcher = NeverRuns()
        execution = services.executions.submit(
            model_id=model_id,
            input_payload={"rows": [{"n": 1}, {"n": 2}]},
        )
        return execution.id


def _run_in_parallel(action, workers: int = 2) -> list:
    """Run `action` on several threads, each with its own session."""
    results: list = [None] * workers
    barrier = threading.Barrier(workers)

    def worker(index: int) -> None:
        #  Every thread waits here, so they arrive at the row together rather
        #  than one after the other - a race that is scheduled is not a race.
        barrier.wait()
        try:
            with session_scope() as session:
                results[index] = action(build_services(session))
        except Exception as exc:  # noqa: BLE001 - recorded, asserted on below
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


# --------------------------------------------------------------------------
# executions
# --------------------------------------------------------------------------
def test_two_workers_do_not_both_run_one_execution(app, model):
    execution_id = _submitted(model["id"])

    outcomes = _run_in_parallel(lambda services: services.executions.run(execution_id))
    for outcome in outcomes:
        assert not isinstance(outcome, Exception), outcome

    with session_scope() as session:
        services = build_services(session)
        execution = services.executions.get(execution_id)

    assert execution.status is ExecutionStatus.SUCCEEDED
    assert execution.attempts == 1

    #  This is the assertion that catches it. `attempts` cannot be trusted to:
    #  two workers reading 0 and both writing 1 also produces 1, which is
    #  exactly the lost update this is about. Counting Results counts the work
    #  that actually happened - and two Results for one submission is what a
    #  user would eventually notice, weeks later, in a chart that double-counts.
    with session_scope() as session:
        results = [
            r for r in build_services(session).results.list(limit=500)
            if r.execution_id == execution_id
        ]
    assert len(results) == 1, f"{len(results)} results for one execution"


def test_the_loser_returns_the_execution_rather_than_failing(app, model):
    """A worker that loses the race has nothing to report and must not raise.

    It is a normal outcome, not an error: the work is being done by somebody
    else, and a stack trace in the worker log would send whoever reads it
    looking for a bug that is not there.
    """
    execution_id = _submitted(model["id"])
    outcomes = _run_in_parallel(lambda services: services.executions.run(execution_id))

    for outcome in outcomes:
        assert not isinstance(outcome, Exception)
        assert outcome.id == execution_id
        assert outcome.status is ExecutionStatus.SUCCEEDED


def test_claiming_something_already_terminal_answers_none(app, model):
    """The recovery sweep must not resurrect finished work."""
    execution_id = _submitted(model["id"])
    with session_scope() as session:
        build_services(session).executions.run(execution_id)

    with session_scope() as session:
        claimed = build_services(session).executions.repository.claim(execution_id)
    assert claimed is None


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------
def test_two_workers_do_not_both_run_one_job(app):
    ran: list[str] = []
    lock = threading.Lock()

    def handler(job) -> dict:
        with lock:
            ran.append(job.id)
        return {"ok": True}

    with session_scope() as session:
        services = build_services(session)
        services.jobs.handlers["concurrency_probe"] = handler
        job = services.jobs.repository.add(
            _pending_job(kind="concurrency_probe", target_id="thing_1")
        )
        job_id = job.id

    def run(services):
        services.jobs.handlers["concurrency_probe"] = handler
        return services.jobs.run(job_id)

    outcomes = _run_in_parallel(run)
    for outcome in outcomes:
        assert not isinstance(outcome, Exception), outcome

    with session_scope() as session:
        job = build_services(session).jobs.get(job_id)

    assert job.status is JobStatus.SUCCEEDED
    assert job.attempts == 1, "both workers claimed the same job"
    assert len(ran) == 1, f"the handler ran {len(ran)} times"


def _pending_job(*, kind: str, target_id: str):
    from app.modules.jobs.domain.entities import Job

    return Job(kind=kind, target_id=target_id)
