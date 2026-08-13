"""Shared XuniHub core smoke checks loaded by the focused CI test target."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def verify_project_memory_checkpoint_cycle(tmp_path_factory):
    from superagi.xunihub.project_memory import ProjectMemoryStore, RepairStatus

    root = tmp_path_factory.mktemp("xunihub-memory-smoke")
    store = ProjectMemoryStore(str(root / "memory.sqlite3"))
    baseline = {"files": {"app.py": "print('known good')"}}
    checkpoint = store.create_checkpoint("ci-project", baseline, known_good=True, now=100)

    assert store.restore_checkpoint(checkpoint.checkpoint_id) == baseline
    session = store.start_repair("ci-project", max_attempts=2, now=101)
    assert session.base_checkpoint_id == checkpoint.checkpoint_id
    assert store.record_repair_failure(
        session.session_id, "attempt one", now=102
    ).status == RepairStatus.ACTIVE
    assert store.record_repair_failure(
        session.session_id, "attempt two", now=103
    ).status == RepairStatus.ROLLBACK_REQUIRED
    assert store.rollback_repair(session.session_id, now=104) == baseline
    assert store.get_repair(session.session_id).status == RepairStatus.ROLLED_BACK


@pytest.fixture(scope="session", autouse=True)
def verify_repair_orchestrator_cycle(tmp_path_factory):
    from superagi.xunihub.project_memory import ProjectMemoryStore, RepairStatus
    from superagi.xunihub.repair_orchestrator import RepairCoordinator
    from superagi.xunihub.task_runtime import TaskStore, TaskStatus

    root = tmp_path_factory.mktemp("xunihub-repair-smoke")
    memory = ProjectMemoryStore(str(root / "memory.sqlite3"))
    tasks = TaskStore(str(root / "tasks.sqlite3"))
    coordinator = RepairCoordinator(memory)

    baseline = {"files": {"app.py": "print('stable')"}}
    base = coordinator.checkpoint_verified("ci-repair-project", baseline, label="ci-baseline")
    task = tasks.enqueue("ci-repair-project", "browser-test", max_attempts=1)
    claimed = tasks.claim_next("ci-worker")
    assert claimed is not None
    failed = tasks.fail(claimed.task_id, "ci-worker", "interaction failed")
    assert failed.status == TaskStatus.FAILED

    session = coordinator.start_for_failed_task(failed, max_attempts=2)
    restored = []
    outcome = coordinator.run(
        session.session_id,
        lambda context: {"files": {"app.py": "print('still broken')"}},
        lambda snapshot: (False, "verification failed"),
        restore_step=lambda snapshot: restored.append(snapshot),
    )

    assert outcome.session.status == RepairStatus.ROLLED_BACK
    assert outcome.checkpoint_id == base.checkpoint_id
    assert outcome.rollback_snapshot == baseline
    assert restored == [baseline]
