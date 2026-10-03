import pytest

from geometrize_py.errors import APIError
from geometrize_py.exporting import ExportArtifact, ExportCache, validate_scene


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
