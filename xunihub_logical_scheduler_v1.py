"""Bounded scheduler for a 1,000,000-worker logical address space.

Workers are identifiers, not processes. The scheduler leases only a small bounded
active set and leaves every other logical worker dormant.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List

TOTAL_LOGICAL_WORKERS = 1_000_000


@dataclass(frozen=True)
class WorkItem:
    task_id: str
    priority: int = 50


@dataclass(frozen=True)
class WorkerLease:
    worker_id: int
    task_id: str


class ElasticLogicalScheduler:
    def __init__(self, max_active: int = 64) -> None:
        if not isinstance(max_active, int) or isinstance(max_active, bool):
            raise ValueError("max_active must be an integer")
        if max_active <= 0 or max_active > TOTAL_LOGICAL_WORKERS:
            raise ValueError("max_active is outside the logical worker address space")
        self.max_active = max_active
        self._active: Dict[int, WorkerLease] = {}
        self._next_worker = 0

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def dormant_count(self) -> int:
        return TOTAL_LOGICAL_WORKERS - self.active_count

    def lease(self, work: Iterable[WorkItem]) -> List[WorkerLease]:
        available = self.max_active - self.active_count
        ordered = sorted(work, key=lambda item: (-item.priority, item.task_id))
        leases: List[WorkerLease] = []
        for item in ordered[:available]:
            worker_id = self._allocate_worker_id()
            lease = WorkerLease(worker_id=worker_id, task_id=item.task_id)
            self._active[worker_id] = lease
            leases.append(lease)
        return leases

    def release(self, worker_id: int) -> bool:
        return self._active.pop(worker_id, None) is not None

    def release_all(self) -> None:
        self._active.clear()

    def _allocate_worker_id(self) -> int:
        for _ in range(TOTAL_LOGICAL_WORKERS):
            worker_id = self._next_worker
            self._next_worker = (self._next_worker + 1) % TOTAL_LOGICAL_WORKERS
            if worker_id not in self._active:
                return worker_id
        raise RuntimeError("logical worker address space exhausted")
