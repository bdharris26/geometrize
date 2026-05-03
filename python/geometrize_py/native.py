from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

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
    steps: int = 75
    shape_types: tuple[str, ...] = DEFAULT_SHAPES
    alpha: int = 128
    shape_count: int = 50
    mutations: int = 100
    seed: int = 9001
    max_threads: int = 0
    max_size: int = 512

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "RunOptions":
        data = data or {}
        shape_types = data.get("shape_types", DEFAULT_SHAPES)
        if isinstance(shape_types, str):
            shape_types = tuple(s.strip() for s in shape_types.split(",") if s.strip())
        return cls(
            steps=_clamp_int(data.get("steps", cls.steps), 1, 2000),
            shape_types=normalize_shape_types(shape_types),
            alpha=_clamp_int(data.get("alpha", cls.alpha), 1, 255),
            shape_count=_clamp_int(data.get("shape_count", cls.shape_count), 1, 500),
            mutations=_clamp_int(data.get("mutations", cls.mutations), 1, 1000),
            seed=_clamp_int(data.get("seed", cls.seed), 0, 2**31 - 1),
            max_threads=_clamp_int(data.get("max_threads", cls.max_threads), 0, 128),
            max_size=_clamp_int(data.get("max_size", cls.max_size), 32, 2048),
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


def native_available() -> bool:
    return bool(_native and _native.is_available())


def require_native() -> Any:
    if not native_available():
        detail = f": {_IMPORT_ERROR}" if _IMPORT_ERROR else ""
        raise NativeBackendUnavailable(f"Geometrize native backend is unavailable{detail}")
    return _native


def run_image(image: Image.Image, options: RunOptions | None = None) -> RunResult:
    options = options or RunOptions()
    backend = require_native()
    source = fit_image(image, options.max_size).convert("RGBA")
    width, height = source.size
    background = average_color(source)
    result = backend.run_rgba(width, height, source.tobytes(), options.to_native_dict())
    rgba = result["rgba"]
    output = Image.frombytes("RGBA", (result["width"], result["height"]), rgba)
    return RunResult(
        width=int(result["width"]),
        height=int(result["height"]),
        image=output,
        shapes=list(result["shapes"]),
        attempts=int(result["attempts"]),
        background=background,
    )


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
