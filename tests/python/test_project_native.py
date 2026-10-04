from __future__ import annotations

import copy
from contextlib import closing
from dataclasses import replace

import pytest
from PIL import Image

from geometrize_py.contracts import SHAPE_TYPES
from geometrize_py.errors import APIError
from geometrize_py.images import image_data_url_bytes, image_to_data_url, open_image_bytes
from geometrize_py.native import ImageSession, RunOptions, native_available
from geometrize_py.project import (
    create_project,
    dumps_project,
    fork_project,
    loads_project,
    project_branch,
    project_scene,
    replace_branch_snapshot,
)

pytestmark = pytest.mark.skipif(not native_available(), reason="native backend is not built")


def _pattern() -> Image.Image:
    image = Image.new("RGBA", (48, 32))
    image.putdata([
        ((x * 13 + y * 3) % 256, (x * 7 + y * 11) % 256, (x * 3 + y * 17) % 256, 128 + x % 128)
        for y in range(image.height) for x in range(image.width)
    ])
    return image


def _pixels(session: ImageSession) -> bytes:
    with closing(session.result().image) as image:
        return image.tobytes()


def _snapshot(session: ImageSession) -> dict:
    return {"width": session.width, "height": session.height, "background": session.background,
            "shapes": session.shapes, "attempts": session.attempts, "initial_score": session.initial_score,
            "restored_shape_count": session.restored_shape_count, "target_digest": session.target_digest,
            "source": session.source.to_dict(), "batch_summary": session.batch_summary}


@pytest.mark.parametrize("shape_type", list(SHAPE_TYPES))
def test_project_forks_confirm_exact_native_prefixes_and_fresh_experiments(shape_type) -> None:
    with closing(_pattern()) as image:
        options = RunOptions(steps=2, shape_types=(shape_type,), shape_count=8, mutations=12,
                             max_threads=1, max_size=32, export_size=64, seed=2025,
                             source={"frame": 0, "matte": [245, 245, 245]}, background=[9, 19, 29])
        original = ImageSession.from_image(image, options)
        pixels = [_pixels(original)]
        for event in original.run_batch(options):
            if event["shapes"]:
                pixels.append(_pixels(original))
        assert len(original.shapes) == 2
        encoded = image_to_data_url(image)
        header, payload = encoded.split(",", 1)
        encoded = header.upper() + "," + payload[:40] + "\r\n" + payload[40:]
        saved = loads_project(dumps_project(create_project(
            {"name": "Original PNG", "data_url": encoded, "width": image.width, "height": image.height},
            options, _snapshot(original),
        )))
        before = copy.deepcopy(saved)
        parent_id = saved["history"]["active_branch"]
        for count in (0, 1, 2):
            child = fork_project(saved, name=f"Prefix {count}", shape_count=count, overrides={"seed": 17})
            assert child["source"]["data_url"] == encoded
            future = RunOptions(**child["options"])
            with closing(open_image_bytes(image_data_url_bytes(encoded), future.source)) as prepared:
                restored = ImageSession.from_scene(prepared, future, project_scene(child))
                assert _pixels(restored) == pixels[count]
                assert restored.attempts == restored.batch_count == 0
                assert restored.restored_shape_count == restored.revision == count
                assert restored.background == original.background == (9, 19, 29, 255)
                assert restored.target_digest == original.target_digest
                identifier = child["history"]["active_branch"]
                confirmed = replace_branch_snapshot(child, identifier, _snapshot(restored))
                assert project_scene(confirmed).attempts == 0
                assert confirmed["result"]["restored_shape_count"] == count
                assert confirmed["telemetry"]["batches"] == []
                assert confirmed["telemetry"]["initial_score"] == original.initial_score
                assert project_branch(confirmed, parent_id)["result"] == saved["result"]
                assert loads_project(dumps_project(confirmed)) == confirmed
                if count == 2:
                    # Both newly replayed runners start their seed sequence anew.
                    reference = ImageSession.from_scene(prepared, future, project_scene(child))
                    first = list(restored.run_batch(replace(future, steps=1)))
                    again = list(reference.run_batch(replace(future, steps=1)))
                    assert first == again and _pixels(restored) == _pixels(reference)
                    continued = replace_branch_snapshot(confirmed, identifier, _snapshot(restored))
                    assert continued["telemetry"]["attempts"] == restored.attempts > 0
                    assert continued["result"]["restored_shape_count"] == 2
        assert saved == before and _pixels(original) == pixels[-1]


def test_saved_project_target_digest_rejects_changed_source_before_replay() -> None:
    with closing(_pattern()) as image:
        options = RunOptions(max_size=32, steps=1, max_threads=1, shape_count=4, mutations=8)
        original = ImageSession.from_image(image, options)
        saved = create_project({"name": "Original", "data_url": image_to_data_url(image),
                                "width": image.width, "height": image.height}, options, _snapshot(original))
        child = fork_project(saved, name="Same target")
        before = copy.deepcopy(child)
        image.paste((255, 0, 255, 255), (0, 0, 8, 8))
        with pytest.raises(APIError, match="target digest") as error:
            ImageSession.from_scene(image, RunOptions(**child["options"]), project_scene(child))
        assert error.value.code == "invalid_result" and child == before
        assert original.attempts == original.revision == 0


def test_export_valid_but_native_unsafe_project_geometry_is_rejected_before_drawing() -> None:
    with closing(_pattern()) as image:
        options = RunOptions(max_size=32)
        original = ImageSession.from_image(image, options)
        snapshot = _snapshot(original)
        snapshot["shapes"] = [{"type": "ellipse", "color": [0, 0, 0, 128],
                                "data": {"x": 4, "y": 4, "rx": 32, "ry": 1e-40}}]
        saved = create_project({"name": "Imported", "data_url": image_to_data_url(image),
                                "width": image.width, "height": image.height}, options, snapshot)
        scene = project_scene(saved)
        assert len(scene.shapes) == 1
        with pytest.raises(ValueError, match="Ellipse aspect"):
            ImageSession.from_scene(image, options, scene)
        assert original.attempts == original.revision == 0
