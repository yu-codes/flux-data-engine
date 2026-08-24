"""Background worker.

Runs three loops in one process:

* **queue**     — pops work pushed by the API and runs it: single executions,
                  and jobs (a pipeline run, an experiment, a report export);
* **scheduler** — fires schedules whose time has come;
* **recovery**  — sweeps up executions left PENDING (a queue blip, or an API
                  restart between INSERT and push) and RUNNING executions whose
                  worker stopped reporting in.

Start with ``python -m app.worker``. Safe to run several replicas: each
execution is claimed exactly once, and `run()` returns early for anything
already terminal.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

from app.core.config import get_settings
from app.core.console import configure_streams
from app.core.container import build_services
from app.core.database import import_all_orm_models, session_scope
from app.modules.execution.domain.entities import ExecutionStatus
from app.plugins.bootstrap import register_builtin_plugins
from app.shared.ids import utcnow
from app.shared.scoping import WorkspaceScope

configure_streams()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("app.worker")

#  A pending execution older than this was never picked up; re-run it.
RECOVERY_AFTER_SECONDS = 120
RECOVERY_BATCH = 20


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.running = True
        self._last_schedule_sweep = 0.0
        self._last_recovery_sweep = 0.0

    # -- lifecycle ---------------------------------------------------------
    def stop(self, *_: object) -> None:
        if self.running:
            logger.info("shutdown requested; finishing the current execution")
        self.running = False

    def run(self) -> int:
        import_all_orm_models()
        register_builtin_plugins()
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        logger.info(
            "worker started (mode=%s queue=%s scheduler=%s)",
            self.settings.execution_mode,
            self.settings.queue_name,
            "on" if self.settings.scheduler_enabled else "off",
        )

        client = self._redis() if self.settings.uses_queue else None
        while self.running:
            worked = self._drain_queue(client) if client else False
            self._tick_scheduler()
            self._tick_recovery()
            if not worked and not client:
                #  Nothing to block on without a queue, so pace the loops.
                time.sleep(min(self.settings.worker_poll_seconds, 5))

        logger.info("worker stopped")
        return 0

    # -- loops -------------------------------------------------------------
    def _drain_queue(self, client) -> bool:
        """Block briefly for one message, then run it."""
        import redis.exceptions

        try:
            message = client.blpop(
                self.settings.queue_name, timeout=self.settings.worker_poll_seconds
            )
        except redis.exceptions.TimeoutError:
            #  An idle blocking pop, not a fault: nothing arrived in time.
            return False
        except Exception:
            logger.exception("could not read the queue; retrying shortly")
            time.sleep(self.settings.worker_poll_seconds)
            return False
        if not message:
            return False

        _, raw = message
        try:
            envelope = json.loads(raw)
        except ValueError:
            logger.error("discarding an unreadable queue message: %r", raw)
            return True

        #  One queue, two shapes. Which key is present says what the work is;
        #  a second queue would mean a second blocking pop and a second place
        #  to look when something goes missing.
        if execution_id := envelope.get("execution_id"):
            self._run_execution(execution_id)
        elif job_id := envelope.get("job_id"):
            self._run_job(job_id)
        else:
            logger.error("discarding a queue message with no work in it: %r", raw)
        return True

    def _tick_scheduler(self) -> None:
        if not self.settings.scheduler_enabled:
            return
        now = time.monotonic()
        if now - self._last_schedule_sweep < self.settings.scheduler_interval_seconds:
            return
        self._last_schedule_sweep = now
        try:
            with session_scope() as session:
                fired = build_services(session).schedules.run_due()
                #  Each schedule's execution belongs where the schedule does.

            for schedule in fired:
                logger.info(
                    "schedule '%s' fired -> execution %s (%s)",
                    schedule.name, schedule.last_execution_id, schedule.last_status,
                )
        except Exception:
            logger.exception("the scheduler sweep failed")

    def _tick_recovery(self) -> None:
        """Re-run what was never picked up, and fail what was abandoned."""
        now = time.monotonic()
        if now - self._last_recovery_sweep < RECOVERY_AFTER_SECONDS:
            return
        self._last_recovery_sweep = now
        cutoff = utcnow() - timedelta(seconds=RECOVERY_AFTER_SECONDS)
        try:
            with session_scope() as session:
                services = build_services(session)
                stale = [
                    execution
                    for execution in services.executions.list(
                        status=ExecutionStatus.PENDING.value, limit=RECOVERY_BATCH
                    )
                    if _aware(execution.created_at) < cutoff
                ]
            for execution in stale:
                logger.warning("recovering stranded execution %s", execution.id)
                self._run_execution(execution.id)

            #  A RUNNING row whose heartbeat has gone quiet belonged to a
            #  worker that is no longer there. Nothing will ever finish it, so
            #  leaving it RUNNING misreports the platform's state for ever.
            with session_scope() as session:
                reclaimed = build_services(session).executions.reclaim_stale(
                    after_seconds=self.settings.execution_lease_seconds
                )
            for execution in reclaimed:
                logger.warning(
                    "execution %s was abandoned by its worker; marked failed",
                    execution.id,
                )

            #  Jobs get the same two sweeps: never-started, and abandoned.
            with session_scope() as session:
                services = build_services(session)
                pending_jobs = [
                    job
                    for job in services.jobs.list(status="pending", limit=RECOVERY_BATCH)
                    if _aware(job.created_at) < cutoff
                ]
                lost_jobs = services.jobs.reclaim_stale(
                    after_seconds=self.settings.execution_lease_seconds
                )
            for job in pending_jobs:
                logger.warning("recovering stranded job %s", job.id)
                self._run_job(job.id)
            for job in lost_jobs:
                logger.warning(
                    "job %s was abandoned by its worker; marked failed", job.id
                )
        except Exception:
            logger.exception("the recovery sweep failed")

    # -- work --------------------------------------------------------------
    def _scope_of(self, session, kind: str, work_id: str) -> WorkspaceScope:
        """Run this piece of work inside the workspace that asked for it.

        The worker reads a queue shared by every workspace, so it finds the
        item unscoped and then narrows to the item's own workspace before
        doing anything. Otherwise the results, datasets and versions it
        produces would belong to nobody and nothing would ever find them.
        """
        services = build_services(session)
        source = services.executions if kind == "execution" else services.jobs
        item = source.repository.get(work_id)
        workspace_id = getattr(item, "workspace_id", None) if item else None
        return WorkspaceScope(workspace_id=workspace_id)

    def _run_execution(self, execution_id: str) -> None:
        started = time.monotonic()
        with self._heartbeat(execution_id):
            try:
                with session_scope() as session:
                    scope = self._scope_of(session, "execution", execution_id)
                    execution = build_services(session, scope=scope).executions.run(
                        execution_id
                    )
                logger.info(
                    "execution %s %s in %.2fs",
                    execution_id, execution.status.value, time.monotonic() - started,
                )
            except Exception:
                #  ExecutionService already recorded the failure on the row;
                #  this only stops one bad execution from taking the worker down.
                logger.exception("execution %s raised", execution_id)

    def _run_job(self, job_id: str) -> None:
        started = time.monotonic()
        with self._heartbeat(job_id, jobs=True):
            try:
                with session_scope() as session:
                    scope = self._scope_of(session, "job", job_id)
                    job = build_services(session, scope=scope).jobs.run(job_id)
                logger.info(
                    "job %s (%s) %s in %.2fs",
                    job_id, job.kind, job.status.value, time.monotonic() - started,
                )
            except Exception:
                logger.exception("job %s raised", job_id)

    @contextmanager
    def _heartbeat(self, work_id: str, *, jobs: bool = False):
        """Say "still alive" on its own thread while the execution runs.

        A separate thread because the execution itself is one long call into a
        plugin: there is no point inside it where the worker gets control back
        to write a heartbeat, and a plugin should not have to remember to.

        Its own session, because the running execution holds one and two
        threads must not share it.
        """
        stop = threading.Event()

        def tick() -> None:
            while not stop.wait(self.settings.heartbeat_interval_seconds):
                try:
                    with session_scope() as session:
                        services = build_services(session)
                        target = services.jobs if jobs else services.executions
                        target.beat(work_id)
                except Exception:
                    #  A missed beat is survivable; the lease is generous.
                    logger.debug("heartbeat for %s failed", work_id)

        thread = threading.Thread(
            target=tick, name=f"heartbeat-{work_id}", daemon=True
        )
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)

    # -- helpers -----------------------------------------------------------
    def _redis(self):
        import redis

        #  The socket must outlive the blocking pop, otherwise the client times
        #  out reading the very reply that says "nothing arrived".
        client = redis.Redis.from_url(
            self.settings.redis_url,
            decode_responses=True,
            socket_timeout=self.settings.worker_poll_seconds + 15,
            socket_connect_timeout=10,
            health_check_interval=30,
            retry_on_timeout=False,
        )
        for attempt in range(1, 31):
            try:
                client.ping()
                logger.info("connected to redis at %s", self.settings.redis_url)
                return client
            except Exception:
                logger.warning("redis is not ready (attempt %s/30)", attempt)
                time.sleep(2)
        raise SystemExit("could not reach redis; giving up")


def _aware(value):
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)


def main() -> int:
    return Worker().run()


if __name__ == "__main__":
    sys.exit(main())
