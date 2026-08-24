"""Independent steps of one pipeline run at the same time.

A pipeline that fans out - load once, then six unrelated branches - spent its
whole wall clock running those branches one after another, for no reason but
the shape of the loop that walked them. Nothing in a branch reads anything
another branch writes, so nothing in a branch has any reason to wait.

The database decides whether that actually happens: SQLite takes one writer at
a time, so the parallel path is off there and these tests exercise the
scheduler directly rather than pretending otherwise.
"""

from __future__ import annotations

import threading
import time

from app.modules.orchestration.application.services import PipelineService
from app.modules.orchestration.domain.entities import (
    Pipeline,
    PipelineStep,
    RunStatus,
    StepRun,
    waves,
)


def _step(name: str, **kwargs) -> PipelineStep:
    return PipelineStep(name=name, provider="python-transform", **kwargs)


# --------------------------------------------------------------------------
# which steps may run together
# --------------------------------------------------------------------------
def test_a_chain_is_one_step_per_wave():
    chain = [
        _step("load"),
        _step("clean", input_from="load"),
        _step("score", input_from="clean"),
    ]
    assert [[s.name for s in wave] for wave in waves(chain)] == [
        ["load"],
        ["clean"],
        ["score"],
    ]


def test_branches_off_one_step_share_a_wave():
    fan_out = [
        _step("load"),
        _step("north", input_from="load"),
        _step("south", input_from="load"),
        _step("east", input_from="load"),
    ]
    grouped = [[s.name for s in wave] for wave in waves(fan_out)]
    assert grouped[0] == ["load"]
    assert sorted(grouped[1]) == ["east", "north", "south"]


def test_a_merging_step_waits_for_every_parent():
    """The step that reads two branches belongs after the slower of them."""
    graph = [
        _step("load"),
        _step("left", input_from="load"),
        _step("middle", input_from="left"),
        _step("right", input_from="load"),
        _step("join", input_from="middle", inputs={"other": "right"}),
    ]
    grouped = [[s.name for s in wave] for wave in waves(graph)]
    assert grouped[-1] == ["join"]
    #  'right' could have run in wave 1; what matters is that 'join' comes
    #  after both of the steps it reads, not that every wave is full.
    assert "middle" in grouped[2]


def test_steps_with_no_input_all_start_together():
    grouped = waves([_step("a"), _step("b"), _step("c")])
    assert len(grouped) == 1
    assert len(grouped[0]) == 3


# --------------------------------------------------------------------------
# and that they really do run together
# --------------------------------------------------------------------------
class _Recorder:
    """A worker that shows whether it was ever called twice at once."""

    def __init__(self, hold: float = 0.05):
        self.hold = hold
        self.running = 0
        self.most_at_once = 0
        self.threads: set[str] = set()
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(
        self,
        pipeline_id,
        step_name,
        order,
        source_version_id,
        source_table,
        extra_inputs,
        depth,
    ) -> StepRun:
        with self._lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
            self.threads.add(threading.current_thread().name)
            self.calls.append(step_name)
        time.sleep(self.hold)
        with self._lock:
            self.running -= 1
        return StepRun(
            step_name=step_name,
            model_id="",
            order=order,
            status=RunStatus.SUCCEEDED,
            execution_id=f"exec_{step_name}",
            metrics={"ran": True},
        )


def _service(worker, max_parallel: int) -> PipelineService:
    """A pipeline service with only what the wave scheduler touches.

    Built by hand rather than through the container: what is under test is
    which steps are dispatched together, and wiring a database to find that
    out would test the database.
    """
    service = object.__new__(PipelineService)
    service.worker = worker
    service.max_parallel = max_parallel
    return service


def _prepared(names: list[str]) -> list[tuple]:
    return [
        (_step(name), StepRun(step_name=name, model_id="", order=index), None, None, {})
        for index, name in enumerate(names)
    ]


def test_independent_steps_run_at_the_same_time():
    recorder = _Recorder()
    service = _service(recorder, max_parallel=4)

    prepared = _prepared(["north", "south", "east"])
    done = service._run_wave(
        Pipeline(name="Fans out", input_dataset_id="ds_1"), prepared, 0
    )

    assert recorder.most_at_once > 1, "the steps were run one after another"
    assert len(recorder.threads) > 1
    assert sorted(step.name for step, _ in done) == ["east", "north", "south"]
    #  What the worker recorded in its own session reached the run's record.
    assert all(step_run.status is RunStatus.SUCCEEDED for _, step_run in done)
    assert all(step_run.execution_id for _, step_run in done)


def test_one_at_a_time_is_what_a_deployment_asks_for_by_setting_one():
    recorder = _Recorder(hold=0.01)
    service = _service(recorder, max_parallel=1)
    ran: list[str] = []

    def _run_one(pipeline, step, step_run, *args):
        ran.append(step.name)
        step_run.status = RunStatus.SUCCEEDED

    service._run_one = _run_one
    service._run_wave(
        Pipeline(name="Fans out", input_dataset_id="ds_1"),
        _prepared(["north", "south"]),
        0,
    )

    assert ran == ["north", "south"]
    assert recorder.calls == [], "the worker ran despite parallelism being off"


def test_a_step_that_fails_in_a_worker_fails_its_step_run():
    """A thread that raises must not leave a step looking untouched."""

    def _explode(*args):
        raise RuntimeError("the worker died")

    service = _service(_explode, max_parallel=4)
    done = service._run_wave(
        Pipeline(name="Fans out", input_dataset_id="ds_1"),
        _prepared(["north", "south"]),
        0,
    )

    assert all(step_run.status is RunStatus.FAILED for _, step_run in done)
    assert all("the worker died" in step_run.error for _, step_run in done)


def test_a_nested_pipeline_step_is_never_handed_to_a_worker():
    """It writes a run of its own, which belongs to the caller's session."""
    recorder = _Recorder(hold=0.01)
    service = _service(recorder, max_parallel=4)
    ran: list[str] = []

    def _run_one(pipeline, step, step_run, *args):
        ran.append(step.name)
        step_run.status = RunStatus.SUCCEEDED

    service._run_one = _run_one
    prepared = [
        (
            PipelineStep(name="nested", pipeline_id="pipe_other"),
            StepRun(step_name="nested", model_id="", order=0),
            None,
            None,
            {},
        ),
        *_prepared(["north", "south"]),
    ]
    service._run_wave(Pipeline(name="Mixed", input_dataset_id="ds_1"), prepared, 0)

    assert ran == ["nested"]
    assert sorted(recorder.calls) == ["north", "south"]
