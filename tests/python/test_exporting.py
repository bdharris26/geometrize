import tracemalloc

import pytest

from geometrize_py import exporting
from geometrize_py.contracts import app_contract
from geometrize_py.errors import APIError
from geometrize_py.exporting import ExportArtifact, ExportCache, inspect_scene, validate_scene
from geometrize_py.resources import scene_memory_bytes


def scene_with(shape):
    return {"width": 8, "height": 8, "background": [0, 0, 0, 255], "shapes": [shape]}


@pytest.mark.parametrize("kind", [[], {}, None, "unknown"])
def test_invalid_shape_type_has_consistent_error_code(kind) -> None:
    with pytest.raises(APIError) as error:
        validate_scene(scene_with({"type": kind}))
    assert error.value.code == "invalid_result"


@pytest.mark.parametrize("radius", [-1, float("nan"), float("inf"), 10**400])
def test_invalid_geometry_is_rejected_without_overflow(radius) -> None:
    with pytest.raises(APIError):
        validate_scene(
            scene_with(
                {
                    "type": "circle",
                    "color": {"r": 0, "g": 0, "b": 0, "a": 255},
                    "data": {"x": 4, "y": 4, "r": radius},
                }
            )
        )


def test_source_relative_geometry_bound_and_global_angle_score_bound() -> None:
    shape = {
        "type": "rotated_ellipse",
        "color": [0, 0, 0, 255],
        "data": {"x": 4, "y": 4, "rx": 128, "ry": 2, "angle": 1e8},
        "score": 1e8,
    }
    accepted = validate_scene(scene_with(shape))
    assert accepted.shapes[0]["data"]["rx"] == 128
    assert accepted.shapes[0]["data"]["angle"] == 1e8
    assert accepted.shapes[0]["score"] == 1e8
    for field in ("x", "rx"):
        shape["data"][field] = 129
        with pytest.raises(APIError, match="128") as error:
            validate_scene(scene_with(shape))
        assert error.value.code == "invalid_result"
        shape["data"][field] = 4 if field == "x" else 128
    shape["data"]["angle"] = 1e9 + 1
    with pytest.raises(APIError, match="finite number"):
        validate_scene(scene_with(shape))


def test_aggregate_polyline_cap_is_enforced_before_normalization(monkeypatch) -> None:
    monkeypatch.setattr(exporting, "PROJECT_MAX_TOTAL_POINTS", 3)
    raw = scene_with({"type": "polyline", "data": {"points": [[0, 0]] * 4}})

    def copied_shape(*_args):
        raise AssertionError("normalization must not start before point counting")

    monkeypatch.setattr(exporting, "_shape", copied_shape)
    with pytest.raises(APIError, match="polyline points") as error:
        validate_scene(raw)
    assert error.value.code == "invalid_result"


def test_scene_inspection_counts_points_in_raw_and_normalized_results() -> None:
    raw = scene_with(
        {"type": "polyline", "color": [0, 0, 0, 255], "data": {"points": [[0, 0], [1, 2]]}}
    )
    assert inspect_scene(raw) == (1, 2)
    assert inspect_scene(validate_scene(raw)) == (1, 2)
    project = app_contract()["project"]
    assert project["max_total_points"] == 1_000_000
    assert project["max_geometry_factor"] == 16


def test_scene_memory_estimate_covers_measured_normalization_peak() -> None:
    raw = {
        "width": 8,
        "height": 8,
        "background": [0, 0, 0, 255],
        "shapes": [
            {
                "type": "polyline",
                "color": [0, 0, 0, 255],
                "data": {"points": [[x % 8, x % 8] for x in range(1000)]},
            }
            for _ in range(100)
        ],
    }
    shape_count, total_points = inspect_scene(raw)
    tracemalloc.start()
    try:
        frozen = validate_scene(raw)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(frozen.shapes) == shape_count
    assert total_points == 100_000
    assert scene_memory_bytes(shape_count, total_points) >= peak


def test_scene_validation_copies_geometry_and_channels() -> None:
    raw = scene_with(
        {"type": "circle", "color": {"r": 20, "g": 40, "b": 60, "a": 128}, "data": {"x": 4, "y": 4, "r": 2}}
    )
    frozen = validate_scene(raw)
    before = frozen.fingerprint()
    raw["shapes"][0]["data"]["r"] = 7
    raw["shapes"][0]["color"]["r"] = 90
    assert frozen.fingerprint() == before
    assert frozen.fingerprint() != validate_scene(raw).fingerprint()


def test_cache_enforces_lru_and_byte_budget() -> None:
    cache = ExportCache(max_bytes=12, max_entries=2)
    artifact = ExportArtifact(b"png", "svg", 8, 8)
    cache.store(("first", 8), artifact)
    cache.store(("second", 8), artifact)
    assert cache.get(("first", 8)) is artifact
    cache.store(("third", 8), artifact)
    assert cache.get(("second", 8)) is None
    assert cache.get(("first", 8)) is artifact
    cache.store(("huge", 8), ExportArtifact(b"x" * 20, "svg", 8, 8))
    assert cache.get(("huge", 8)) is None
