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
