from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from threading import Lock
from typing import Any

from PIL import Image

from .images import average_color, fit_image

SHAPE_TYPES: dict[str, int] = {
    "rectangle": 1,
    "rotated_rectangle": 2,
    "triangle": 4,
    "ellipse": 8,
    "rotated_ellipse": 16,
    "circle": 32,
    "line": 64,
    "quadratic_bezier": 128,
    "polyline": 256,
}

DEFAULT_SHAPES = ("ellipse", "rotated_rectangle", "triangle")
MAX_WORKING_IMAGE_SIZE = 2048
MAX_IMAGE_SIZE = 8192


class NativeBackendUnavailable(RuntimeError):
    """Raised when the native C++ core extension cannot be imported."""


try:
    from . import _native
except Exception as exc:  # pragma: no cover - exercised when extension is absent
    _native = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@dataclass(frozen=True)
class RunOptions:
    steps: int = 128
    shape_types: tuple[str, ...] = DEFAULT_SHAPES
    alpha: int = 128
    shape_count: int = 64
    mutations: int = 128
    seed: int = 9001
    max_threads: int = 0
    max_size: int = 1024
    export_size: int = 1024

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> RunOptions:
        data = data or {}
        shape_types = data.get("shape_types", DEFAULT_SHAPES)
        if isinstance(shape_types, str):
            shape_types = tuple(s.strip() for s in shape_types.split(",") if s.strip())
        max_size = _clamp_int(data.get("max_size", cls.max_size), 32, MAX_WORKING_IMAGE_SIZE)
        return cls(
            steps=_clamp_int(data.get("steps", cls.steps), 1, 4096),
            shape_types=normalize_shape_types(shape_types),
            alpha=_clamp_int(data.get("alpha", cls.alpha), 1, 255),
            shape_count=_clamp_int(data.get("shape_count", cls.shape_count), 1, 512),
            mutations=_clamp_int(data.get("mutations", cls.mutations), 1, 2048),
            seed=_clamp_int(data.get("seed", cls.seed), 0, 2**31 - 1),
            max_threads=_clamp_int(data.get("max_threads", cls.max_threads), 0, 128),
            max_size=max_size,
            export_size=_clamp_int(data.get("export_size", data.get("max_size", max_size)), 32, MAX_IMAGE_SIZE),
        )

    def to_native_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "shape_types": list(self.shape_types),
            "alpha": self.alpha,
            "shape_count": self.shape_count,
            "mutations": self.mutations,
            "seed": self.seed,
            "max_threads": self.max_threads,
        }


@dataclass(frozen=True)
class RunResult:
    width: int
    height: int
    image: Image.Image
    shapes: list[dict[str, Any]]
    attempts: int
    background: tuple[int, int, int, int]


class ImageSession:
    def __init__(
        self,
        width: int,
        height: int,
        background: tuple[int, int, int, int],
        session: Any,
    ) -> None:
        self.width = width
        self.height = height
        self.background = background
        self._session = session
        self.shapes: list[dict[str, Any]] = []
        self.batch_count = 0
        self.lock = Lock()

    @classmethod
    def from_image(cls, image: Image.Image, options: RunOptions) -> ImageSession:
        backend = require_native()
        source = fit_image(image, options.max_size).convert("RGBA")
        width, height = source.size
        background = average_color(source)
        session = backend.RunnerSession(width, height, source.tobytes(), options.to_native_dict())
        return cls(width, height, background, session)

    @property
    def attempts(self) -> int:
        return int(self._session.attempts)

    def run_batch(self, options: RunOptions) -> Iterator[dict[str, Any]]:
        with self.lock:
            self.batch_count += 1
            batch_index = self.batch_count
            accepted_at_start = len(self.shapes)
            attempts_at_start = self.attempts
            max_attempts = max(options.steps * 8, options.steps + 64)

            while (
                len(self.shapes) - accepted_at_start < options.steps
                and self.attempts - attempts_at_start < max_attempts
            ):
                step = self._session.step(options.to_native_dict())
                step_shapes = list(step["shapes"])
                self.shapes.extend(step_shapes)
                yield {
                    "event": "step",
                    "batch": batch_index,
                    "attempt": int(step["attempt"]),
                    "attempts": int(step["attempt"]),
                    "shapes": step_shapes,
                    "shape_count": len(self.shapes),
                    "batch_shape_count": len(self.shapes) - accepted_at_start,
                    "batch_goal": options.steps,
                }

    def result(self) -> RunResult:
        output = Image.frombytes("RGBA", (self.width, self.height), self._session.current_rgba())
        return RunResult(
            width=self.width,
            height=self.height,
            image=output,
            shapes=self.shapes.copy(),
            attempts=self.attempts,
            background=self.background,
        )


def native_available() -> bool:
    return bool(_native and _native.is_available())


def require_native() -> Any:
    if not native_available():
        detail = f": {_IMPORT_ERROR}" if _IMPORT_ERROR else ""
        raise NativeBackendUnavailable(f"Geometrize native backend is unavailable{detail}")
    return _native


def run_image(image: Image.Image, options: RunOptions | None = None) -> RunResult:
    result: RunResult | None = None
    for event in iter_image(image, options):
        if event["event"] == "complete":
            result = event["result"]
    if result is None:
        raise RuntimeError("Geometrize run did not produce a result")
    return result


def iter_image(image: Image.Image, options: RunOptions | None = None) -> Iterator[dict[str, Any]]:
    options = options or RunOptions()
    session = ImageSession.from_image(image, options)

    yield {
        "event": "start",
        "width": session.width,
        "height": session.height,
        "background": session.background,
        "attempts": 0,
        "shape_count": 0,
        "batch": 1,
        "batch_goal": options.steps,
    }

    yield from session.run_batch(options)
    result = session.result()
    yield {
        "event": "complete",
        "result": result,
        "width": result.width,
        "height": result.height,
        "attempts": result.attempts,
        "shape_count": len(result.shapes),
        "batch": session.batch_count,
    }


def normalize_shape_types(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        if not key:
            continue
        if key not in SHAPE_TYPES:
            valid = ", ".join(SHAPE_TYPES)
            raise ValueError(f"Unknown shape type '{value}'. Expected one of: {valid}")
        if key not in normalized:
            normalized.append(key)
    if not normalized:
        raise ValueError("At least one shape type is required")
    return tuple(normalized)


def _clamp_int(value: Any, lower: int, upper: int) -> int:
    number = int(value)
    return max(lower, min(upper, number))
