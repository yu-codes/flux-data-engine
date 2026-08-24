"""The two pluggable seams: object storage and execution dispatch.

Both are ports with more than one implementation, so these tests pin the
contract rather than either implementation's internals.
"""

from __future__ import annotations

import pytest

from app.shared.storage import LocalObjectStore, ObjectStore, create_object_store


# --------------------------------------------------------------------------
# object storage
# --------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


def test_bytes_round_trip(store):
    uri = store.put_bytes("a/b/thing.bin", b"payload")
    assert uri.startswith("file://")
    assert store.get_bytes(uri) == b"payload"
    assert store.exists(uri)


def test_json_round_trip(store):
    uri = store.put_json("a/doc.json", {"count": 3, "label": "ok"})
    assert store.get_json(uri) == {"count": 3, "label": "ok"}


def test_files_round_trip(store, tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("from a file", encoding="utf-8")
    uri = store.put_file("uploads/source.txt", source)
    assert store.local_path(uri).read_text(encoding="utf-8") == "from a file"


def test_delete_is_idempotent(store):
    uri = store.put_bytes("gone.bin", b"x")
    store.delete(uri)
    store.delete(uri)  # deleting twice must not raise
    assert not store.exists(uri)


def test_keys_cannot_escape_the_store_root(store):
    with pytest.raises(ValueError):
        store.put_bytes("../../escaped.txt", b"nope")


def test_the_factory_builds_the_local_backend(tmp_path):
    built = create_object_store(backend="local", local_root=tmp_path)
    assert isinstance(built, LocalObjectStore)
    assert isinstance(built, ObjectStore)


def test_the_factory_rejects_an_unknown_backend(tmp_path):
    with pytest.raises(ValueError, match="unknown storage backend"):
        create_object_store(backend="carrier-pigeon", local_root=tmp_path)


def test_the_s3_backend_implements_the_same_port():
    """Both backends satisfy ObjectStore, so callers never branch on backend."""
    from app.shared.s3_storage import S3ObjectStore

    assert issubclass(S3ObjectStore, ObjectStore)
    port_methods = {
        name for name in dir(ObjectStore)
        if not name.startswith("_") and callable(getattr(ObjectStore, name))
    }
    missing = [name for name in port_methods if not hasattr(S3ObjectStore, name)]
    assert not missing, f"S3ObjectStore is missing {missing}"


# --------------------------------------------------------------------------
# execution dispatch
# --------------------------------------------------------------------------
def test_inline_dispatch_is_the_default_policy():
    from app.modules.execution.domain.ports import RunInline

    policy = RunInline()
    assert policy.runs_inline is True
    assert policy.mode == "inline"
    #  Enqueue is a no-op for the inline policy.
    assert policy.enqueue("exec_anything") is None


def test_the_dispatcher_factory_follows_the_execution_mode():
    from app.core.config import Settings
    from app.modules.execution.infrastructure.dispatch import (
        InlineDispatcher,
        RedisQueueDispatcher,
        build_dispatcher,
    )

    inline = build_dispatcher(None, Settings(execution_mode="inline"))
    assert isinstance(inline, InlineDispatcher)
    assert inline.runs_inline is True

    queued = build_dispatcher(None, Settings(execution_mode="queue"))
    assert isinstance(queued, RedisQueueDispatcher)
    assert queued.runs_inline is False
    assert queued.mode == "queue"


def test_queued_dispatch_defers_the_push_until_commit():
    """A worker must never see an id whose row is not committed yet."""
    from app.core.database import SessionFactory
    from app.modules.execution.infrastructure.dispatch import RedisQueueDispatcher

    pushed: list[str] = []

    class FakeRedis:
        def rpush(self, _queue, payload):
            pushed.append(payload)

    session = SessionFactory()
    try:
        dispatcher = RedisQueueDispatcher(
            session, redis_url="redis://unused", queue_name="flux:test"
        )
        #  The client factory moved to the shared kernel: both the execution
        #  dispatcher and the job dispatcher push onto the same list, and
        #  reaching into one module from the other for a connection helper
        #  would be a dependency between peers for no reason.
        import app.modules.execution.infrastructure.dispatch as dispatch_module

        original = dispatch_module.redis_client
        dispatch_module.redis_client = lambda _url: FakeRedis()
        try:
            dispatcher.enqueue("exec_deferred")
            assert pushed == [], "the push must wait for the commit"
            session.commit()
            assert pushed and "exec_deferred" in pushed[0]
        finally:
            dispatch_module.redis_client = original
    finally:
        session.close()


def test_submitting_in_queue_mode_leaves_the_execution_pending(client, api, monkeypatch):
    """In queue mode the API returns immediately with a pending execution."""
    from app.core.container import build_services
    from app.core.database import session_scope
    from app.modules.execution.domain.ports import ExecutionDispatcher

    model = client.post(
        f"{api}/models",
        json={
            "name": "Queued formula",
            "provider": "formula",
            "configuration": {"expressions": {"y": "x * 3"}},
        },
    ).json()

    enqueued: list[str] = []

    class RecordingDispatcher:
        runs_inline = False
        mode = "queue"

        def enqueue(self, execution_id: str) -> None:
            enqueued.append(execution_id)

    assert isinstance(RecordingDispatcher(), ExecutionDispatcher)

    with session_scope() as session:
        services = build_services(session)
        services.executions.dispatcher = RecordingDispatcher()
        execution = services.executions.submit(
            model_id=model["id"], input_payload={"x": 2}
        )
        execution_id = execution.id
        assert execution.status.value == "pending"

    assert enqueued == [execution_id]

    #  Nothing ran, so there is no result yet - exactly what a worker would find.
    stored = client.get(f"{api}/executions/{execution_id}").json()
    assert stored["status"] == "pending"
    assert stored["result_id"] is None
    assert any("queued" in line for line in stored["logs"])

    #  And running it later - as the worker does - completes it.
    with session_scope() as session:
        finished = build_services(session).executions.run(execution_id)
    assert finished.status.value == "succeeded"

    payload = client.get(
        f"{api}/results/{client.get(f'{api}/executions/{execution_id}').json()['result_id']}/payload"
    ).json()["payload"]
    assert payload["y"] == 6
