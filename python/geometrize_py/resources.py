"""Nonblocking reservations for active CPU and memory use.

Each lease fixes its thread count for the batch. Cache memory is accounted for
separately; this budget covers transient fitting and export allocations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import Lock

DEFAULT_WORKER_BUDGET = max(1, min(8, os.cpu_count() or 1))
DEFAULT_ACTIVE_MEMORY_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class ResourceLease:
    workers: int
    memory_bytes: int
    _token: object = field(default_factory=object, repr=False)


class WorkBudget:
    def __init__(self, workers: int, memory_bytes: int) -> None:
        if workers < 1 or memory_bytes < 1:
            raise ValueError("Worker and active memory budgets must be positive")
        self.workers = workers
        self.memory_bytes = memory_bytes
        self._used_workers = 0
        self._used_memory = 0
        self._lock = Lock()
        self._leases: set[ResourceLease] = set()

    def try_reserve(self, workers: int, memory_bytes: int) -> ResourceLease | None:
        if workers < 1 or memory_bytes < 1:
            raise ValueError("Resource reservations must be positive")
        with self._lock:
            if self._used_workers + workers > self.workers or self._used_memory + memory_bytes > self.memory_bytes:
                return None
            self._used_workers += workers
            self._used_memory += memory_bytes
            lease = ResourceLease(workers, memory_bytes)
            self._leases.add(lease)
            return lease

    def try_resize(self, lease: ResourceLease, memory_bytes: int) -> ResourceLease | None:
        if memory_bytes < 1:
            raise ValueError("Resource reservations must be positive")
        with self._lock:
            self._require_lease(lease)
            if self._used_memory - lease.memory_bytes + memory_bytes > self.memory_bytes:
                return None
            updated = ResourceLease(lease.workers, memory_bytes)
            self._used_memory += memory_bytes - lease.memory_bytes
            self._leases.remove(lease)
            self._leases.add(updated)
            return updated

    def release(self, lease: ResourceLease) -> None:
        with self._lock:
            self._require_lease(lease)
            self._used_workers -= lease.workers
            self._used_memory -= lease.memory_bytes
            self._leases.remove(lease)

    def _require_lease(self, lease: ResourceLease) -> None:
        if lease not in self._leases:
            raise ValueError("Unknown or already released resource lease")

    def usage(self) -> tuple[int, int]:
        with self._lock:
            return self._used_workers, self._used_memory


def fitting_memory_bytes(width: int, height: int, workers: int) -> int:
    # Retained bitmaps, worker scratch copies, source conversion and rollback.
    return width * height * (32 + 4 * workers) + 1024 * 1024


def export_memory_bytes(width: int, height: int) -> int:
    # RGBA destination/overlay plus conservative encoding and serialization room.
    return width * height * 16 + 1024 * 1024
