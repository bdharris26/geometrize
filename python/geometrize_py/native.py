from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from geometrize_py.images import RgbaImage, load_rgba_image, save_rgba_image
from geometrize_py.jobs import ExportFormat, GeometrizeJob


class NativeCoreUnavailable(RuntimeError):
    """Raised when the C++ Geometrize core has not been bound yet."""


def is_native_core_available() -> bool:
    try:
        import geometrize_py._native  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class RunResult:
    output_path: Path
    shapes_written: int
    attempts: int


class NativeRunner:
    def run(self, job: GeometrizeJob) -> RunResult:
        try:
            from geometrize_py import _native
        except ImportError as exc:
            raise NativeCoreUnavailable(
                "The Python CLI is ready, but the native core binding is not available yet."
            ) from exc

        if job.export_format is not ExportFormat.PNG:
            raise ValueError(f"native runner currently supports PNG output, not {job.export_format.value}")

        input_image = load_rgba_image(job.input_path)
        result = _native.run_rgba(
            input_image.width,
            input_image.height,
            input_image.rgba,
            {
                "shape": job.shape.cli_name,
                "count": job.count,
                "alpha": job.alpha,
                "candidate_shape_count": job.candidate_shape_count,
                "max_shape_mutations": job.max_shape_mutations,
                "seed": job.seed,
                "max_threads": job.max_threads,
            },
        )

        output_image = RgbaImage(
            width=int(result["width"]),
            height=int(result["height"]),
            rgba=bytes(result["rgba"]),
        )
        save_rgba_image(output_image, job.output_path)
        return RunResult(
            output_path=job.output_path,
            shapes_written=len(result["shapes"]),
            attempts=int(result["attempts"]),
        )
