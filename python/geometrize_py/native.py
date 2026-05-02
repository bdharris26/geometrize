from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from geometrize_py.jobs import GeometrizeJob


class NativeCoreUnavailable(RuntimeError):
    """Raised when the C++ Geometrize core has not been bound yet."""


@dataclass(frozen=True, slots=True)
class RunResult:
    output_path: Path
    shapes_written: int


class NativeRunner:
    def run(self, job: GeometrizeJob) -> RunResult:
        raise NativeCoreUnavailable(
            "The Python CLI is ready, but the native core binding is not available yet."
        )
