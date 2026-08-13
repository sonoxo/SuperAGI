"""Durable project memory, checkpoints, and bounded repair state for XuniHub.

The primitives here are intentionally provider- and filesystem-agnostic. A checkpoint
stores a JSON-serializable project snapshot, verifies it with SHA-256 on restore, and
can be tagged as known-good. Repair sessions persist bounded retry/rollback decisions
without pretending that a model, IDE, or deployment actually executed a repair.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class RepairStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
    ROLLED_BACK = "ROLLED_BACK"
    EXHAUSTED = "EXHAUSTED"


@dataclass(frozen=True)
class CheckpointRecord:
    checkpoint_id: str
    project_id: str
    created_at: float
    label: Optional[str]
    parent_checkpoint_id: Optional[str]
    digest: str
    known_good: bool
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class RepairSession:
    session_id: str
    project_id: str
    failed_task_id: Optional[str]
    base_checkpoint_id: Optional[str]
    status: RepairStatus
    attempt: int
    max_attempts: int
    created_at: float
    updated_at: float
    last_error: Optional[str]


class CheckpointIntegrityError(RuntimeError):
    """Raised when persisted checkpoint content no longer matches its digest."""


class ProjectMemoryStore:
    """SQLite-backed project snapshots, memory entries, and repair audit state."""

    def __init__(self, database_path: str):
        self.database_path = database_path
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=20, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS xunihub_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    label TEXT,
                    parent_checkpoint_id TEXT,
                    snapshot_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    known_good INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(parent_checkpoint_id) REFERENCES xunihub_checkpoints(checkpoint_id)
                );
                CREATE INDEX IF NOT EXISTS ix_xunihub_checkpoints_project
                    ON xunihub_checkpoints(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS ix_xunihub_checkpoints_known_good
                    ON xunihub_checkpoints(project_id, known_good, created_at DESC);

                CREATE TABLE IF NOT EXISTS xunihub_memory_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    checkpoint_id TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(checkpoint_id) REFERENCES xunihub_checkpoints(checkpoint_id)
                );
                CREATE INDEX IF NOT EXISTS ix_xunihub_memory_project
                    ON xunihub_memory_entries(project_id, id DESC);

                CREATE TABLE IF NOT EXISTS xunihub_repair_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    failed_task_id TEXT,
                    base_checkpoint_id TEXT,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT,
                    FOREIGN KEY(base_checkpoint_id) REFERENCES xunihub_checkpoints(checkpoint_id)
                );
                CREATE INDEX IF NOT EXISTS ix_xunihub_repair_project
                    ON xunihub_repair_sessions(project_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS xunihub_repair_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    details_json TEXT,
                    FOREIGN KEY(session_id) REFERENCES xunihub_repair_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS ix_xunihub_repair_events_session
                    ON xunihub_repair_events(session_id, id);
                """
            )

    @staticmethod
    def _validate_project(project_id: str) -> str:
        value = str(project_id).strip()
        if not value:
            raise ValueError("project_id is required")
        return value

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def _digest(cls, snapshot: Dict[str, Any]) -> str:
        return hashlib.sha256(cls._canonical(snapshot).encode("utf-8")).hexdigest()

    @staticmethod
    def _checkpoint(row: sqlite3.Row) -> CheckpointRecord:
        return CheckpointRecord(
            checkpoint_id=row["checkpoint_id"],
            project_id=row["project_id"],
            created_at=float(row["created_at"]),
            label=row["label"],
            parent_checkpoint_id=row["parent_checkpoint_id"],
            digest=row["digest"],
            known_good=bool(row["known_good"]),
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _repair(row: sqlite3.Row) -> RepairSession:
        return RepairSession(
            session_id=row["session_id"],
            project_id=row["project_id"],
            failed_task_id=row["failed_task_id"],
            base_checkpoint_id=row["base_checkpoint_id"],
            status=RepairStatus(row["status"]),
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_error=row["last_error"],
        )

    @staticmethod
    def _repair_event(
        conn: sqlite3.Connection,
        session_id: str,
        event_type: str,
        occurred_at: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn.execute(
            "INSERT INTO xunihub_repair_events(session_id, event_type, occurred_at, details_json) VALUES (?, ?, ?, ?)",
            (
                session_id,
                event_type,
                occurred_at,
                json.dumps(details, sort_keys=True) if details is not None else None,
            ),
        )

    def create_checkpoint(
        self,
        project_id: str,
        snapshot: Dict[str, Any],
        *,
        label: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        parent_checkpoint_id: Optional[str] = None,
        known_good: bool = False,
        checkpoint_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> CheckpointRecord:
        project = self._validate_project(project_id)
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot must be a dictionary")
        timestamp = time.time() if now is None else float(now)
        identifier = checkpoint_id or str(uuid.uuid4())
        snapshot_json = self._canonical(snapshot)
        metadata_json = self._canonical(metadata or {})
        digest = self._digest(snapshot)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if parent_checkpoint_id is not None:
                parent = conn.execute(
                    "SELECT project_id FROM xunihub_checkpoints WHERE checkpoint_id = ?",
                    (parent_checkpoint_id,),
                ).fetchone()
                if parent is None:
                    conn.rollback()
                    raise KeyError("parent checkpoint does not exist")
                if parent["project_id"] != project:
                    conn.rollback()
                    raise ValueError("parent checkpoint belongs to a different project")
            conn.execute(
                """
                INSERT INTO xunihub_checkpoints(
                    checkpoint_id, project_id, created_at, label, parent_checkpoint_id,
                    snapshot_json, metadata_json, digest, known_good
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    project,
                    timestamp,
                    label,
                    parent_checkpoint_id,
                    snapshot_json,
                    metadata_json,
                    digest,
                    1 if known_good else 0,
                ),
            )
            row = conn.execute(
                "SELECT * FROM xunihub_checkpoints WHERE checkpoint_id = ?", (identifier,)
            ).fetchone()
            conn.commit()
        assert row is not None
        return self._checkpoint(row)

    def get_checkpoint(self, checkpoint_id: str) -> Optional[CheckpointRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM xunihub_checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)
            ).fetchone()
        return None if row is None else self._checkpoint(row)

    def list_checkpoints(self, project_id: str, *, limit: int = 100) -> List[CheckpointRecord]:
        project = self._validate_project(project_id)
        safe_limit = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM xunihub_checkpoints WHERE project_id = ? ORDER BY created_at DESC, checkpoint_id DESC LIMIT ?",
                (project, safe_limit),
            ).fetchall()
        return [self._checkpoint(row) for row in rows]

    def restore_checkpoint(self, checkpoint_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot_json, digest FROM xunihub_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            raise KeyError("checkpoint does not exist")
        snapshot = json.loads(row["snapshot_json"])
        if self._digest(snapshot) != row["digest"]:
            raise CheckpointIntegrityError("checkpoint digest mismatch")
        return snapshot

    def mark_known_good(self, checkpoint_id: str, *, exclusive: bool = True) -> CheckpointRecord:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT project_id FROM xunihub_checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise KeyError("checkpoint does not exist")
            if exclusive:
                conn.execute(
                    "UPDATE xunihub_checkpoints SET known_good = 0 WHERE project_id = ?",
                    (row["project_id"],),
                )
            conn.execute(
                "UPDATE xunihub_checkpoints SET known_good = 1 WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )
            updated = conn.execute(
                "SELECT * FROM xunihub_checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)
            ).fetchone()
            conn.commit()
        assert updated is not None
        return self._checkpoint(updated)

    def latest_known_good(self, project_id: str) -> Optional[CheckpointRecord]:
        project = self._validate_project(project_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM xunihub_checkpoints
                WHERE project_id = ? AND known_good = 1
                ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1
                """,
                (project,),
            ).fetchone()
        return None if row is None else self._checkpoint(row)

    def append_memory(
        self,
        project_id: str,
        kind: str,
        content: Any,
        *,
        checkpoint_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> int:
        project = self._validate_project(project_id)
        memory_kind = str(kind).strip()
        if not memory_kind:
            raise ValueError("kind is required")
        timestamp = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if checkpoint_id is not None:
                cp = conn.execute(
                    "SELECT project_id FROM xunihub_checkpoints WHERE checkpoint_id = ?",
                    (checkpoint_id,),
                ).fetchone()
                if cp is None:
                    conn.rollback()
                    raise KeyError("checkpoint does not exist")
                if cp["project_id"] != project:
                    conn.rollback()
                    raise ValueError("checkpoint belongs to a different project")
            cursor = conn.execute(
                "INSERT INTO xunihub_memory_entries(project_id, kind, content_json, checkpoint_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (project, memory_kind, self._canonical(content), checkpoint_id, timestamp),
            )
            memory_id = int(cursor.lastrowid)
            conn.commit()
        return memory_id

    def memories(self, project_id: str, *, kind: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        project = self._validate_project(project_id)
        safe_limit = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            if kind is None:
                rows = conn.execute(
                    "SELECT * FROM xunihub_memory_entries WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                    (project, safe_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM xunihub_memory_entries WHERE project_id = ? AND kind = ? ORDER BY id DESC LIMIT ?",
                    (project, kind, safe_limit),
                ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "project_id": row["project_id"],
                "kind": row["kind"],
                "content": json.loads(row["content_json"]),
                "checkpoint_id": row["checkpoint_id"],
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def start_repair(
        self,
        project_id: str,
        *,
        failed_task_id: Optional[str] = None,
        base_checkpoint_id: Optional[str] = None,
        max_attempts: int = 3,
        session_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> RepairSession:
        project = self._validate_project(project_id)
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_checkpoint_id is None:
            known_good = self.latest_known_good(project)
            base_checkpoint_id = None if known_good is None else known_good.checkpoint_id
        timestamp = time.time() if now is None else float(now)
        identifier = session_id or str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if base_checkpoint_id is not None:
                cp = conn.execute(
                    "SELECT project_id FROM xunihub_checkpoints WHERE checkpoint_id = ?",
                    (base_checkpoint_id,),
                ).fetchone()
                if cp is None:
                    conn.rollback()
                    raise KeyError("base checkpoint does not exist")
                if cp["project_id"] != project:
                    conn.rollback()
                    raise ValueError("base checkpoint belongs to a different project")
            conn.execute(
                """
                INSERT INTO xunihub_repair_sessions(
                    session_id, project_id, failed_task_id, base_checkpoint_id, status,
                    attempt, max_attempts, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, NULL)
                """,
                (
                    identifier,
                    project,
                    failed_task_id,
                    base_checkpoint_id,
                    RepairStatus.ACTIVE.value,
                    int(max_attempts),
                    timestamp,
                    timestamp,
                ),
            )
            self._repair_event(
                conn,
                identifier,
                "REPAIR_STARTED",
                timestamp,
                {"base_checkpoint_id": base_checkpoint_id, "max_attempts": int(max_attempts)},
            )
            row = conn.execute(
                "SELECT * FROM xunihub_repair_sessions WHERE session_id = ?", (identifier,)
            ).fetchone()
            conn.commit()
        assert row is not None
        return self._repair(row)

    def get_repair(self, session_id: str) -> Optional[RepairSession]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM xunihub_repair_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return None if row is None else self._repair(row)

    def record_repair_failure(
        self, session_id: str, error: str, *, now: Optional[float] = None
    ) -> RepairSession:
        timestamp = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM xunihub_repair_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if current is None:
                conn.rollback()
                raise KeyError("repair session does not exist")
            if current["status"] != RepairStatus.ACTIVE.value:
                conn.rollback()
                raise RuntimeError("repair session is not active")
            attempt = int(current["attempt"]) + 1
            terminal = attempt >= int(current["max_attempts"])
            if terminal:
                status = (
                    RepairStatus.ROLLBACK_REQUIRED
                    if current["base_checkpoint_id"] is not None
                    else RepairStatus.EXHAUSTED
                )
            else:
                status = RepairStatus.ACTIVE
            conn.execute(
                "UPDATE xunihub_repair_sessions SET status = ?, attempt = ?, updated_at = ?, last_error = ? WHERE session_id = ?",
                (status.value, attempt, timestamp, str(error), session_id),
            )
            self._repair_event(
                conn,
                session_id,
                "REPAIR_FAILED",
                timestamp,
                {"attempt": attempt, "error": str(error), "next_status": status.value},
            )
            row = conn.execute(
                "SELECT * FROM xunihub_repair_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            conn.commit()
        assert row is not None
        return self._repair(row)

    def resolve_repair(
        self,
        session_id: str,
        *,
        checkpoint_id: Optional[str] = None,
        mark_known_good: bool = True,
        now: Optional[float] = None,
    ) -> RepairSession:
        timestamp = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM xunihub_repair_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if current is None:
                conn.rollback()
                raise KeyError("repair session does not exist")
            if current["status"] != RepairStatus.ACTIVE.value:
                conn.rollback()
                raise RuntimeError("repair session is not active")
            if checkpoint_id is not None:
                cp = conn.execute(
                    "SELECT project_id FROM xunihub_checkpoints WHERE checkpoint_id = ?",
                    (checkpoint_id,),
                ).fetchone()
                if cp is None:
                    conn.rollback()
                    raise KeyError("checkpoint does not exist")
                if cp["project_id"] != current["project_id"]:
                    conn.rollback()
                    raise ValueError("checkpoint belongs to a different project")
                if mark_known_good:
                    conn.execute(
                        "UPDATE xunihub_checkpoints SET known_good = 0 WHERE project_id = ?",
                        (current["project_id"],),
                    )
                    conn.execute(
                        "UPDATE xunihub_checkpoints SET known_good = 1 WHERE checkpoint_id = ?",
                        (checkpoint_id,),
                    )
            conn.execute(
                "UPDATE xunihub_repair_sessions SET status = ?, updated_at = ?, last_error = NULL WHERE session_id = ?",
                (RepairStatus.RESOLVED.value, timestamp, session_id),
            )
            self._repair_event(
                conn,
                session_id,
                "REPAIR_RESOLVED",
                timestamp,
                {"checkpoint_id": checkpoint_id},
            )
            row = conn.execute(
                "SELECT * FROM xunihub_repair_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            conn.commit()
        assert row is not None
        return self._repair(row)

    def rollback_repair(self, session_id: str, *, now: Optional[float] = None) -> Dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM xunihub_repair_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if current is None:
                conn.rollback()
                raise KeyError("repair session does not exist")
            if current["status"] != RepairStatus.ROLLBACK_REQUIRED.value:
                conn.rollback()
                raise RuntimeError("repair session does not require rollback")
            checkpoint_id = current["base_checkpoint_id"]
            if checkpoint_id is None:
                conn.rollback()
                raise RuntimeError("repair session has no rollback checkpoint")
            cp = conn.execute(
                "SELECT snapshot_json, digest FROM xunihub_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
            if cp is None:
                conn.rollback()
                raise KeyError("rollback checkpoint does not exist")
            snapshot = json.loads(cp["snapshot_json"])
            if self._digest(snapshot) != cp["digest"]:
                conn.rollback()
                raise CheckpointIntegrityError("checkpoint digest mismatch")
            conn.execute(
                "UPDATE xunihub_repair_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                (RepairStatus.ROLLED_BACK.value, timestamp, session_id),
            )
            self._repair_event(
                conn,
                session_id,
                "ROLLED_BACK",
                timestamp,
                {"checkpoint_id": checkpoint_id},
            )
            conn.commit()
        return snapshot

    def repair_events(self, session_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_type, occurred_at, details_json FROM xunihub_repair_events WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "occurred_at": float(row["occurred_at"]),
                "details": json.loads(row["details_json"]) if row["details_json"] else None,
            }
            for row in rows
        ]
