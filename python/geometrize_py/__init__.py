"""Python UI and orchestration layer for Geometrize."""

from .native import NativeBackendUnavailable, RunOptions, RunResult, native_available, run_image

__all__ = [
    "NativeBackendUnavailable",
    "RunOptions",
    "RunResult",
    "native_available",
    "run_image",
]

__version__ = "0.2.0"
