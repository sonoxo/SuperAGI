from superagi.xunihub.project_memory import ProjectMemoryStore, RepairStatus
from superagi.xunihub.repair_orchestrator import RepairCoordinator
from superagi.xunihub.task_runtime import TaskStore, TaskStatus


def _failed_task(tmp_path, project_id="project-1"):
    store = TaskStore(str(tmp_path / "tasks.db"))
    task = store.enqueue(project_id, "build", {"goal": "calculator"}, max_attempts=1)
    claimed = store.claim_next("worker-1")
    assert claimed is not None
    failed = store.fail(claimed.task_id, "worker-1", "browser verification failed")
    assert failed.status == TaskStatus.FAILED
    return failed


def test_successful_repair_promotes_verified_checkpoint(tmp_path):
    memory = ProjectMemoryStore(str(tmp_path / "memory.db"))
    coordinator = RepairCoordinator(memory)
    base = coordinator.checkpoint_verified(
        "project-1", {"files": {"app.js": "broken? no"}}, label="baseline"
    )
    failed = _failed_task(tmp_path)
    session = coordinator.start_for_failed_task(failed, max_attempts=2)

    calls = []

    def repair(context):
        calls.append(context["attempt"])
        return {"files": {"app.js": "fixed"}}

    def verify(snapshot):
        return snapshot["files"]["app.js"] == "fixed", None

    outcome = coordinator.run(session.session_id, repair, verify)

    assert outcome.succeeded
    assert outcome.session.status == RepairStatus.RESOLVED
    assert outcome.repaired_snapshot == {"files": {"app.js": "fixed"}}
    assert calls == [1]
    assert outcome.checkpoint_id is not None
    assert outcome.checkpoint_id != base.checkpoint_id
    latest = memory.latest_known_good("project-1")
    assert latest is not None
    assert latest.checkpoint_id == outcome.checkpoint_id
    assert memory.restore_checkpoint(outcome.checkpoint_id) == outcome.repaired_snapshot


def test_failed_verification_retries_with_persisted_error_context(tmp_path):
    memory = ProjectMemoryStore(str(tmp_path / "memory.db"))
    coordinator = RepairCoordinator(memory)
    coordinator.checkpoint_verified("project-1", {"version": 1})
    failed = _failed_task(tmp_path)
    session = coordinator.start_for_failed_task(failed, max_attempts=3)

    attempts = []

    def repair(context):
        attempts.append((context["attempt"], context["last_error"]))
        return {"version": context["attempt"] + 1}

    def verify(snapshot):
        if snapshot["version"] < 3:
            return False, "still failing interaction test"
        return True, None

    outcome = coordinator.run(session.session_id, repair, verify)

    assert outcome.succeeded
    assert attempts[0] == (1, None)
    assert attempts[1][0] == 2
    assert "still failing interaction test" in attempts[1][1]
    events = memory.repair_events(session.session_id)
    assert [event["event_type"] for event in events] == [
        "REPAIR_STARTED",
        "REPAIR_FAILED",
        "REPAIR_RESOLVED",
    ]


def test_exhausted_repairs_restore_known_good_snapshot(tmp_path):
    memory = ProjectMemoryStore(str(tmp_path / "memory.db"))
    coordinator = RepairCoordinator(memory)
    baseline = {"files": {"index.html": "known-good"}}
    base = coordinator.checkpoint_verified("project-1", baseline)
    failed = _failed_task(tmp_path)
    session = coordinator.start_for_failed_task(failed, max_attempts=2)

    restored = []

    def repair(context):
        return {"files": {"index.html": "still-broken-%s" % context["attempt"]}}

    def verify(snapshot):
        return False, "preview did not change"

    outcome = coordinator.run(
        session.session_id,
        repair,
        verify,
        restore_step=lambda snapshot: restored.append(snapshot),
    )

    assert outcome.rolled_back
    assert outcome.session.status == RepairStatus.ROLLED_BACK
    assert outcome.rollback_snapshot == baseline
    assert restored == [baseline]
    assert outcome.checkpoint_id == base.checkpoint_id
    events = memory.repair_events(session.session_id)
    assert [event["event_type"] for event in events] == [
        "REPAIR_STARTED",
        "REPAIR_FAILED",
        "REPAIR_FAILED",
        "ROLLED_BACK",
    ]


def test_failed_task_is_required_to_start_repair(tmp_path):
    memory = ProjectMemoryStore(str(tmp_path / "memory.db"))
    coordinator = RepairCoordinator(memory)
    tasks = TaskStore(str(tmp_path / "tasks.db"))
    pending = tasks.enqueue("project-1", "build")

    try:
        coordinator.start_for_failed_task(pending)
    except ValueError as exc:
        assert "terminal FAILED task" in str(exc)
    else:
        raise AssertionError("pending task incorrectly started a repair session")
