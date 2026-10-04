from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from geometrize_py import native
from geometrize_py.errors import APIError
from geometrize_py.exporting import validate_scene
from geometrize_py.images import apply_matte, fit_image
from geometrize_py.native import ImageSession, NativeBackendUnavailable, RunOptions, native_available
from geometrize_py.source import SourceOptions


def _image() -> Image.Image:
    image = Image.new("RGBA", (48, 32), (25, 70, 210, 128))
    image.paste((200, 110, 35, 255), (24, 0, 48, 32))
    return image


def _options(**changes) -> RunOptions:
    return replace(RunOptions(steps=2, max_size=32, max_threads=1, shape_types=("rectangle",),
                              shape_count=8, mutations=8, seed=123), **changes)


def _scene(session: ImageSession) -> dict:
    return {"width": session.width, "height": session.height, "background": session.background,
            "shapes": session.shapes, "attempts": session.attempts, "target_digest": session.target_digest}


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("background", [None, (255, 255, 255), (0, 0, 0), (30, 40, 50)])
def test_creation_background_is_opaque_and_matches_initial_native_pixels(background) -> None:
    image = Image.new("RGBA", (2, 1))
    image.putdata([(201, 51, 11, 0), (20, 40, 60, 128)])
    session = ImageSession.from_image(image, _options(background=background))
    expected = (*background, 255) if background is not None else (110, 45, 35, 255)
    assert session.background == expected
    assert session.result().image.tobytes() == bytes(expected) * 2
    assert session.initial_score == session.score > 0
    assert session.attempts == 0


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_supplied_still_is_matted_before_fit_and_digest_without_reselecting_frame() -> None:
    image = _image()
    source = SourceOptions(7, (255, 255, 255))
    options = _options(source=source)
    session = ImageSession.from_image(image, options)
    with apply_matte(image, source.matte) as matted, fit_image(matted, options.max_size) as fitted:
        digest = hashlib.sha256(fitted.tobytes()).hexdigest()
        # Applying the same policy to a prepared still cannot flatten twice.
        already_prepared = ImageSession.from_image(matted, options)
    assert session.source == source
    assert session.target_digest == session.result().target_digest == digest
    assert already_prepared.target_digest == digest
    assert already_prepared.background == session.background
    assert session.result().image.tobytes() == already_prepared.result().image.tobytes()
    assert image.getpixel((0, 0))[3] == 128
    events = list(session.run_batch(options))
    assert all(event["source"] == source.to_dict() and event["target_digest"] == digest for event in events)
    assert session.batch_summary["source"] == source.to_dict()
    summary = session.batch_summary
    summary["source"]["matte"][0] = 1
    assert session.batch_summary["source"]["matte"] == [255, 255, 255]


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_restore_keeps_actual_background_fitted_target_and_prefix_then_continues() -> None:
    image = _image()
    options = _options(source=SourceOptions(2, (255, 255, 255)), background=(9, 19, 29))
    original = ImageSession.from_image(image, options)
    list(original.run_batch(options))
    assert len(original.shapes) == 2
    scene = _scene(original)
    restored = ImageSession.from_scene(image, replace(options, max_size=128, background=(240, 240, 240)), scene)
    assert (restored.width, restored.height) == (32, 21)
    assert restored.background == original.background == (9, 19, 29, 255)
    assert restored.source == options.source
    assert restored.target_digest == original.target_digest
    assert restored.result().image.tobytes() == original.result().image.tobytes()
    assert restored.attempts == restored.batch_count == 0
    assert restored.restored_shape_count == 2
    before = original.result().image.tobytes()
    list(restored.run_batch(replace(options, steps=1, background=(255, 255, 255))))
    assert restored.background == original.background
    assert original.result().image.tobytes() == before
    legacy = scene.copy()
    legacy.pop("target_digest")
    assert ImageSession.from_scene(image, options, legacy).target_digest == original.target_digest


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_digest_mismatch_rejects_before_native_replay(monkeypatch) -> None:
    image = _image()
    original = ImageSession.from_image(image, _options())
    scene = _scene(original)
    scene["target_digest"] = "0" * 64
    backend = native.require_native()
    stale = SimpleNamespace(is_available=lambda: True, replay_memory=backend.replay_memory,
                            restore_rgba=lambda *_args, **_kwargs: pytest.fail("Mismatched target was replayed"))
    monkeypatch.setattr(native, "_native", stale)
    with pytest.raises(APIError, match="target digest") as error:
        ImageSession.from_scene(image, _options(), scene)
    assert error.value.code == "invalid_result"
    assert original.attempts == original.revision == 0


@pytest.mark.parametrize("digest", [None, True, "", "a" * 63, "g" * 64, "a" * 65])
def test_saved_target_digest_is_strict_when_present(digest) -> None:
    scene = {"width": 1, "height": 1, "background": [0, 0, 0, 255], "shapes": [], "target_digest": digest}
    with pytest.raises(APIError, match="target_digest"):
        validate_scene(scene)
    scene["target_digest"] = "AB" * 32
    assert validate_scene(scene).target_digest == "ab" * 32
    scene.pop("target_digest")
    assert validate_scene(scene).target_digest is None


def test_frozen_source_rejection_preserves_pause_focus_palette_and_counters() -> None:
    runner = SimpleNamespace(initial_score=0.5, score=0.5, attempts=4)
    options = _options(source=SourceOptions(1, (255, 255, 255)), focus={"x": 0.2, "y": 0.3},
                       palette={"colors": [[1, 2, 3]], "strength": 0})
    session = ImageSession(2, 2, (0, 0, 0, 255), runner, options.focus, options.palette, options.source)
    session.request_cancel()
    changed = replace(options, source=SourceOptions(), focus=None, palette=None)
    with pytest.raises(APIError) as error:
        list(session.run_batch(changed))
    assert error.value.code == "source_mismatch"
    assert error.value.status == 409
    assert session._cancel_requested.is_set()
    assert session.focus == options.focus and session.palette == options.palette
    assert session.attempts == 4 and session.batch_count == session.revision == 0
    assert session.batch_summary is None


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_stale_background_capability_keeps_small_legacy_runs_but_rejects_unsupported_modes(monkeypatch) -> None:
    backend = native.require_native()
    old = SimpleNamespace(is_available=lambda: True, RunnerSession=backend.RunnerSession,
                          replay_memory=backend.replay_memory, restore_rgba=backend.restore_rgba)
    monkeypatch.setattr(native, "_native", old)
    assert native.diagnostics()["background_available"] is False
    original = ImageSession.from_image(_image(), _options())
    with pytest.raises(NativeBackendUnavailable, match="rebuild or reinstall"):
        ImageSession.from_image(_image(), _options(background=(255, 255, 255)))
    with pytest.raises(NativeBackendUnavailable, match="large-image backgrounds"):
        native.require_background_native(None, (2**32 - 1) // 255 + 1)
    assert native.require_background_native(None, (2**32 - 1) // 255) is old
    # Restore ignores a new-canvas choice and uses the already saved canvas.
    restored = ImageSession.from_scene(_image(), _options(background=(1, 2, 3)), _scene(original))
    assert restored.background == original.background


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("background", [True, [True, 0, 0], [1.0, 2, 3], [1, 2], [10**400, 0, 0]])
def test_native_boundary_rejects_invalid_creation_background(background) -> None:
    with pytest.raises((ValueError, TypeError, OverflowError)):
        native.require_native().RunnerSession(2, 2, bytes((0, 0, 0, 255)) * 4, {"background": background})


@pytest.mark.parametrize("fail", [False, True])
def test_native_constructor_releases_owned_pillow_stills_even_when_it_raises(monkeypatch, fail) -> None:
    owned = []
    actual_matte, actual_fit = native.apply_matte, native.fit_image

    def matte(*args):
        image = actual_matte(*args)
        owned.append(image)
        return image

    def fit(*args):
        image = actual_fit(*args)
        owned.append(image)
        return image

    def runner(*args):
        if fail:
            raise RuntimeError("constructor failed")
        return SimpleNamespace(background=[1, 2, 3, 255], initial_score=0.5, score=0.5)

    monkeypatch.setattr(native, "apply_matte", matte)
    monkeypatch.setattr(native, "fit_image", fit)
    monkeypatch.setattr(native, "_native", SimpleNamespace(is_available=lambda: True, RunnerSession=runner,
                                                         background_api_version=1))
    source = _image()
    if fail:
        with pytest.raises(RuntimeError, match="constructor failed"):
            ImageSession.from_image(source, _options(source=SourceOptions(0, (255, 255, 255))))
    else:
        ImageSession.from_image(source, _options(source=SourceOptions(0, (255, 255, 255))))
    for image in owned:
        with pytest.raises(ValueError, match="closed image"):
            image.getpixel((0, 0))
    assert source.getpixel((0, 0)) == (25, 70, 210, 128)
