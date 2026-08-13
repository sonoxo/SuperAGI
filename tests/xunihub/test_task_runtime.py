from pathlib import Path

from superagi.xunihub.task_runtime import TaskExecutor, TaskStatus, TaskStore


def make_store(tmp_path: Path) -> TaskStore:
    return TaskStore(str(tmp_path / "tasks.sqlite3"))


def test_enqueue_is_durable_and_idempotent(tmp_path):
    store = make_store(tmp_path)
    first = store.enqueue(
        "project-1",
        "build",
        {"prompt": "hello"},
        idempotency_key="request-1",
        now=100,
    )
    duplicate = store.enqueue(
        "project-1",
        "build",
        {"prompt": "different payload is ignored"},
        idempotency_key="request-1",
        now=101,
    )
    reopened = make_store(tmp_path)
    persisted = reopened.get(first.task_id)

    assert duplicate.task_id == first.task_id
    assert persisted is not None
    assert persisted.payload == {"prompt": "hello"}
    assert len(reopened.list_recent(project_id="project-1")) == 1


def test_claim_is_exclusive_and_completion_persists(tmp_path):
    store = make_store(tmp_path)
    task = store.enqueue("project-1", "build", now=100)
    claimed = store.claim_next("worker-a", lease_seconds=30, now=100)

    assert claimed is not None
    assert claimed.task_id == task.task_id
    assert claimed.status == TaskStatus.RUNNING
    assert store.claim_next("worker-b", now=100) is None

    done = store.complete(task.task_id, "worker-a", {"ok": True}, now=101)
    assert done.status == TaskStatus.SUCCEEDED
    assert done.result == {"ok": True}
    assert done.claimed_by is None


def test_failure_retries_then_becomes_terminal(tmp_path):
    store = make_store(tmp_path)
    task = store.enqueue("project-1", "build", max_attempts=2, now=100)

    assert store.claim_next("worker", now=100) is not None
    retried = store.fail(
        task.task_id, "worker", "boom", retry_delay_seconds=10, now=101
    )
    assert retried.status == TaskStatus.PENDING
    assert retried.attempt == 1
    assert retried.run_after == 111
    assert store.claim_next("worker", now=110) is None

    assert store.claim_next("worker", now=111) is not None
    failed = store.fail(task.task_id, "worker", "boom again", now=112)
    assert failed.status == TaskStatus.FAILED
    assert failed.attempt == 2


def test_expired_lease_is_recovered(tmp_path):
    store = make_store(tmp_path)
    task = store.enqueue("project-1", "build", max_attempts=3, now=100)
    store.claim_next("dead-worker", lease_seconds=5, now=100)

    assert store.recover_stale(now=104) == 0
    assert store.recover_stale(now=105) == 1

    recovered = store.get(task.task_id)
    assert recovered is not None
    assert recovered.status == TaskStatus.PENDING
    assert recovered.attempt == 1
    assert recovered.claimed_by is None
    assert recovered.last_error == "worker lease expired"

    reclaimed = store.claim_next("new-worker", now=105)
    assert reclaimed is not None
    assert reclaimed.task_id == task.task_id


def test_executor_records_success_and_handler_failure(tmp_path):
    store = make_store(tmp_path)
    executor = TaskExecutor(store)
    executor.register("sum", lambda payload: payload["a"] + payload["b"])

    success = store.enqueue("p", "sum", {"a": 2, "b": 3})
    success_result = executor.run_once("worker")
    assert success_result is not None
    assert success_result.task_id == success.task_id
    assert success_result.status == TaskStatus.SUCCEEDED
    assert success_result.result == 5

    executor.register("explode", lambda payload: 1 / 0)
    failed_task = store.enqueue("p", "explode", max_attempts=1)
    failed_result = executor.run_once("worker")
    assert failed_result is not None
    assert failed_result.task_id == failed_task.task_id
    assert failed_result.status == TaskStatus.FAILED
    assert "ZeroDivisionError" in (failed_result.last_error or "")


def test_task_events_form_an_audit_trail(tmp_path):
    store = make_store(tmp_path)
    task = store.enqueue("p", "build", now=10)
    store.claim_next("worker", now=10, lease_seconds=5)
    store.heartbeat(task.task_id, "worker", now=11, lease_seconds=5)
    store.complete(task.task_id, "worker", now=12)

    assert [event["event_type"] for event in store.events(task.task_id)] == [
        "ENQUEUED",
        "CLAIMED",
        "HEARTBEAT",
        "SUCCEEDED",
    ]
