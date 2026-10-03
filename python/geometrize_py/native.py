from __future__ import annotations

import os
import platform
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from threading import Event, Lock, RLock
from typing import Any

from PIL import Image

from .contracts import (
    DEFAULT_SHAPES,
    FOCUS_DEFAULTS,
    FOCUS_LIMITS,
    MAX_IMAGE_SIZE,
    MAX_WORKING_IMAGE_SIZE,
    OPTION_DEFAULTS,
    OPTION_LIMITS,
    SHAPE_TYPES,
)
from .images import average_color, fit_image

__all__ = [
    "DEFAULT_SHAPES",
    "MAX_IMAGE_SIZE",
    "MAX_WORKING_IMAGE_SIZE",
    "SHAPE_TYPES",
    "Focus",
    "ImageSession",
    "NativeBackendUnavailable",
    "RunOptions",
    "RunResult",
    "diagnostics",
    "effective_max_threads",
    "iter_image",
    "native_available",
    "normalize_shape_types",
    "normalize_focus",
    "require_native",
    "run_image",
]

MAX_AUTO_THREADS = 8
PERFECT_FIT_EPSILON = 1e-9


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
class Focus:
    """Candidate placement in normalized image coordinates; scoring stays global."""

    x: float
    y: float
    radius: float = FOCUS_DEFAULTS["radius"]
    strength: float = FOCUS_DEFAULTS["strength"]

    def __post_init__(self) -> None:
        for name, (lower, upper) in FOCUS_LIMITS.items():
            value = getattr(self, name)
            try:
                valid = not isinstance(value, bool) and isinstance(value, Real) and isfinite(value)
            except OverflowError:
                valid = False
            if not valid or not lower <= value <= upper:
                raise ValueError(f"focus.{name} must be a finite number from {lower:g} to {upper:g}")
            object.__setattr__(self, name, float(value))

    def to_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in FOCUS_LIMITS}


def normalize_focus(value: Any) -> Focus | None:
    if value is None or isinstance(value, Focus):
        return value
    if not isinstance(value, dict):
        raise ValueError("focus must be an object or null")
    if set(value) - FOCUS_LIMITS.keys():
        raise ValueError("focus only accepts x, y, radius, and strength")
    if "x" not in value or "y" not in value:
        raise ValueError("focus requires x and y")
    return Focus(value["x"], value["y"], value.get("radius", Focus.radius), value.get("strength", Focus.strength))


@dataclass(frozen=True)
class RunOptions:
    steps: int = OPTION_DEFAULTS["steps"]
    shape_types: tuple[str, ...] = DEFAULT_SHAPES
    alpha: int = OPTION_DEFAULTS["alpha"]
    shape_count: int = OPTION_DEFAULTS["shape_count"]
    mutations: int = OPTION_DEFAULTS["mutations"]
    seed: int = OPTION_DEFAULTS["seed"]
    max_threads: int = OPTION_DEFAULTS["max_threads"]
    max_size: int = OPTION_DEFAULTS["max_size"]
    export_size: int = OPTION_DEFAULTS["export_size"]
    stagnation_limit: int = OPTION_DEFAULTS["stagnation_limit"]
    focus: Focus | None = None

    def __post_init__(self) -> None:
        shape_types = self.shape_types
        if isinstance(shape_types, str):
            shape_types = tuple(value.strip() for value in shape_types.split(",") if value.strip())
        object.__setattr__(self, "shape_types", normalize_shape_types(shape_types))
        object.__setattr__(self, "focus", normalize_focus(self.focus))
        for name, (lower, upper) in OPTION_LIMITS.items():
            _validate_int_option(name, getattr(self, name), lower, upper)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> RunOptions:
        data = data or {}
        max_size = _clamp_option("max_size", data.get("max_size", cls.max_size))
        return cls(
            steps=_clamp_option("steps", data.get("steps", cls.steps)),
            shape_types=data.get("shape_types", DEFAULT_SHAPES),
            alpha=_clamp_option("alpha", data.get("alpha", cls.alpha)),
            shape_count=_clamp_option("shape_count", data.get("shape_count", cls.shape_count)),
            mutations=_clamp_option("mutations", data.get("mutations", cls.mutations)),
            seed=_clamp_option("seed", data.get("seed", cls.seed)),
            max_threads=_clamp_option("max_threads", data.get("max_threads", cls.max_threads)),
            max_size=max_size,
            export_size=_clamp_option("export_size", data.get("export_size", data.get("max_size", max_size))),
            stagnation_limit=_clamp_option("stagnation_limit", data.get("stagnation_limit", cls.stagnation_limit)),
            focus=data.get("focus"),
        )

    def to_native_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "shape_types": list(self.shape_types),
            "alpha": self.alpha,
            "shape_count": self.shape_count,
            "mutations": self.mutations,
            "seed": self.seed,
            "max_threads": effective_max_threads(self.max_threads),
            "focus": self.focus.to_dict() if self.focus is not None else None,
        }

    @property
    def actual_max_threads(self) -> int:
        return effective_max_threads(self.max_threads)


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
        focus: Focus | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.background = background
        self._session = session
        self.shapes: list[dict[str, Any]] = []
        self.batch_count = 0
        self._run_lock = RLock()
        self._focus_lock = Lock()
        self._focus = normalize_focus(focus)
        self._cancel_requested = Event()
        self.initial_score = float(getattr(session, "initial_score", 1.0))
        self._score = float(getattr(session, "score", self.initial_score))
        self.revision = 0
        self.stop_reason: str | None = None
        self._batch_summary: dict[str, Any] | None = None

    @classmethod
    def from_image(cls, image: Image.Image, options: RunOptions) -> ImageSession:
        backend = require_native()
        source = fit_image(image, options.max_size).convert("RGBA")
        width, height = source.size
        background = average_color(source)
        session = backend.RunnerSession(width, height, source.tobytes(), options.to_native_dict())
        return cls(width, height, background, session, options.focus)

    @property
    def attempts(self) -> int:
        return int(self._session.attempts)

    @property
    def score(self) -> float:
        return self._score

    @property
    def batch_summary(self) -> dict[str, Any] | None:
        if self._batch_summary is None:
            return None
        summary = self._batch_summary.copy()
        for name in ("focus", "initial_focus"):
            if summary[name] is not None:
                summary[name] = summary[name].copy()
        return summary

    @property
    def focus(self) -> Focus | None:
        with self._focus_lock:
            return self._focus

    def request_focus(self, focus: Focus | None) -> None:
        """Change placement for the next attempt without waiting on the batch lock."""
        normalized = normalize_focus(focus)
        with self._focus_lock:
            self._focus = normalized

    def try_acquire_run(self) -> bool:
        return self._run_lock.acquire(blocking=False)

    def release_run(self) -> None:
        self._run_lock.release()

    def prepare_run(self, options: RunOptions | None = None) -> None:
        """Clear a prior cancellation while holding the run reservation."""
        self._cancel_requested.clear()
        self.stop_reason = None
        if options is not None:
            self.request_focus(options.focus)

    def request_cancel(self) -> None:
        """Ask the active batch to stop after its current native step."""
        self._cancel_requested.set()

    def run_batch(self, options: RunOptions) -> Iterator[dict[str, Any]]:
        with self._run_lock:
            self.prepare_run(options)
            yield from self._run_batch(options)

    def run_reserved_batch(self, options: RunOptions) -> Iterator[dict[str, Any]]:
        """Run after prepare_run(options), while the caller holds the reservation."""
        yield from self._run_batch(options)

    def _run_batch(self, options: RunOptions) -> Iterator[dict[str, Any]]:
        self.batch_count += 1
        batch_index = self.batch_count
        accepted_at_start = len(self.shapes)
        attempts_at_start = self.attempts
        max_attempts = max(options.steps * 8, options.steps + 64)
        native_options = options.to_native_dict()
        rejected_in_a_row = 0
        reason: str | None = None
        self._batch_summary = {
            "index": batch_index,
            "target": options.steps,
            "shapeTypes": list(options.shape_types),
            "candidates": options.shape_count,
            "mutations": options.mutations,
            "alpha": options.alpha,
            "seed": options.seed,
            "max_threads": options.actual_max_threads,
            "effective_threads": options.actual_max_threads,
            "stagnation_limit": options.stagnation_limit,
            "initial_focus": options.focus.to_dict() if options.focus is not None else None,
            "focus": options.focus.to_dict() if options.focus is not None else None,
            "start_shape_count": accepted_at_start,
            "start_attempts": attempts_at_start,
            "start_score": self.score,
            "added": 0,
            "attempts": 0,
            "score": self.score,
            "revision": self.revision,
            "state": "running",
            "reason": None,
        }

        try:
            while True:
                added = len(self.shapes) - accepted_at_start
                batch_attempts = self.attempts - attempts_at_start
                if self._cancel_requested.is_set():
                    reason = "paused"
                    break
                if self.score <= PERFECT_FIT_EPSILON:
                    reason = "adequate_fit"
                    break
                if added >= options.steps:
                    reason = "target_reached"
                    break
                if batch_attempts >= max_attempts:
                    reason = "attempt_limit"
                    break
                if options.stagnation_limit and rejected_in_a_row >= options.stagnation_limit:
                    reason = "no_further_improvement"
                    break

                # Snapshot the immutable control once between native attempts.
                # Its separate tiny lock also permits edits while the GIL is released.
                focus = self.focus
                native_options["focus"] = focus.to_dict() if focus is not None else None
                # The C++ call releases the GIL. Cancellation is checked after
                # it returns so an accepted shape is never lost from history.
                step = self._session.step(native_options)
                step_shapes = list(step["shapes"])
                self.shapes.extend(step_shapes)
                if step_shapes:
                    self._score = float(getattr(self._session, "score", step_shapes[-1].get("score", self.score)))
                    self.revision += len(step_shapes)
                    rejected_in_a_row = 0
                else:
                    rejected_in_a_row += 1
                added = len(self.shapes) - accepted_at_start
                batch_attempts = self.attempts - attempts_at_start
                self._batch_summary.update(
                    added=added,
                    attempts=batch_attempts,
                    score=self.score,
                    revision=self.revision,
                    focus=native_options["focus"],
                )
                yield {
                    "event": "step",
                    "batch": batch_index,
                    "attempt": int(step["attempt"]),
                    "attempts": int(step["attempt"]),
                    "shapes": step_shapes,
                    "shape_count": len(self.shapes),
                    "batch_shape_count": added,
                    "batch_goal": options.steps,
                    "score": self.score,
                    "initial_score": self.initial_score,
                    "revision": self.revision,
                    "focus": native_options["focus"],
                }
        except GeneratorExit:
            reason = "paused"
            raise
        finally:
            # Generator close on a disconnected client also leaves a coherent
            # session that can be exported or continued.
            self.stop_reason = reason or ("paused" if self._cancel_requested.is_set() else "error")
            self._batch_summary.update(
                added=len(self.shapes) - accepted_at_start,
                attempts=self.attempts - attempts_at_start,
                score=self.score,
                revision=self.revision,
                state="paused" if self.stop_reason == "paused" else (
                    "error" if self.stop_reason == "error" else "complete"
                ),
                reason=self.stop_reason,
            )

    def result(self) -> RunResult:
        with self._run_lock:
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


def diagnostics() -> dict[str, Any]:
    """Return concise native import details for the CLI doctor command."""
    return {
        "available": native_available(),
        "module": "geometrize_py._native",
        "module_path": str(getattr(_native, "__file__", "")) if _native else None,
        "import_error_type": type(_IMPORT_ERROR).__name__ if _IMPORT_ERROR else None,
        "import_error": str(_IMPORT_ERROR) if _IMPORT_ERROR else None,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


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
        "score": session.score,
        "initial_score": session.initial_score,
        "revision": session.revision,
        "focus": options.focus.to_dict() if options.focus is not None else None,
    }

    yield from session.run_batch(options)
    result = session.result()
    focus = session.focus
    yield {
        "event": "complete",
        "result": result,
        "width": result.width,
        "height": result.height,
        "attempts": result.attempts,
        "shape_count": len(result.shapes),
        "batch": session.batch_count,
        "score": session.score,
        "initial_score": session.initial_score,
        "revision": session.revision,
        "stop_reason": session.stop_reason,
        "batch_summary": session.batch_summary,
        "focus": focus.to_dict() if focus is not None else None,
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


def _clamp_option(name: str, value: Any) -> int:
    lower, upper = OPTION_LIMITS[name]
    return max(lower, min(upper, int(value)))


def effective_max_threads(requested: int) -> int:
    """Bound the automatic CLI setting before it reaches upstream C++."""
    return requested or min(MAX_AUTO_THREADS, max(1, os.cpu_count() or 1))


def _validate_int_option(name: str, value: Any, lower: int, upper: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < lower or value > upper:
        raise ValueError(f"{name} must be an integer from {lower} to {upper}")
