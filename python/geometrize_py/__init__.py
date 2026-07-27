"""Python UI and orchestration layer for Geometrize."""

from .native import NativeBackendUnavailable, RunOptions, RunResult, iter_image, native_available, run_image

__version__ = "0.2.0"

__all__ = [
    "NativeBackendUnavailable",
    "RunOptions",
    "RunResult",
    "iter_image",
    "native_available",
    "run_image",
]
