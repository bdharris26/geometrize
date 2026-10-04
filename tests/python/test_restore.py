from __future__ import annotations

import copy
import math
from types import SimpleNamespace

import pytest
from PIL import Image

from geometrize_py import native
from geometrize_py.contracts import SHAPE_TYPES, app_contract
from geometrize_py.errors import APIError
from geometrize_py.exporting import RenderScene
from geometrize_py.native import ImageSession, NativeBackendUnavailable, RunOptions, native_available, require_native


def _pattern(width: int = 64, height: int = 48) -> Image.Image:
    image = Image.new("RGBA", (width, height))
    image.putdata([
        ((x * 13 + y * 3) % 256, (x * 7 + y * 11) % 256, (x * 3 + y * 17) % 256, 255)
        for y in range(height) for x in range(width)
    ])
    return image


def _scene(shapes=None, width: int = 64, height: int = 48, background=(23, 47, 71, 255)) -> dict:
    return {"width": width, "height": height, "background": background, "shapes": shapes or [], "attempts": 999}


def _circle(radius=4, **data) -> dict:
    return {"type": "circle", "color": {"r": 230, "g": 110, "b": 20, "a": 128},
            "data": {"x": 12, "y": 16, "r": radius, **data}, "score": 0.999}


def _pixel_score(target: Image.Image, current: Image.Image) -> float:
    squared = sum(
        (expected - actual) ** 2 for expected, actual in zip(target.tobytes(), current.tobytes(), strict=True)
    )
    return math.sqrt(squared / len(target.tobytes())) / 255


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("shape_type", list(SHAPE_TYPES))
def test_native_prefix_restore_preserves_geometry_compositing_and_fresh_counters(shape_type: str) -> None:
    image = _pattern()
    options = RunOptions(steps=4, shape_types=(shape_type,), shape_count=8, mutations=12,
                         max_threads=1, max_size=64, seed=2025)
    original = ImageSession.from_image(image, options)
    pixels = [original.result().image.tobytes()]
    for event in original.run_batch(options):
        if event["shapes"]:
            pixels.append(original.result().image.tobytes())
    assert len(original.shapes) == 4
    frozen = _scene(original.shapes, background=original.background)
    original_pixels = original.result().image.tobytes()
    # New experiment settings configure later candidates, never the saved grid.
    changed = RunOptions(steps=1, shape_types=(shape_type,), shape_count=4, mutations=8,
                         max_threads=1, max_size=32, seed=17)
    for count in (0, 1, 2, 4):
        restored = ImageSession.from_scene(image, changed, frozen, count)
        result = restored.result()
        assert result.image.size == image.size
        assert result.image.tobytes() == pixels[count]
        assert restored.attempts == restored.batch_count == 0
        assert restored.restored_shape_count == restored.revision == len(restored.shapes) == count
        assert restored.initial_score == pytest.approx(original.initial_score)
        assert restored.score == pytest.approx(_pixel_score(image, result.image), abs=1e-12)
        assert restored.batch_summary is None
        for actual, expected in zip(restored.shapes, original.shapes[:count], strict=True):
            assert actual["type"] == expected["type"]
            assert actual["type_id"] == expected["type_id"]
            assert actual["data"] == expected["data"]
            assert actual["color"] == expected["color"]
            assert actual["score"] == pytest.approx(expected["score"], abs=1e-9)
        assert original.result().image.tobytes() == original_pixels
        assert original.restored_shape_count == 0


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_restored_prefix_continues_with_repeatable_new_rng_and_immutable_input() -> None:
    image = _pattern()
    options = RunOptions(steps=2, max_threads=2, shape_count=8, mutations=8, max_size=64, seed=77)
    scene = _scene([_circle(), _circle(8, x=40, y=27)])
    before = copy.deepcopy(scene)
    first = ImageSession.from_scene(image, options, scene)
    again = ImageSession.from_scene(image, options, scene)
    old_score = first.score
    first_events = list(first.run_batch(options))
    again_events = list(again.run_batch(options))
    assert first_events == again_events
    assert first.result().image.tobytes() == again.result().image.tobytes()
    assert first.shapes == again.shapes
    assert first.score <= old_score
    assert first.restored_shape_count == again.restored_shape_count == 2
    assert first.attempts > 0
    assert first_events[0]["attempts"] == 1
    assert first_events[0]["restored_shape_count"] == 2
    assert first.batch_summary["start_attempts"] == 0
    assert first.batch_summary["start_shape_count"] == 2
    assert scene == before
    assert first.shapes[:2] != before["shapes"]  # Imported scores were replaced.


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("size", [(1, 1), (1, 12), (12, 1), (3, 17)])
def test_zero_prefix_uses_saved_background_and_exact_narrow_working_size(size) -> None:
    image = _pattern(*size)
    background = (70, 90, 130, 63)
    restored = ImageSession.from_scene(image, RunOptions(max_size=32), _scene(width=size[0], height=size[1],
                                                                          background=background), 0)
    output = restored.result().image
    assert output.size == size
    assert output.getpixel((0, 0)) == background
    assert output.tobytes() == bytes(background) * (size[0] * size[1])
    assert restored.score == restored.initial_score == pytest.approx(_pixel_score(image, output))


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_restore_does_not_trust_scores_and_recovers_perfect_fit_partial_underflow() -> None:
    image = Image.new("RGBA", (2, 2), (10, 20, 30, 255))
    shape = {"type": "rectangle", "color": [10, 20, 30, 255], "score": -17,
             "data": {"x1": 0, "y1": 0, "x2": 1, "y2": 1}}
    # Native full-image raster bounds exclude the last row/column. Only the
    # covered pixel differs from the saved background, giving an exact fit.
    image.putpixel((0, 0), (110, 120, 130, 255))
    shape["color"] = [110, 120, 130, 255]
    restored = ImageSession.from_scene(image, RunOptions(max_size=32), _scene([shape], 2, 2, (10, 20, 30, 255)))
    assert restored.result().image.tobytes() == image.tobytes()
    assert restored.score == 0
    assert 0 <= restored.shapes[0]["score"] <= 1
    assert list(restored.run_batch(RunOptions(steps=1))) == []
    assert restored.stop_reason == "adequate_fit"


@pytest.mark.parametrize("count", [-1, 2, False, True, 1.5, "1", [], {}])
def test_restore_prefix_count_is_strict_and_precedes_native_access(count, monkeypatch) -> None:
    monkeypatch.setattr(native, "require_restore_native", lambda: pytest.fail("Invalid prefix must not access native"))
    with pytest.raises(APIError, match="shape_count"):
        ImageSession.from_scene(_pattern(), RunOptions(), _scene([_circle()]), count)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("size", [(64, 49), (65, 48), (100, 100)])
def test_restore_rejects_working_dimensions_that_do_not_reproduce_source_fit(size) -> None:
    with pytest.raises(APIError, match="working dimensions"):
        ImageSession.from_scene(_pattern(), RunOptions(), _scene(width=size[0], height=size[1]))


def test_older_native_backend_reports_restore_capability_without_breaking_existing_fits(monkeypatch) -> None:
    backend = SimpleNamespace(is_available=lambda: True)
    monkeypatch.setattr(native, "_native", backend)
    assert native.require_native() is backend
    assert native.diagnostics()["available"]
    assert not native.diagnostics()["restore_available"]
    with pytest.raises(NativeBackendUnavailable, match="rebuild or reinstall"):
        ImageSession.from_scene(_pattern(), RunOptions(), _scene())


@pytest.mark.parametrize("reported", [None, (9, 17, 31, 255)])
def test_saved_background_uses_native_initial_color_when_supported_and_legacy_mean_otherwise(
    monkeypatch, reported
) -> None:
    runner = SimpleNamespace(attempts=0, initial_score=0.5, score=0.5)
    if reported is not None:
        runner.background = reported
    backend = SimpleNamespace(is_available=lambda: True, RunnerSession=lambda *_args: runner)
    monkeypatch.setattr(native, "_native", backend)
    image = Image.new("RGBA", (2, 2), (100, 150, 200, 0))
    session = ImageSession.from_image(image, RunOptions())
    assert session.background == (reported or (100, 150, 200, 255))


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_initial_background_getter_matches_initial_bitmap_and_survives_steps() -> None:
    image = _pattern()
    runner = require_native().RunnerSession(64, 48, image.tobytes(), {"max_threads": 1})
    color = runner.background
    assert runner.current_rgba()[:4] == bytes(color)
    runner.step({"max_threads": 1, "shape_count": 2, "mutations": 2})
    assert runner.background == color


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("value", [False, True, "4", None, float("nan"), float("inf"),
                                  pytest.param(10**400, id="overflowing_integer"), -1, 65])
def test_native_replay_boundary_rejects_hostile_circle_geometry(value) -> None:
    backend = require_native()
    shape = _circle(value)
    with pytest.raises((ValueError, TypeError), match="finite|bounds|negative|number"):
        backend.replay_memory(64, 48, [0, 0, 0, 255], [shape])
    with pytest.raises((ValueError, TypeError)):
        backend.restore_rgba(64, 48, _pattern().tobytes(), {}, [0, 0, 0, 255], [shape])


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_replay_rejects_tiny_nonzero_ellipse_aspect_before_rasterization() -> None:
    shape = {"type": "ellipse", "color": [0, 0, 0, 128], "data": {"x": 4, "y": 4, "rx": 32, "ry": 1e-40}}
    with pytest.raises(ValueError, match="aspect"):
        require_native().restore_rgba(64, 48, _pattern().tobytes(), {}, [0, 0, 0, 255], [shape])
    shape["data"]["ry"] = 0
    assert require_native().replay_memory(64, 48, [0, 0, 0, 255], [shape]) >= 64 * 1024


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize(
    "shapes",
    [
        pytest.param(None, id="missing_array"),
        pytest.param({}, id="object_array"),
        pytest.param([None], id="null_shape"),
        pytest.param([{"type": "unknown", "data": {}, "color": [0, 0, 0, 255]}], id="unknown_type"),
        pytest.param([{**_circle(), "type": []}], id="type_array"),
        pytest.param([{**_circle(), "data": []}], id="data_array"),
        pytest.param([{**_circle(), "data": {"x": 4, "y": 4}}], id="missing_radius"),
        pytest.param([{**_circle(), "color": [False, 0, 0, 255]}], id="boolean_channel"),
        pytest.param([{**_circle(), "color": [256, 0, 0, 255]}], id="channel_range"),
        pytest.param([{**_circle(), "color": [10**400, 0, 0, 255]}], id="huge_channel"),
        pytest.param([{**_circle(), "color": [0, 0, 255]}], id="missing_channel"),
        pytest.param([{"type": "polyline", "data": {"points": {}}, "color": [0, 0, 0, 128]}], id="points_object"),
        pytest.param([{"type": "polyline", "data": {"points": [[0]]}, "color": [0, 0, 0, 128]}], id="point_arity"),
        pytest.param([{"type": "polyline", "data": {"points": [[0, False]]}, "color": [0, 0, 0, 128]}],
                     id="point_boolean"),
    ],
)
def test_native_replay_rejects_wrong_kinds_and_channels_before_allocating_a_bitmap(shapes) -> None:
    # Empty bytes would fail bitmap construction if native validation were late.
    with pytest.raises((ValueError, TypeError), match="array|object|type|data|radius|channel|point|requires"):
        require_native().restore_rgba(64, 48, b"", {}, [0, 0, 0, 255], shapes)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("width", [False, 0, 8193, pytest.param(10**400, id="overflowing_width")])
def test_native_replay_dimensions_are_checked_before_conversion_or_allocation(width) -> None:
    with pytest.raises(ValueError, match="Replay width"):
        require_native().restore_rgba(width, 48, b"", {}, [0, 0, 0, 255], [])


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_public_restore_checks_native_geometry_before_source_fitting(monkeypatch) -> None:
    monkeypatch.setattr(native, "fit_image", lambda *_args: pytest.fail("Unsafe geometry reached image fitting"))
    with pytest.raises(ValueError, match="bounds"):
        ImageSession.from_scene(_pattern(), RunOptions(), _scene([_circle(1000)]))


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_replay_caps_path_scratch_and_aggregate_work_before_drawing() -> None:
    shape = {"type": "polyline", "color": [0, 0, 0, 128],
             "data": {"points": [[-1024, -768], [1024, 768]] * 600}}
    with pytest.raises(ValueError, match="rasterization"):
        require_native().replay_memory(64, 48, [0, 0, 0, 255], [shape])
    shape = {"type": "rectangle", "color": [0, 0, 0, 128],
             "data": {"x1": 0, "y1": 0, "x2": 8191, "y2": 8191}}
    with pytest.raises(ValueError, match="work limit"):
        require_native().replay_memory(8192, 8192, [0, 0, 0, 255], [shape] * 6)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_normal_native_replay_scratch_is_small_even_for_thousands_of_shapes() -> None:
    shape = {"type": "rectangle", "color": [0, 0, 0, 128],
             "data": {"x1": 12, "y1": 16, "x2": 36, "y2": 48}}
    assert require_native().replay_memory(1024, 768, [0, 0, 0, 255], [shape] * 4274) == 64 * 1024


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("limit", [None, False, True, 0, -1, 1000000001, 0.5, "100", [],
                                  pytest.param(10**400, id="overflowing_integer")])
def test_native_replay_work_limit_is_a_strict_lowerable_integer(limit) -> None:
    backend = require_native()
    with pytest.raises(ValueError, match="Replay max_work"):
        backend.replay_memory(32, 32, [0, 0, 0, 255], [], max_work=limit)
    with pytest.raises(ValueError, match="Replay max_work"):
        backend.restore_rgba(32, 32, b"", {}, [0, 0, 0, 255], [], max_work=limit)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_replay_charges_repeated_full_score_fallbacks_before_rescoring() -> None:
    backend = require_native()
    width = height = 32
    pixels = width * height
    background = [0, 0, 0, 255]
    rgba = bytes(background) * pixels
    shapes = [{"type": "rectangle", "color": [level, level, level, 255],
               "data": {"x1": 0, "y1": 0, "x2": 0, "y2": 0}}
              for level in [1, 0] * 32]
    # Each return to black underflows rounded partial SSE and needs a full
    # score scan. Static admission fits; only one such scan fits this ceiling.
    limit = 8 * pixels + 4 * len(shapes) + pixels
    assert backend.replay_memory(width, height, background, shapes, max_work=limit) == 64 * 1024
    with pytest.raises(ValueError, match="work limit"):
        backend.restore_rgba(width, height, rgba, {}, background, shapes, max_work=limit)
    restored = backend.restore_rgba(width, height, rgba, {}, background, shapes,
                                   max_work=limit + 31 * pixels)
    assert restored["session"].current_rgba() == rgba
    assert restored["session"].score == restored["session"].attempts == 0
    assert len(restored["shapes"]) == len(shapes)


def test_restored_scene_object_still_validates_hostile_geometry() -> None:
    scene = RenderScene(64, 48, (0, 0, 0, 255), [_circle(float("nan"))])
    with pytest.raises(APIError, match="finite"):
        ImageSession.from_scene(_pattern(), RunOptions(), scene)


class _AcceptingRunner:
    attempts = 0
    initial_score = 0.5
    score = 0.5

    def __init__(self, shape):
        self.shape = shape

    def step(self, _options):
        self.attempts += 1
        self.score *= 0.9
        return {"attempt": self.attempts, "shapes": [self.shape]}


def test_session_shape_cap_stops_before_native_attempt_and_can_return_coherent_result(monkeypatch) -> None:
    monkeypatch.setattr(native, "PROJECT_MAX_SHAPES", 2)
    runner = _AcceptingRunner(_circle())
    session = ImageSession(64, 48, (0, 0, 0, 255), runner)
    session.shapes = [_circle()]
    session.restored_shape_count = session.revision = 1
    events = list(session.run_batch(RunOptions(steps=4)))
    assert len(events) == 1
    assert session.stop_reason == "shape_limit"
    assert session.attempts == 1
    assert len(session.shapes) == session.revision == 2
    assert session.batch_summary["added"] == 1
    assert session.batch_summary["state"] == "complete"
    assert list(session.run_batch(RunOptions(steps=1))) == []
    assert session.attempts == 1


def test_session_polyline_point_cap_stops_before_an_oversized_accepted_shape(monkeypatch) -> None:
    monkeypatch.setattr(native, "PROJECT_MAX_TOTAL_POINTS", 8)
    shape = {"type": "polyline", "data": {"points": [[0, 0], [1, 1], [2, 2], [3, 3]]}}
    runner = _AcceptingRunner(shape)
    session = ImageSession(64, 48, (0, 0, 0, 255), runner)
    session.shapes = [copy.deepcopy(shape)]
    session.restored_shape_count = session.revision = 1
    events = list(session.run_batch(RunOptions(steps=3, shape_types=("polyline",))))
    assert len(events) == 1
    assert session.stop_reason == "geometry_limit"
    assert session.attempts == 1
    assert len(session.shapes) == session.revision == 2
    assert sum(len(item["data"]["points"]) for item in session.shapes) == 8


def test_history_contract_publishes_v2_limits_and_honest_restore_semantics() -> None:
    contract = app_contract()
    assert contract["project"]["version"] == 2
    assert contract["project"]["max_branches"] == 32
    assert contract["project"]["max_history_shapes"] == 200000
    assert contract["project"]["max_history_points"] == 1000000
    assert contract["restore"] == {"max_dimension": 8192, "max_raster_points": 2000000,
                                    "max_work": 1000000000, "attempt_policy": "reset"}
    assert contract["stop_reasons"]["shape_limit"] == "Shape limit reached"
    assert contract["stop_reasons"]["geometry_limit"] == "Polyline point limit reached"
