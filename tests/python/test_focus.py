from __future__ import annotations

import json
import math
import subprocess
import sys
import threading
from dataclasses import FrozenInstanceError
from statistics import mean

import pytest
from PIL import Image

from geometrize_py.contracts import SHAPE_TYPES
from geometrize_py.native import Focus, ImageSession, RunOptions, native_available, require_native


@pytest.mark.parametrize("value", [False, True, 0, 0.5, "center", [], [0.5, 0.5], {}])
def test_focus_rejects_non_objects_and_missing_center(value) -> None:
    with pytest.raises(ValueError, match="focus"):
        RunOptions.from_mapping({"focus": value})


@pytest.mark.parametrize("field", ["x", "y", "radius", "strength"])
@pytest.mark.parametrize("value", [False, True, "0.5", None, float("nan"), float("inf"), -float("inf"), 10**400])
def test_focus_requires_finite_real_values(field: str, value) -> None:
    focus = {"x": 0.5, "y": 0.5, "radius": 0.2, "strength": 0.75, field: value}
    with pytest.raises(ValueError, match=f"focus.{field}"):
        RunOptions(focus=focus)


@pytest.mark.parametrize(
    ("field", "value"), [("x", -0.01), ("x", 1.01), ("y", -0.01), ("y", 1.01),
                         ("radius", 0), ("radius", 1.01), ("strength", -0.01), ("strength", 1.01)],
)
def test_focus_rejects_out_of_range_values(field: str, value: float) -> None:
    focus = {"x": 0.5, "y": 0.5, field: value}
    with pytest.raises(ValueError, match=f"focus.{field}"):
        RunOptions.from_mapping({"focus": focus})


def test_focus_is_immutable_and_keeps_defaults_and_boundary_values() -> None:
    raw = {"x": 0, "y": 1}
    options = RunOptions.from_mapping({"focus": raw})
    raw["x"] = 1
    assert options.focus == Focus(0, 1, radius=0.2, strength=0.75)
    with pytest.raises(FrozenInstanceError):
        options.focus.x = 0.5
    assert RunOptions().focus is None
    assert RunOptions.from_mapping({"focus": None}).to_native_dict()["focus"] is None
    assert Focus(0, 1, 0.01, 0).to_dict() == {"x": 0.0, "y": 1.0, "radius": 0.01, "strength": 0.0}
    assert Focus(1, 0, 1, 1).to_dict() == {"x": 1.0, "y": 0.0, "radius": 1.0, "strength": 1.0}
    with pytest.raises(ValueError, match="only accepts"):
        RunOptions.from_mapping({"focus": {"x": 0.5, "y": 0.5, "mask": []}})


class _ControlledRunner:
    initial_score = 0.5
    score = 0.5
    attempts = 0

    def __init__(self) -> None:
        self.entered = [threading.Event(), threading.Event()]
        self.release = [threading.Event(), threading.Event()]
        self.focus_seen = []

    def step(self, options):
        self.focus_seen.append(options["focus"])
        if self.attempts < 2:
            self.entered[self.attempts].set()
            assert self.release[self.attempts].wait(5)
        self.attempts += 1
        self.score *= 0.9
        return {"attempt": self.attempts,
                "shapes": [{"type": "rectangle", "score": self.score, "data": {}, "color": {}}]}


def test_live_focus_is_read_between_attempts_without_rolling_back_counters() -> None:
    runner = _ControlledRunner()
    session = ImageSession(8, 8, (0, 0, 0, 255), runner)
    initial = Focus(0.25, 0.5)
    moved = Focus(0.8, 0.2, strength=1)
    events = []
    errors = []

    def run():
        try:
            events.extend(session.run_batch(RunOptions(steps=3, focus=initial)))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert runner.entered[0].wait(5)
        assert not session.try_acquire_run()
        session.request_focus(moved)
        assert session.attempts == session.revision == len(session.shapes) == 0
        runner.release[0].set()
        assert runner.entered[1].wait(5)
        assert session.attempts == session.revision == len(session.shapes) == 1
        session.request_focus(None)
        runner.release[1].set()
        worker.join(5)
        assert not worker.is_alive()
        assert not errors
        assert runner.focus_seen == [initial.to_dict(), moved.to_dict(), None]
        assert [event["focus"] for event in events] == runner.focus_seen
        assert session.attempts == session.revision == len(session.shapes) == 3
        assert session.stop_reason == "target_reached"
        assert session.batch_summary["initial_focus"] == initial.to_dict()
        assert session.batch_summary["focus"] is None
        assert session.focus is None

        # Each subsequent batch installs its own captured placement setting.
        list(session.run_batch(RunOptions(steps=1, focus=moved)))
        assert runner.focus_seen[-1] == moved.to_dict()
        list(session.run_batch(RunOptions(steps=1)))
        assert runner.focus_seen[-1] is None
        assert session.attempts == session.revision == 5
    finally:
        for event in runner.release:
            event.set()
        worker.join(5)


def _pattern(width: int, height: int) -> Image.Image:
    image = Image.new("RGBA", (width, height))
    image.putdata([((x * 3 + y * 7) % 256, (x * 11 + y * 5) % 256, (x * 7 + y * 13) % 256, 255)
                   for y in range(height) for x in range(width)])
    return image


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("workers", [1, 3])
@pytest.mark.parametrize("shape_type", list(SHAPE_TYPES))
def test_focus_is_seeded_and_safe_for_every_primitive(shape_type: str, workers: int) -> None:
    image = _pattern(72, 48)
    options = RunOptions(steps=1, shape_types=(shape_type,), shape_count=8, mutations=16, max_threads=workers,
                         focus=Focus(0.75, 0.25, radius=0.15), seed=1234).to_native_dict()
    first = require_native().run_rgba(72, 48, image.tobytes(), {**options, "steps": 4})
    again = require_native().run_rgba(72, 48, image.tobytes(), {**options, "steps": 4})
    assert first == again
    assert first["attempts"] == 4
    assert first["shapes"]
    assert all(shape["type"] == shape_type and math.isfinite(shape["score"]) for shape in first["shapes"])


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_none_and_zero_strength_preserve_geometry_and_pixels() -> None:
    image = _pattern(64, 32)
    backend = require_native()
    for shape_type in ("rectangle", "rotated_rectangle", "triangle", "ellipse", "rotated_ellipse", "circle",
                       "line", "quadratic_bezier", "polyline"):
        options = {"steps": 3, "shape_types": [shape_type], "shape_count": 8, "mutations": 16,
                   "max_threads": 1, "seed": 909}
        original = backend.run_rgba(64, 32, image.tobytes(), options)
        assert backend.run_rgba(64, 32, image.tobytes(), {**options, "focus": None}) == original
        zero = {**options, "focus": Focus(1, 0, strength=0).to_dict()}
        assert backend.run_rgba(64, 32, image.tobytes(), zero) == original


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_focus_pulls_candidate_placement_toward_region() -> None:
    image = _pattern(128, 96)
    focus = Focus(0.75, 0.25, radius=0.08, strength=1)
    distances = {"global": [], "mixed": [], "focused": []}
    backend = require_native()
    for seed in range(64):
        options = {"steps": 1, "shape_types": ["circle"], "shape_count": 1, "mutations": 1,
                   "max_threads": 1, "seed": seed}
        for name, setting in (("global", None), ("mixed", {**focus.to_dict(), "strength": 0.5}),
                              ("focused", focus.to_dict())):
            result = backend.run_rgba(128, 96, image.tobytes(), {**options, "focus": setting})
            for shape in result["shapes"]:
                distances[name].append(math.hypot(shape["data"]["x"] - focus.x * 127,
                                                 shape["data"]["y"] - focus.y * 95))
    assert min(map(len, distances.values())) >= 40
    assert mean(distances["focused"]) < mean(distances["global"]) * 0.4
    assert mean(distances["focused"]) < mean(distances["mixed"]) < mean(distances["global"])


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_focused_scoring_and_rasterization_cover_full_geometry() -> None:
    image = _pattern(64, 48)
    focus = Focus(0.5, 0.5, radius=0.01, strength=1)
    session = ImageSession.from_image(image, RunOptions(shape_types=("circle",), focus=focus))
    list(session.run_batch(RunOptions(steps=1, shape_types=("circle",), shape_count=8, mutations=8,
                                    max_threads=1, focus=focus)))
    assert len(session.shapes) == 1
    result = session.result()
    total = sum((a - b) ** 2 for a, b in zip(image.tobytes(), result.image.tobytes(), strict=True))
    assert session.score == pytest.approx(math.sqrt(total / (64 * 48 * 4)) / 255, abs=1e-6)
    # The tiny placement ring is not a raster clip: accepted geometry still
    # paints image pixels outside the ring.
    assert any(result.image.getpixel((x, y)) != result.background
               and math.hypot(x - 31.5, y - 23.5) > 2
               for y in range(48) for x in range(64))


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_tiny_and_narrow_images_allow_enable_clear_and_zero_strength() -> None:
    # Isolate native safety coverage so an invalid RNG interval reports a
    # subprocess failure instead of taking down the test process.
    script = """
import json, math
from geometrize_py import _native
types = ('rectangle', 'rotated_rectangle', 'triangle', 'ellipse', 'rotated_ellipse',
         'circle', 'line', 'quadratic_bezier', 'polyline')
count = 0
for width, height in ((1, 1), (1, 7), (9, 1), (2, 2), (47, 3)):
    rgba = bytes(c for y in range(height) for x in range(width)
                 for c in ((x * 31 + y * 41) % 256, (x * 61 + y * 17) % 256, 90, 255))
    for shape_type in types:
        options = {'shape_types': [shape_type], 'shape_count': 2, 'mutations': 4, 'max_threads': 2}
        session = _native.RunnerSession(width, height, rgba, options)
        for focus in ({'x': 0, 'y': 1, 'radius': 0.01, 'strength': 1}, None,
                      {'x': 1, 'y': 0, 'radius': 1, 'strength': 0}):
            session.step({**options, 'focus': focus})
            assert math.isfinite(session.score)
            assert len(session.current_rgba()) == width * height * 4
            count += 1
print(json.dumps({'safe_steps': count}))
"""
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {"safe_steps": 135}


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("focus", [False, [], {"x": True, "y": 0.5}, {"x": 0.5},
                                  {"x": 0.5, "y": float("inf")}, {"x": 0.5, "y": 0.5, "radius": 0}])
def test_direct_native_options_validate_focus(focus) -> None:
    with pytest.raises(ValueError, match="focus"):
        require_native().RunnerSession(2, 2, bytes((10, 20, 30, 255)) * 4, {"focus": focus})
