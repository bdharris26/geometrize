"""Python UI and orchestration layer for Geometrize."""

from .native import Focus, NativeBackendUnavailable, RunOptions, RunResult, iter_image, native_available, run_image
from .palette import Palette
from .source import SourceOptions

__version__ = "0.2.0"

__all__ = [
    "Focus",
    "NativeBackendUnavailable",
    "Palette",
    "RunOptions",
    "RunResult",
    "SourceOptions",
    "iter_image",
    "native_available",
    "run_image",
]
