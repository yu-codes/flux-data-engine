"""Cancelling means cancelled, and an abandoned run does not stay running.

Both of these were previously claims the platform made and did not keep, and
neither is visible from a normal successful run - which is exactly why they
need tests. A cancellation that gets overwritten looks like a UI glitch, and an
execution stuck at RUNNING for ever looks like a slow model.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.container import build_services
from app.core.database import session_scope
from app.modules.execution.domain.entities import Execution, ExecutionStatus
from app.shared.ids import utcnow


@pytest.fixture(scope="module")
def model(client, api) -> dict:
    created = client.post(
        f"{api}/models",
        json={
            "name": "Lifecycle formula",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "value * 2"}},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _execution(client, api, model) -> dict:
    response = client.post(
        f"{api}/executions",
        json={"model_id": model["id"], "kind": "calculation", "input": {"value": 2}},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# cancellation
# --------------------------------------------------------------------------
def test_cancelling_a_finished_execution_is_refused(client, api, model):
    execution = _execution(client, api, model)
    assert execution["status"] == "succeeded"

    response = client.post(f"{api}/executions/{execution['id']}/cancel")
    assert response.status_code == 422, response.text
    assert "succeeded" in response.text


def test_a_pending_execution_cancels_immediately():
    """Nothing has started, so there is nothing to ask."""
    with session_scope() as session:
        services = build_services(session)
        pending = services.executions.repository.add(
            Execution(target_id="mdl_never_run", kind=_kind())
        )
        cancelled = services.executions.cancel(pending.id)
        assert cancelled.status is ExecutionStatus.CANCELLED
        assert cancelled.finished_at is not None


def test_a_running_execution_records_the_request_rather_than_the_outcome():
    """The distinction that was missing.

    Marking a running execution `cancelled` on the spot is what used to happen,
    and the worker then finished the work it had never been told to abandon and
    wrote `succeeded` over the top. Recording the *request* leaves the runner
    to end it at a point where the state is consistent.
    """
    with session_scope() as session:
        services = build_services(session)
        execution = services.executions.repository.add(
            Execution(target_id="mdl_pretend", kind=_kind())
        )
        execution.mark_running()
        services.executions.repository.update(execution)

        asked = services.executions.cancel(execution.id)
        assert asked.status is ExecutionStatus.RUNNING
        assert asked.cancel_requested is True
        assert "cancellation requested" in " ".join(asked.logs)


def test_a_cancelled_execution_never_reports_a_result(client, api, model):
    """The runner honours the request instead of overwriting it."""
    with session_scope() as session:
        services = build_services(session)
        execution = services.executions.submit(
            model_id=model["id"], kind="calculation", input_payload={"value": 2}
        )
        #  Re-submit one that is asked to stop before anyone picks it up.
        queued = services.executions.repository.add(
            Execution(
                target_id=model["id"],
                kind=execution.kind,
                input_payload={"value": 2},
                model_version_id=execution.model_version_id,
            )
        )
        services.executions.cancel(queued.id)
        ran = services.executions.run(queued.id)

    assert ran.status is ExecutionStatus.CANCELLED
    assert ran.result_id is None
    assert ran.error == "cancelled before it started"


# --------------------------------------------------------------------------
# abandoned work
# --------------------------------------------------------------------------
def test_a_running_execution_with_a_fresh_heartbeat_is_left_alone(model):
    with session_scope() as session:
        services = build_services(session)
        execution = services.executions.repository.add(
            Execution(target_id=model["id"], kind=_kind())
        )
        execution.mark_running()
        services.executions.repository.update(execution)

        reclaimed = services.executions.reclaim_stale(after_seconds=600)
        assert execution.id not in {e.id for e in reclaimed}


def test_a_running_execution_whose_worker_went_quiet_is_failed(model):
    """Otherwise it stays RUNNING for ever and nothing will ever finish it."""
    with session_scope() as session:
        services = build_services(session)
        execution = services.executions.repository.add(
            Execution(target_id=model["id"], kind=_kind())
        )
        execution.mark_running()
        execution.heartbeat_at = utcnow() - timedelta(hours=2)
        services.executions.repository.update(execution)

        reclaimed = services.executions.reclaim_stale(after_seconds=600)
        assert execution.id in {e.id for e in reclaimed}

        after = services.executions.get(execution.id)
        assert after.status is ExecutionStatus.FAILED
        assert "stopped" in (after.error or "")
        assert "no heartbeat" in " ".join(after.logs)


def test_a_heartbeat_keeps_an_execution_out_of_the_sweep(model):
    with session_scope() as session:
        services = build_services(session)
        execution = services.executions.repository.add(
            Execution(target_id=model["id"], kind=_kind())
        )
        execution.mark_running()
        execution.heartbeat_at = utcnow() - timedelta(hours=2)
        services.executions.repository.update(execution)

        #  The worker checks in, and the row is healthy again.
        services.executions.beat(execution.id)
        reclaimed = services.executions.reclaim_stale(after_seconds=600)
        assert execution.id not in {e.id for e in reclaimed}


def test_beating_a_finished_execution_does_nothing(client, api, model):
    execution = _execution(client, api, model)
    with session_scope() as session:
        services = build_services(session)
        services.executions.beat(execution["id"])
        after = services.executions.get(execution["id"])
    assert after.status is ExecutionStatus.SUCCEEDED


# --------------------------------------------------------------------------
# the plugin's side of it
# --------------------------------------------------------------------------
def test_a_plugin_can_ask_whether_it_should_stop():
    """Long computations get a way to bail out that is not being killed."""
    from app.modules.model.domain.entities import ModelDefinition
    from app.modules.model.domain.plugin import ExecutionContext, ExecutionInput

    definition = ModelDefinition(name="x", slug="x", provider="formula")
    context = ExecutionContext(
        execution_id="exec_1",
        kind=_kind(),
        definition=definition,
        input=ExecutionInput(),
        should_cancel=lambda: True,
    )
    assert context.cancelled() is True

    #  The default is "keep going", and a broken callback must not stop work.
    assert ExecutionContext(
        execution_id="exec_2", kind=_kind(), definition=definition,
        input=ExecutionInput(),
    ).cancelled() is False

    def explode() -> bool:
        raise RuntimeError("the checker itself failed")

    assert ExecutionContext(
        execution_id="exec_3", kind=_kind(), definition=definition,
        input=ExecutionInput(), should_cancel=explode,
    ).cancelled() is False


def _kind():
    from app.modules.model.domain.plugin import ExecutionKind

    return ExecutionKind.CALCULATION


def test_a_request_that_arrives_mid_run_still_cancels(monkeypatch, model):
    """The case the old code got wrong, reproduced deterministically.

    A real cancellation lands while the plugin is executing. Rather than race
    a thread, the check itself is made to answer "yes" - which is exactly what
    it would answer if somebody had pressed cancel a moment earlier.
    """
    from app.modules.execution.application.services import ExecutionService

    with session_scope() as session:
        services = build_services(session)
        execution = services.executions.repository.add(
            Execution(
                target_id=model["id"],
                kind=_kind(),
                input_payload={"value": 2},
                model_version_id=model["current_version_id"],
            )
        )
        monkeypatch.setattr(
            ExecutionService, "_cancel_requested", lambda self, _id: True
        )
        ran = services.executions.run(execution.id)

    #  The work finished, but the answer is not offered as one anybody wanted.
    assert ran.status is ExecutionStatus.CANCELLED
    assert ran.error == "cancelled while running"
    assert ran.result_id is None


# --------------------------------------------------------------------------
# providers that could otherwise run for ever
# --------------------------------------------------------------------------
def _context(provider: str, configuration: dict, **kwargs):
    from app.modules.model.domain.entities import ModelDefinition
    from app.modules.model.domain.plugin import ExecutionContext, ExecutionInput

    definition = ModelDefinition(
        name="long", slug="long", provider=provider, configuration=configuration
    )
    return ExecutionContext(
        execution_id="exec_long",
        kind=_kind(),
        definition=definition,
        input=ExecutionInput(),
        **kwargs,
    )


def _simulation(**kwargs):
    from app.modules.model.domain.registry import registry

    context = _context(
        "monte-carlo",
        {
            "expression": "price * units",
            "trials": 200_000,
            "inputs": {
                "price": {"distribution": "uniform", "min": 1, "max": 2},
                "units": {"distribution": "uniform", "min": 1, "max": 2},
            },
        },
        **kwargs,
    )
    return registry.get("monte-carlo").execute(context)


def test_a_simulation_stops_when_it_is_cancelled():
    """Two hundred thousand draws is bounded in count, not in time.

    The optimizer already checked; the simulation did not, so cancelling one
    left the request running until it finished anyway - which is the same
    "cancel is a lie" the execution layer was fixed for, one level down.
    """
    from app.shared.errors import ExecutionError

    #  Cancelled before the first batch, so there is nothing to summarise - and
    #  the reason given has to be the cancellation, not a broken expression.
    with pytest.raises(ExecutionError, match="cancelled"):
        _simulation(should_cancel=lambda: True)


def test_a_simulation_cancelled_part_way_keeps_what_it_drew():
    """A distribution from the draws that finished beats nothing at all."""
    calls = {"n": 0}

    def cancel_after_a_while() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    outcome = _simulation(should_cancel=cancel_after_a_while)

    assert outcome.metrics["complete"] == 0
    assert outcome.payload.summary["requested_trials"] == 200_000
    #  It stopped at a check rather than running the lot.
    assert 0 < outcome.metrics["trials"] <= 2_000
    assert any("cancelled" in line for line in outcome.logs)


def test_a_simulation_stops_when_it_runs_out_of_time():
    import time

    from app.shared.errors import ExecutionError

    with pytest.raises(ExecutionError, match="out of time"):
        _simulation(deadline=time.monotonic() - 1)


def test_a_simulation_that_finishes_says_it_finished():
    """The flag has to mean something, so the ordinary case is pinned too."""
    from app.modules.model.domain.registry import registry

    context = _context(
        "monte-carlo",
        {
            "expression": "price",
            "trials": 1_000,
            "inputs": {"price": {"distribution": "uniform", "min": 1, "max": 2}},
        },
    )
    outcome = registry.get("monte-carlo").execute(context)

    assert outcome.metrics["complete"] == 1
    assert outcome.metrics["trials"] == 1_000
    assert outcome.payload.summary["requested_trials"] == 1_000
