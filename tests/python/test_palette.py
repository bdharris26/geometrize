from __future__ import annotations

import copy
import math
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest
from PIL import Image

from geometrize_py import native
from geometrize_py.contracts import SHAPE_TYPES, app_contract
from geometrize_py.native import Focus, ImageSession, NativeBackendUnavailable, RunOptions, native_available
from geometrize_py.palette import Palette, extract_palette, normalize_palette, palette_memory_bytes


@pytest.mark.parametrize("value", [False, True, 0, [], "red", {}, {"colors": []}, {"colors": [[0, 0, 0]] * 33},
                                   {"colors": [[0, 0, 0]], "mode": "exact"}])
def test_palette_requires_a_bounded_known_object(value) -> None:
    with pytest.raises(ValueError, match="palette"):
        RunOptions.from_mapping({"palette": value})


@pytest.mark.parametrize("color", [None, {}, "#000000", [0, 0], [0, 0, 0, 255], [True, 0, 0], [0.0, 0, 0],
                                  ["0", 0, 0], [-1, 0, 0], [256, 0, 0], [10**400, 0, 0]])
def test_palette_requires_rgb_integer_bytes(color) -> None:
    with pytest.raises(ValueError, match="[Pp]alette"):
        Palette([color])


@pytest.mark.parametrize("strength", [False, True, None, "0.5", float("nan"), float("inf"), -0.01, 1.01, 10**400])
def test_palette_strength_is_finite_real_and_bounded(strength) -> None:
    with pytest.raises(ValueError, match="palette.strength"):
        RunOptions(palette={"colors": [[1, 2, 3]], "strength": strength})


def test_palette_is_immutable_canonical_and_serialized_without_aliases() -> None:
    raw = {"colors": [[0, 255, 42], [0, 255, 42], [255, 0, 0]]}
    options = RunOptions.from_mapping({"palette": raw})
    assert options.palette == Palette(((0, 255, 42), (255, 0, 0)))
    raw["colors"][0][0] = 99
    with pytest.raises(FrozenInstanceError):
        options.palette.strength = 0.5
    output = options.to_native_dict()["palette"]
    output["colors"][0][0] = 99
    assert options.palette.to_dict() == {"colors": [[0, 255, 42], [255, 0, 0]], "strength": 1.0}
    assert normalize_palette(options.palette) is options.palette
    assert RunOptions().palette is None
    assert RunOptions.from_mapping({"palette": None}).to_native_dict()["palette"] is None
    assert Palette([(0, 0, 0)], 0).strength == 0


def test_palette_contract_is_optional_and_defensively_copied() -> None:
    config = app_contract()
    assert config["defaults"]["palette"] is None
    assert config["palette"] == {
        "max_colors": 32, "sample_size": 256,
        "defaults": {"max_colors": 8, "strength": 1.0, "soft_strength": 0.75},
        "limits": {"strength": {"min": 0.0, "max": 1.0}},
    }
    config["palette"]["defaults"]["max_colors"] = 100
    config["palette"]["limits"]["strength"]["max"] = 100
    assert app_contract()["palette"]["defaults"]["max_colors"] == 8
    assert app_contract()["palette"]["limits"]["strength"]["max"] == 1


@pytest.mark.parametrize("count", [True, False, None, 0, 33, 8.0, "8"])
def test_extraction_count_is_strict(count) -> None:
    with pytest.raises(ValueError, match="max_colors"):
        extract_palette(Image.new("RGB", (8, 8)), count)


def test_extraction_is_repeatable_alpha_weighted_and_skips_hidden_rgb() -> None:
    source = Image.new("RGBA", (4, 1))
    source.putdata([(255, 0, 0, 255), (0, 0, 255, 1), (0, 255, 0, 0), (0, 255, 0, 0)])
    assert extract_palette(source, 1)["colors"] == [[254, 0, 1]]
    assert extract_palette(source, 8)["colors"] == [[255, 0, 0], [0, 0, 255]]
    assert extract_palette(source, 8) == extract_palette(source, 8)
    with pytest.raises(ValueError, match="fully transparent"):
        extract_palette(Image.new("RGBA", (4, 4)))


def test_extraction_samples_bounded_source_and_returns_fewer_unique_flat_colors() -> None:
    source = Image.new("RGBA", (1024, 512), (23, 47, 71, 255))
    before = source.tobytes()
    assert extract_palette(source, 32) == {
        "colors": [[23, 47, 71]], "requested_max_colors": 32, "sample_width": 256, "sample_height": 128,
    }
    assert source.tobytes() == before
    assert palette_memory_bytes(1024, 512) > 1024 * 512 * 12


@pytest.mark.parametrize("position", [(0, 0), (1, 1), (511, 511)])
def test_extraction_recovers_visible_source_pixels_missed_by_sampling(position) -> None:
    source = Image.new("RGBA", (512, 512))
    source.putpixel(position, (255, 0, 0, 255))
    assert extract_palette(source, 8)["colors"] == [[255, 0, 0]]


def _pattern(width: int = 48, height: int = 32) -> Image.Image:
    image = Image.new("RGBA", (width, height))
    image.putdata([((x * 13 + y * 3) % 256, (x * 7 + y * 11) % 256, (x * 3 + y * 17) % 256, 255)
                   for y in range(height) for x in range(width)])
    return image


def _sse(target: Image.Image, current: Image.Image) -> int:
    return sum((expected - actual) ** 2
               for expected, actual in zip(target.tobytes(), current.tobytes(), strict=True))


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("shape_type", list(SHAPE_TYPES))
def test_palette_off_and_zero_strength_preserve_geometry_pixels_and_attempts(shape_type: str) -> None:
    image = _pattern()
    options = RunOptions(steps=3, shape_types=(shape_type,), shape_count=4, mutations=8, max_threads=1)
    backend = native.require_native()
    plain = options.to_native_dict()
    plain.pop("palette")
    expected = backend.run_rgba(image.width, image.height, image.tobytes(), plain)
    assert backend.run_rgba(image.width, image.height, image.tobytes(), options.to_native_dict()) == expected
    zero = replace(options, palette=Palette([(255, 0, 0)], strength=0)).to_native_dict()
    assert backend.run_rgba(image.width, image.height, image.tobytes(), zero) == expected


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("shape_type", list(SHAPE_TYPES))
@pytest.mark.parametrize("strength", [0.5, 1.0])
@pytest.mark.parametrize("workers", [1, 2])
def test_palette_new_shapes_preserve_alpha_reduce_actual_error_and_are_seeded(shape_type, strength, workers) -> None:
    image = _pattern()
    palette = Palette([(0, 0, 0), (255, 255, 255), (255, 32, 96)], strength)
    options = RunOptions(steps=2, shape_types=(shape_type,), shape_count=4, mutations=8, max_threads=workers,
                         max_size=48, palette=palette, alpha=128, seed=888)
    first = ImageSession.from_image(image, options)
    before = _sse(image, first.result().image)
    accepted = 0
    for event in first.run_batch(options):
        current = first.result().image
        after = _sse(image, current)
        if event["shapes"]:
            assert after < before
            accepted += len(event["shapes"])
        else:
            assert after == before
        for shape in event["shapes"]:
            color = shape["color"]
            assert color["a"] == options.alpha
            if strength == 1:
                assert (color["r"], color["g"], color["b"]) in palette.colors
        assert event["score"] == pytest.approx(math.sqrt(after / (image.width * image.height * 4)) / 255, abs=1e-9)
        before = after
    assert accepted == 2
    again = ImageSession.from_image(image, options)
    list(again.run_batch(options))
    assert again.result().image.tobytes() == first.result().image.tobytes()
    assert again.shapes == first.shapes
    assert again.attempts == first.attempts


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_palette_batches_and_restored_history_keep_colors_and_metadata_independent() -> None:
    image = _pattern()
    options = RunOptions(steps=1, max_size=48, shape_count=4, mutations=8, max_threads=1,
                         palette=Palette([(255, 0, 0), (0, 0, 255)]))
    session = ImageSession.from_image(image, options)
    list(session.run_batch(options))
    scene = {"width": session.width, "height": session.height, "shapes": copy.deepcopy(session.shapes),
             "background": session.background, "attempts": session.attempts}
    changed = replace(options, palette=Palette([(0, 0, 0), (255, 255, 255)]))
    restored = ImageSession.from_scene(image, changed, scene)
    assert restored.result().image.tobytes() == session.result().image.tobytes()
    list(restored.run_batch(changed))
    for retained, original in zip(restored.shapes[:1], session.shapes, strict=True):
        assert {key: value for key, value in retained.items() if key != "score"} == {
            key: value for key, value in original.items() if key != "score"
        }
    assert restored.restored_shape_count == 1
    summary = restored.batch_summary
    assert summary["palette"] == changed.palette.to_dict()
    summary["palette"]["colors"][0][0] = 77
    assert restored.batch_summary["palette"] == changed.palette.to_dict()
    assert scene["shapes"] == session.shapes
    list(restored.run_batch(replace(changed, palette=None)))
    assert restored.palette is None
    assert restored.batch_summary["palette"] is None


def test_stale_native_palette_capability_is_actionable_and_off_still_works(monkeypatch) -> None:
    backend = SimpleNamespace(is_available=lambda: True, RunnerSession=lambda *_args: SimpleNamespace())
    monkeypatch.setattr(native, "_native", backend)
    assert native.diagnostics()["palette_available"] is False
    ImageSession.from_image(_pattern(), RunOptions())
    ImageSession.from_image(_pattern(), RunOptions(palette=Palette([(0, 0, 0)], 0)))
    with pytest.raises(NativeBackendUnavailable, match="palette fitting; rebuild or reinstall"):
        ImageSession.from_image(_pattern(), RunOptions(palette=Palette([(0, 0, 0)])))


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("palette", [{"colors": [[10**400, 0, 0]]}, {"colors": [[0, 0, 0]], "strength": 10**400},
                                    {"colors": [[0, 0, 0]], "strength": True}, {"colors": [[0, False, 0]]}])
def test_native_palette_validation_precedes_attempt_mutation(palette) -> None:
    backend = native.require_native()
    image = _pattern()
    runner = backend.RunnerSession(image.width, image.height, image.tobytes(), RunOptions().to_native_dict())
    before = runner.current_rgba()
    with pytest.raises(ValueError, match="[Pp]alette"):
        runner.step({**RunOptions().to_native_dict(), "palette": palette})
    assert runner.attempts == 0
    assert runner.current_rgba() == before


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("shape_type", ["quadratic_bezier", "polyline"])
@pytest.mark.parametrize("size", [(1, 32), (32, 1), (48, 32)])
def test_palette_clipped_overlap_keeps_original_blending_and_actual_scores(shape_type, size) -> None:
    image = Image.new("RGBA", size)
    image.putdata([(25, 70, 210, 255) if index % 2 else (200, 110, 35, 255)
                   for index in range(image.width * image.height)])
    options = RunOptions(steps=4, shape_types=(shape_type,), shape_count=8, mutations=8,
                         max_threads=1, seed=888, max_size=48, alpha=128,
                         focus=Focus(0.4, 0.6, radius=0.1, strength=0.8),
                         palette=Palette([(0, 0, 0), (255, 255, 255), (255, 32, 96)], 0.5))
    session = ImageSession.from_image(image, options)
    before = _sse(image, session.result().image)
    for event in session.run_batch(options):
        after = _sse(image, session.result().image)
        assert after < before if event["shapes"] else after == before
        assert session.score == pytest.approx(math.sqrt(after / len(image.tobytes())) / 255, abs=1e-12)
        before = after
    scene = {"width": session.width, "height": session.height, "background": session.background,
             "shapes": session.shapes, "attempts": session.attempts}
    restored = ImageSession.from_scene(image, options, scene)
    assert restored.result().image.tobytes() == session.result().image.tobytes()
    assert restored.score == pytest.approx(session.score, abs=1e-12)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_palette_repaired_model_preserves_rng_across_rejections_workers_seeds_and_off() -> None:
    image = Image.new("RGBA", (1, 32))
    image.putdata([(25, 70, 210, 255) if index % 2 else (200, 110, 35, 255) for index in range(32)])
    backend = native.require_native()
    options = RunOptions(shape_types=("quadratic_bezier",), shape_count=8, mutations=8, max_threads=1,
                         seed=888, focus=Focus(0.4, 0.6, 0.1, 0.8),
                         palette=Palette([(0, 0, 0), (255, 255, 255), (255, 32, 96)], 0.5))
    runner = backend.RunnerSession(1, 32, image.tobytes(), options.to_native_dict())
    rejected = replace(options, max_threads=3, palette=Palette([runner.background[:3]]))
    assert runner.step(rejected.to_native_dict())["shapes"] == []
    shapes = []
    consumed = 3
    for workers, seed, strength in [(1, 888, 0.5), (2, 19, 1), (1, 911, 0.5), (3, 7, 1)]:
        current_options = replace(options, max_threads=workers, seed=seed,
                                  palette=Palette(options.palette.colors, strength))
        for _ in range(3):
            shapes.extend(runner.step(current_options.to_native_dict())["shapes"])
            consumed += workers
    assert shapes
    frozen = {"width": 1, "height": 32, "background": runner.background, "shapes": shapes, "attempts": 13}
    off = replace(options, palette=None, max_threads=2, seed=71)
    reference = backend.restore_rgba(
        1, 32, image.tobytes(), off.to_native_dict(), frozen["background"], shapes,
    )["session"]
    expected = reference.step(replace(off, seed=off.seed + consumed).to_native_dict())
    actual = runner.step(off.to_native_dict())
    # Ordinary fitting retains upstream partial-score rounding; RNG continuity
    # is observable in the complete geometry, colors and exact composited pixels.
    assert [{key: value for key, value in shape.items() if key != "score"} for shape in actual["shapes"]] == [
        {key: value for key, value in shape.items() if key != "score"} for shape in expected["shapes"]
    ]
    assert runner.current_rgba() == reference.current_rgba()
    assert actual["attempt"] == 14
