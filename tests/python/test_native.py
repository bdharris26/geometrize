from __future__ import annotations

import pytest
from PIL import Image

from geometrize_py.native import MAX_IMAGE_SIZE, NativeBackendUnavailable, RunOptions, native_available, run_image


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_runner_returns_preview_and_shapes() -> None:
    image = Image.new("RGBA", (8, 8), (240, 30, 30, 255))
    result = run_image(image, RunOptions(steps=2, shape_types=("ellipse",), shape_count=10, mutations=10, max_size=64))

    assert result.width == 8
    assert result.height == 8
    assert result.attempts >= 1
    assert result.image.size == (8, 8)
    assert isinstance(result.shapes, list)


def test_native_unavailable_error_is_importable() -> None:
    assert issubclass(NativeBackendUnavailable, RuntimeError)


def test_run_options_keep_high_resolution_budget() -> None:
    assert RunOptions().max_size == 1024
    assert MAX_IMAGE_SIZE == 8192
    assert RunOptions.from_mapping({"max_size": 99999}).max_size == MAX_IMAGE_SIZE
