"""Repair orchestration that bridges XuniHub tasks and project memory.

This module is deliberately provider/runtime agnostic. It can coordinate a bounded
repair loop using caller-supplied repair and verification functions, persist every
attempt, promote verified snapshots, and return a verified known-good snapshot for
rollback. It does not claim that a model, filesystem, IDE, or deployment performed
work unless the caller's adapters actually do so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from .project_memory import ProjectMemoryStore, RepairSession, RepairStatus
from .task_runtime import TaskRecord, TaskStatus


RepairStep = Callable[[Dict[str, Any]], Dict[str, Any]]
VerifyStep = Callable[[Dict[str, Any]], Tuple[bool, Optional[str]]]
RestoreStep = Callable[[Dict[str, Any]], None]


@dataclass(frozen=True)
class RepairOutcome:
    session: RepairSession
    repaired_snapshot: Optional[Dict[str, Any]]
    rollback_snapshot: Optional[Dict[str, Any]]
    checkpoint_id: Optional[str]

    @property
    def succeeded(self) -> bool:
        return self.session.status == RepairStatus.RESOLVED

    @property
    def rolled_back(self) -> bool:
        return self.session.status == RepairStatus.ROLLED_BACK


class RepairCoordinator:
    """Bounded, durable repair loop around ProjectMemoryStore.

    The coordinator requires explicit adapters for repair, verification, and optional
    restore. That keeps the durable core honest while allowing the Xuni agent/model,
    sandbox runtime, or IDE layer to be connected later without changing persistence
    semantics.
    """

    def __init__(self, memory: ProjectMemoryStore):
        self.memory = memory

    def checkpoint_verified(
        self,
        project_id: str,
        snapshot: Dict[str, Any],
        *,
        label: str = "verified",
        metadata: Optional[Dict[str, Any]] = None,
        parent_checkpoint_id: Optional[str] = None,
    ):
        checkpoint = self.memory.create_checkpoint(
            project_id,
            snapshot,
            label=label,
            metadata=metadata,
            parent_checkpoint_id=parent_checkpoint_id,
            known_good=True,
        )
        self.memory.mark_known_good(checkpoint.checkpoint_id)
        self.memory.append_memory(
            project_id,
            "verification",
            {"status": "passed", "checkpoint_id": checkpoint.checkpoint_id},
            checkpoint_id=checkpoint.checkpoint_id,
        )
        return checkpoint

    def start_for_failed_task(
        self,
        task: TaskRecord,
        *,
        max_attempts: int = 3,
    ) -> RepairSession:
        if task.status != TaskStatus.FAILED:
            raise ValueError("repair can only start from a terminal FAILED task")
        session = self.memory.start_repair(
            task.project_id,
            failed_task_id=task.task_id,
            max_attempts=max_attempts,
        )
        self.memory.append_memory(
            task.project_id,
            "task_failure",
            {
                "task_id": task.task_id,
                "task_type": task.task_type,
                "attempt": task.attempt,
                "error": task.last_error,
                "repair_session_id": session.session_id,
            },
            checkpoint_id=session.base_checkpoint_id,
        )
        return session

    def run(
        self,
        session_id: str,
        repair_step: RepairStep,
        verify_step: VerifyStep,
        *,
        restore_step: Optional[RestoreStep] = None,
        label: str = "repair-verified",
    ) -> RepairOutcome:
        session = self.memory.get_repair(session_id)
        if session is None:
            raise KeyError("repair session does not exist")
        if session.status != RepairStatus.ACTIVE:
            raise RuntimeError("repair session is not active")

        base_snapshot = None
        if session.base_checkpoint_id is not None:
            base_snapshot = self.memory.restore_checkpoint(session.base_checkpoint_id)

        current_snapshot = base_snapshot or {}
        while session.status == RepairStatus.ACTIVE:
            context = {
                "project_id": session.project_id,
                "failed_task_id": session.failed_task_id,
                "repair_session_id": session.session_id,
                "attempt": session.attempt + 1,
                "max_attempts": session.max_attempts,
                "last_error": session.last_error,
                "base_snapshot": base_snapshot,
                "current_snapshot": current_snapshot,
            }
            try:
                candidate = repair_step(context)
                if not isinstance(candidate, dict):
                    raise TypeError("repair_step must return a dictionary snapshot")
                passed, verification_error = verify_step(candidate)
                if not passed:
                    raise RuntimeError(verification_error or "verification failed")
            except Exception as exc:
                session = self.memory.record_repair_failure(
                    session.session_id, "%s: %s" % (exc.__class__.__name__, exc)
                )
                self.memory.append_memory(
                    session.project_id,
                    "repair_attempt",
                    {
                        "session_id": session.session_id,
                        "attempt": session.attempt,
                        "status": session.status.value,
                        "error": session.last_error,
                    },
                    checkpoint_id=session.base_checkpoint_id,
                )
                if session.status == RepairStatus.ACTIVE:
                    continue
                break

            checkpoint = self.memory.create_checkpoint(
                session.project_id,
                candidate,
                label=label,
                parent_checkpoint_id=session.base_checkpoint_id,
                metadata={
                    "repair_session_id": session.session_id,
                    "failed_task_id": session.failed_task_id,
                    "verified": True,
                },
            )
            session = self.memory.resolve_repair(
                session.session_id,
                checkpoint_id=checkpoint.checkpoint_id,
                mark_known_good=True,
            )
            self.memory.append_memory(
                session.project_id,
                "repair_result",
                {
                    "session_id": session.session_id,
                    "status": session.status.value,
                    "checkpoint_id": checkpoint.checkpoint_id,
                },
                checkpoint_id=checkpoint.checkpoint_id,
            )
            return RepairOutcome(
                session=session,
                repaired_snapshot=candidate,
                rollback_snapshot=None,
                checkpoint_id=checkpoint.checkpoint_id,
            )

        if session.status == RepairStatus.ROLLBACK_REQUIRED:
            rollback = self.memory.rollback_repair(session.session_id)
            if restore_step is not None:
                restore_step(rollback)
            final_session = self.memory.get_repair(session.session_id)
            assert final_session is not None
            self.memory.append_memory(
                final_session.project_id,
                "repair_result",
                {
                    "session_id": final_session.session_id,
                    "status": final_session.status.value,
                    "checkpoint_id": final_session.base_checkpoint_id,
                },
                checkpoint_id=final_session.base_checkpoint_id,
            )
            return RepairOutcome(
                session=final_session,
                repaired_snapshot=None,
                rollback_snapshot=rollback,
                checkpoint_id=final_session.base_checkpoint_id,
            )

        return RepairOutcome(
            session=session,
            repaired_snapshot=None,
            rollback_snapshot=None,
            checkpoint_id=None,
        )
