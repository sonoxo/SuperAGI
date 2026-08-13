import sqlite3
from pathlib import Path

import pytest

from superagi.xunihub.project_memory import (
    CheckpointIntegrityError,
    ProjectMemoryStore,
    RepairStatus,
)


def make_store(tmp_path: Path) -> ProjectMemoryStore:
    return ProjectMemoryStore(str(tmp_path / "xunihub-memory.sqlite3"))


def test_checkpoint_persists_and_restores_with_digest(tmp_path):
    store = make_store(tmp_path)
    snapshot = {
        "files": {"src/app.py": "print('hello')", "README.md": "# demo"},
        "runtime": {"entrypoint": "src/app.py"},
    }
    checkpoint = store.create_checkpoint(
        "project-1",
        snapshot,
        label="initial",
        metadata={"reason": "pre-build"},
        known_good=True,
        now=100,
    )

    reopened = make_store(tmp_path)
    persisted = reopened.get_checkpoint(checkpoint.checkpoint_id)

    assert persisted is not None
    assert persisted.project_id == "project-1"
    assert persisted.known_good is True
    assert persisted.metadata == {"reason": "pre-build"}
    assert reopened.restore_checkpoint(checkpoint.checkpoint_id) == snapshot
    assert reopened.latest_known_good("project-1") == persisted


def test_checkpoint_detects_tampering(tmp_path):
    store = make_store(tmp_path)
    checkpoint = store.create_checkpoint("p", {"files": {"a.txt": "safe"}})

    with sqlite3.connect(store.database_path) as conn:
        conn.execute(
            "UPDATE xunihub_checkpoints SET snapshot_json = ? WHERE checkpoint_id = ?",
            ('{"files":{"a.txt":"tampered"}}', checkpoint.checkpoint_id),
        )

    with pytest.raises(CheckpointIntegrityError):
        store.restore_checkpoint(checkpoint.checkpoint_id)


def test_parent_checkpoint_must_belong_to_same_project(tmp_path):
    store = make_store(tmp_path)
    parent = store.create_checkpoint("p1", {"v": 1})

    with pytest.raises(ValueError):
        store.create_checkpoint("p2", {"v": 2}, parent_checkpoint_id=parent.checkpoint_id)

    child = store.create_checkpoint(
        "p1", {"v": 2}, parent_checkpoint_id=parent.checkpoint_id
    )
    assert child.parent_checkpoint_id == parent.checkpoint_id


def test_known_good_selection_is_explicit_and_exclusive(tmp_path):
    store = make_store(tmp_path)
    first = store.create_checkpoint("p", {"v": 1}, known_good=True, now=10)
    second = store.create_checkpoint("p", {"v": 2}, now=20)

    assert store.latest_known_good("p").checkpoint_id == first.checkpoint_id

    marked = store.mark_known_good(second.checkpoint_id)
    assert marked.known_good is True
    assert store.latest_known_good("p").checkpoint_id == second.checkpoint_id
    by_id = {cp.checkpoint_id: cp for cp in store.list_checkpoints("p")}
    assert by_id[first.checkpoint_id].known_good is False


def test_project_memory_is_persistent_and_checkpoint_scoped(tmp_path):
    store = make_store(tmp_path)
    checkpoint = store.create_checkpoint("p", {"files": {}})
    memory_id = store.append_memory(
        "p",
        "decision",
        {"text": "Use the durable task runtime"},
        checkpoint_id=checkpoint.checkpoint_id,
        now=50,
    )
    store.append_memory("p", "observation", {"tests": "passed"}, now=51)

    reopened = make_store(tmp_path)
    decisions = reopened.memories("p", kind="decision")
    assert decisions == [
        {
            "id": memory_id,
            "project_id": "p",
            "kind": "decision",
            "content": {"text": "Use the durable task runtime"},
            "checkpoint_id": checkpoint.checkpoint_id,
            "created_at": 50.0,
        }
    ]
    assert len(reopened.memories("p")) == 2


def test_repair_is_bounded_and_rolls_back_to_known_good(tmp_path):
    store = make_store(tmp_path)
    base_snapshot = {"files": {"app.py": "working"}}
    base = store.create_checkpoint("p", base_snapshot, known_good=True, now=10)
    repair = store.start_repair(
        "p", failed_task_id="task-123", max_attempts=2, now=20
    )

    assert repair.base_checkpoint_id == base.checkpoint_id
    first = store.record_repair_failure(repair.session_id, "tests still fail", now=21)
    assert first.status == RepairStatus.ACTIVE
    assert first.attempt == 1

    second = store.record_repair_failure(repair.session_id, "same failure", now=22)
    assert second.status == RepairStatus.ROLLBACK_REQUIRED
    assert second.attempt == 2

    restored = store.rollback_repair(repair.session_id, now=23)
    assert restored == base_snapshot
    assert store.get_repair(repair.session_id).status == RepairStatus.ROLLED_BACK
    assert [event["event_type"] for event in store.repair_events(repair.session_id)] == [
        "REPAIR_STARTED",
        "REPAIR_FAILED",
        "REPAIR_FAILED",
        "ROLLED_BACK",
    ]


def test_repair_without_checkpoint_exhausts_instead_of_faking_rollback(tmp_path):
    store = make_store(tmp_path)
    repair = store.start_repair("new-project", max_attempts=1, now=10)

    failed = store.record_repair_failure(repair.session_id, "no baseline", now=11)
    assert failed.status == RepairStatus.EXHAUSTED
    assert failed.base_checkpoint_id is None

    with pytest.raises(RuntimeError):
        store.rollback_repair(repair.session_id)


def test_successful_repair_can_promote_new_known_good_checkpoint(tmp_path):
    store = make_store(tmp_path)
    old = store.create_checkpoint("p", {"v": 1}, known_good=True, now=10)
    repair = store.start_repair("p", max_attempts=3, now=20)
    new = store.create_checkpoint(
        "p", {"v": 2}, parent_checkpoint_id=old.checkpoint_id, now=21
    )

    resolved = store.resolve_repair(
        repair.session_id, checkpoint_id=new.checkpoint_id, now=22
    )
    assert resolved.status == RepairStatus.RESOLVED
    assert store.latest_known_good("p").checkpoint_id == new.checkpoint_id

    old_after = store.get_checkpoint(old.checkpoint_id)
    assert old_after is not None
    assert old_after.known_good is False
