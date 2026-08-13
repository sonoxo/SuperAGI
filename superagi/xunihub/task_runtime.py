"""Durable single-node task execution primitives for XuniHub.

This module deliberately provides SQLite-backed durability and lease-based worker
coordination without claiming distributed-queue guarantees. It has no provider
or runtime dependencies, so the core can be exercised safely in CI.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    project_id: str
    task_type: str
    payload: Dict[str, Any]
    status: TaskStatus
    attempt: int
    max_attempts: int
    created_at: float
    updated_at: float
    run_after: float
    lease_expires_at: Optional[float]
    claimed_by: Optional[str]
    idempotency_key: Optional[str]
    result: Optional[Any]
    last_error: Optional[str]


class TaskStore:
    """SQLite task store with atomic claims, leases, retries, and idempotency."""

    def __init__(self, database_path: str):
        self.database_path = database_path
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
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
                CREATE TABLE IF NOT EXISTS xunihub_tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    run_after REAL NOT NULL,
                    lease_expires_at REAL,
                    claimed_by TEXT,
                    idempotency_key TEXT,
                    result_json TEXT,
                    last_error TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_xunihub_tasks_idempotency
                    ON xunihub_tasks(project_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;

                CREATE INDEX IF NOT EXISTS ix_xunihub_tasks_claim
                    ON xunihub_tasks(status, run_after, created_at);

                CREATE INDEX IF NOT EXISTS ix_xunihub_tasks_lease
                    ON xunihub_tasks(status, lease_expires_at);

                CREATE TABLE IF NOT EXISTS xunihub_task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    details_json TEXT,
                    FOREIGN KEY(task_id) REFERENCES xunihub_tasks(task_id)
                );

                CREATE INDEX IF NOT EXISTS ix_xunihub_task_events_task
                    ON xunihub_task_events(task_id, id);
                """
            )

    @staticmethod
    def _decode_json(value: Optional[str]) -> Optional[Any]:
        if value is None:
            return None
        return json.loads(value)

    @classmethod
    def _record(cls, row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            project_id=row["project_id"],
            task_type=row["task_type"],
            payload=cls._decode_json(row["payload_json"]) or {},
            status=TaskStatus(row["status"]),
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            run_after=float(row["run_after"]),
            lease_expires_at=row["lease_expires_at"],
            claimed_by=row["claimed_by"],
            idempotency_key=row["idempotency_key"],
            result=cls._decode_json(row["result_json"]),
            last_error=row["last_error"],
        )

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        task_id: str,
        event_type: str,
        now: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn.execute(
            "INSERT INTO xunihub_task_events(task_id, event_type, occurred_at, details_json) VALUES (?, ?, ?, ?)",
            (task_id, event_type, now, json.dumps(details, sort_keys=True) if details is not None else None),
        )

    def enqueue(
        self,
        project_id: str,
        task_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        max_attempts: int = 3,
        run_after: Optional[float] = None,
        idempotency_key: Optional[str] = None,
        task_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> TaskRecord:
        if not project_id.strip():
            raise ValueError("project_id is required")
        if not task_type.strip():
            raise ValueError("task_type is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        timestamp = time.time() if now is None else float(now)
        due = timestamp if run_after is None else float(run_after)
        identifier = task_id or str(uuid.uuid4())
        body = payload or {}

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = conn.execute(
                    "SELECT * FROM xunihub_tasks WHERE project_id = ? AND idempotency_key = ?",
                    (project_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    conn.commit()
                    return self._record(existing)

            conn.execute(
                """
                INSERT INTO xunihub_tasks(
                    task_id, project_id, task_type, payload_json, status, attempt,
                    max_attempts, created_at, updated_at, run_after, lease_expires_at,
                    claimed_by, idempotency_key, result_json, last_error
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL)
                """,
                (
                    identifier,
                    project_id,
                    task_type,
                    json.dumps(body, sort_keys=True),
                    TaskStatus.PENDING.value,
                    int(max_attempts),
                    timestamp,
                    timestamp,
                    due,
                    idempotency_key,
                ),
            )
            self._event(conn, identifier, "ENQUEUED", timestamp)
            row = conn.execute("SELECT * FROM xunihub_tasks WHERE task_id = ?", (identifier,)).fetchone()
            conn.commit()
        assert row is not None
        return self._record(row)

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM xunihub_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return None if row is None else self._record(row)

    def list_recent(self, *, project_id: Optional[str] = None, limit: int = 100) -> List[TaskRecord]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            if project_id is None:
                rows = conn.execute(
                    "SELECT * FROM xunihub_tasks ORDER BY created_at DESC LIMIT ?", (safe_limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM xunihub_tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                    (project_id, safe_limit),
                ).fetchall()
        return [self._record(row) for row in rows]

    def events(self, task_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_type, occurred_at, details_json FROM xunihub_task_events WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "occurred_at": float(row["occurred_at"]),
                "details": self._decode_json(row["details_json"]),
            }
            for row in rows
        ]

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: Optional[float] = None,
    ) -> Optional[TaskRecord]:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")

        timestamp = time.time() if now is None else float(now)
        lease_until = timestamp + int(lease_seconds)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT task_id FROM xunihub_tasks
                WHERE status = ? AND run_after <= ?
                ORDER BY run_after ASC, created_at ASC, task_id ASC
                LIMIT 1
                """,
                (TaskStatus.PENDING.value, timestamp),
            ).fetchone()
            if row is None:
                conn.commit()
                return None

            task_id = row["task_id"]
            updated = conn.execute(
                """
                UPDATE xunihub_tasks
                SET status = ?, claimed_by = ?, lease_expires_at = ?, updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (TaskStatus.RUNNING.value, worker_id, lease_until, timestamp, task_id, TaskStatus.PENDING.value),
            )
            if updated.rowcount != 1:
                conn.rollback()
                return None
            self._event(conn, task_id, "CLAIMED", timestamp, {"worker_id": worker_id, "lease_expires_at": lease_until})
            claimed = conn.execute("SELECT * FROM xunihub_tasks WHERE task_id = ?", (task_id,)).fetchone()
            conn.commit()
        assert claimed is not None
        return self._record(claimed)

    def heartbeat(
        self,
        task_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: Optional[float] = None,
    ) -> TaskRecord:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        timestamp = time.time() if now is None else float(now)
        lease_until = timestamp + int(lease_seconds)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                """
                UPDATE xunihub_tasks SET lease_expires_at = ?, updated_at = ?
                WHERE task_id = ? AND status = ? AND claimed_by = ?
                """,
                (lease_until, timestamp, task_id, TaskStatus.RUNNING.value, worker_id),
            )
            if updated.rowcount != 1:
                conn.rollback()
                raise RuntimeError("task is not actively leased by this worker")
            self._event(conn, task_id, "HEARTBEAT", timestamp, {"lease_expires_at": lease_until})
            row = conn.execute("SELECT * FROM xunihub_tasks WHERE task_id = ?", (task_id,)).fetchone()
            conn.commit()
        assert row is not None
        return self._record(row)

    def complete(
        self,
        task_id: str,
        worker_id: str,
        result: Optional[Any] = None,
        *,
        now: Optional[float] = None,
    ) -> TaskRecord:
        timestamp = time.time() if now is None else float(now)
        result_json = json.dumps(result, sort_keys=True) if result is not None else None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                """
                UPDATE xunihub_tasks
                SET status = ?, result_json = ?, last_error = NULL,
                    claimed_by = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE task_id = ? AND status = ? AND claimed_by = ?
                """,
                (TaskStatus.SUCCEEDED.value, result_json, timestamp, task_id, TaskStatus.RUNNING.value, worker_id),
            )
            if updated.rowcount != 1:
                conn.rollback()
                raise RuntimeError("task is not actively leased by this worker")
            self._event(conn, task_id, "SUCCEEDED", timestamp)
            row = conn.execute("SELECT * FROM xunihub_tasks WHERE task_id = ?", (task_id,)).fetchone()
            conn.commit()
        assert row is not None
        return self._record(row)

    def fail(
        self,
        task_id: str,
        worker_id: str,
        error: str,
        *,
        retry_delay_seconds: float = 0,
        now: Optional[float] = None,
    ) -> TaskRecord:
        timestamp = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM xunihub_tasks WHERE task_id = ? AND status = ? AND claimed_by = ?",
                (task_id, TaskStatus.RUNNING.value, worker_id),
            ).fetchone()
            if current is None:
                conn.rollback()
                raise RuntimeError("task is not actively leased by this worker")

            next_attempt = int(current["attempt"]) + 1
            terminal = next_attempt >= int(current["max_attempts"])
            next_status = TaskStatus.FAILED if terminal else TaskStatus.PENDING
            next_run_after = timestamp if terminal else timestamp + max(0.0, float(retry_delay_seconds))
            conn.execute(
                """
                UPDATE xunihub_tasks
                SET status = ?, attempt = ?, run_after = ?, last_error = ?,
                    claimed_by = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (next_status.value, next_attempt, next_run_after, str(error), timestamp, task_id),
            )
            self._event(
                conn,
                task_id,
                "FAILED" if terminal else "RETRY_SCHEDULED",
                timestamp,
                {"attempt": next_attempt, "error": str(error)},
            )
            row = conn.execute("SELECT * FROM xunihub_tasks WHERE task_id = ?", (task_id,)).fetchone()
            conn.commit()
        assert row is not None
        return self._record(row)

    def recover_stale(self, *, now: Optional[float] = None) -> int:
        timestamp = time.time() if now is None else float(now)
        recovered = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            stale_rows = conn.execute(
                """
                SELECT * FROM xunihub_tasks
                WHERE status = ? AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
                ORDER BY lease_expires_at ASC
                """,
                (TaskStatus.RUNNING.value, timestamp),
            ).fetchall()
            for row in stale_rows:
                next_attempt = int(row["attempt"]) + 1
                terminal = next_attempt >= int(row["max_attempts"])
                next_status = TaskStatus.FAILED if terminal else TaskStatus.PENDING
                conn.execute(
                    """
                    UPDATE xunihub_tasks
                    SET status = ?, attempt = ?, claimed_by = NULL,
                        lease_expires_at = NULL, run_after = ?, updated_at = ?, last_error = ?
                    WHERE task_id = ?
                    """,
                    (
                        next_status.value,
                        next_attempt,
                        timestamp,
                        timestamp,
                        "worker lease expired",
                        row["task_id"],
                    ),
                )
                self._event(
                    conn,
                    row["task_id"],
                    "LEASE_EXPIRED",
                    timestamp,
                    {"attempt": next_attempt, "terminal": terminal},
                )
                recovered += 1
            conn.commit()
        return recovered


TaskHandler = Callable[[Dict[str, Any]], Any]


class TaskExecutor:
    """Deterministic executor around TaskStore with explicitly registered handlers."""

    def __init__(self, store: TaskStore):
        self.store = store
        self._handlers: Dict[str, TaskHandler] = {}

    def register(self, task_type: str, handler: TaskHandler) -> None:
        if not task_type.strip():
            raise ValueError("task_type is required")
        self._handlers[task_type] = handler

    def run_once(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        retry_delay_seconds: float = 0,
    ) -> Optional[TaskRecord]:
        self.store.recover_stale()
        task = self.store.claim_next(worker_id, lease_seconds=lease_seconds)
        if task is None:
            return None

        handler = self._handlers.get(task.task_type)
        if handler is None:
            return self.store.fail(
                task.task_id,
                worker_id,
                "no handler registered for task type: %s" % task.task_type,
                retry_delay_seconds=retry_delay_seconds,
            )

        try:
            result = handler(task.payload)
        except Exception as exc:
            return self.store.fail(
                task.task_id,
                worker_id,
                "%s: %s" % (exc.__class__.__name__, exc),
                retry_delay_seconds=retry_delay_seconds,
            )
        return self.store.complete(task.task_id, worker_id, result)
