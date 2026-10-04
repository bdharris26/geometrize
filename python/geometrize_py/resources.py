"""Nonblocking reservations for active CPU and memory use.

Leases resize atomically from source inspection to decoding and fitting. Cache
memory is accounted for separately; this budget covers transient allocations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import Lock

from .image_probe import SourceProbe

DEFAULT_WORKER_BUDGET = max(1, min(8, os.cpu_count() or 1))
DEFAULT_ACTIVE_MEMORY_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class ResourceLease:
    workers: int
    memory_bytes: int
    _token: object = field(default_factory=object, repr=False)


class WorkBudget:
    def __init__(self, workers: int, memory_bytes: int) -> None:
        if not _valid_int(workers, 1) or not _valid_int(memory_bytes, 1):
            raise ValueError("Worker and active memory budgets must be positive")
        self.workers = workers
        self.memory_bytes = memory_bytes
        self._used_workers = 0
        self._used_memory = 0
        self._lock = Lock()
        self._leases: set[ResourceLease] = set()

    def try_reserve(self, workers: int, memory_bytes: int) -> ResourceLease | None:
        if not _valid_int(workers, 0) or not _valid_int(memory_bytes, 1):
            raise ValueError("Worker reservations cannot be negative and memory reservations must be positive")
        with self._lock:
            if self._used_workers + workers > self.workers or self._used_memory + memory_bytes > self.memory_bytes:
                return None
            self._used_workers += workers
            self._used_memory += memory_bytes
            lease = ResourceLease(workers, memory_bytes)
            self._leases.add(lease)
            return lease

    def try_resize(
        self, lease: ResourceLease, memory_bytes: int, *, workers: int | None = None,
    ) -> ResourceLease | None:
        if not _valid_int(memory_bytes, 1):
            raise ValueError("Resource reservations must be positive")
        with self._lock:
            self._require_lease(lease)
            workers = lease.workers if workers is None else workers
            if not _valid_int(workers, 0):
                raise ValueError("Worker reservations cannot be negative")
            if (self._used_memory - lease.memory_bytes + memory_bytes > self.memory_bytes
                    or self._used_workers - lease.workers + workers > self.workers):
                return None
            updated = ResourceLease(workers, memory_bytes)
            self._used_workers += workers - lease.workers
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


def _valid_int(value: object, lower: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= lower


def fitting_memory_bytes(width: int, height: int, workers: int) -> int:
    # Retained bitmaps, worker scratch copies, source conversion and rollback.
    return width * height * (32 + 4 * workers) + 1024 * 1024


def source_memory_bytes(probe: SourceProbe, raw_bytes: int, *, preview_pixels: int = 0) -> int:
    # Decoder canvas/current/previous/disposal plus detached RGBA, orientation
    # and matte copies. WebP allocates canvases and all demux records at open.
    pixels = probe.width * probe.height
    composition = probe.animated or probe.mime_type in {"image/apng", "image/webp"}
    return (pixels * (32 if composition else 16) + raw_bytes * 2 + probe.metadata_memory
            + preview_pixels * 32 + 64 * 1024)


def export_memory_bytes(width: int, height: int) -> int:
    # RGBA destination/overlay plus conservative encoding and serialization room.
    return width * height * 16 + 1024 * 1024


def scene_memory_bytes(shape_count: int, total_points: int = 0) -> int:
    """Budget normalized scene metadata, including each copied polyline vertex.

    A point is represented by a Python list and two floats. 192 bytes covers
    those objects plus list/dictionary references and serialization overhead.
    """
    if not _valid_int(shape_count, 0) or not _valid_int(total_points, 0):
        raise ValueError("Shape and point counts cannot be negative")
    return 1024 * 1024 + shape_count * 3072 + total_points * 192
