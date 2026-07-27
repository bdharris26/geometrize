from __future__ import annotations

import pytest
from PIL import Image

from geometrize_py.native import (
    MAX_IMAGE_SIZE,
    MAX_WORKING_IMAGE_SIZE,
    NativeBackendUnavailable,
    RunOptions,
    iter_image,
    native_available,
    run_image,
)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_runner_returns_preview_and_shapes() -> None:
    image = Image.new("RGBA", (8, 8), (240, 30, 30, 255))
    result = run_image(image, RunOptions(steps=2, shape_types=("ellipse",), shape_count=10, mutations=10, max_size=64))

    assert result.width == 8
    assert result.height == 8
    assert result.attempts >= 1
    assert result.image.size == (8, 8)
    assert isinstance(result.shapes, list)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_runner_streams_progress_events() -> None:
    image = Image.new("RGBA", (8, 8), (30, 120, 200, 255))
    options = RunOptions(
        steps=2,
        shape_types=("ellipse",),
        shape_count=10,
        mutations=10,
        max_size=64,
    )
    events = list(iter_image(image, options))

    assert events[0]["event"] == "start"
    assert events[-1]["event"] == "complete"
    assert any(event["event"] == "step" for event in events)
    assert events[0]["width"] == 8
    assert events[1]["attempt"] == 1
    assert events[-1]["result"].attempts >= 2


def test_native_unavailable_error_is_importable() -> None:
    assert issubclass(NativeBackendUnavailable, RuntimeError)


def test_run_options_keep_high_resolution_budget() -> None:
    assert RunOptions().max_size == 1024
    assert RunOptions().export_size == 1024
    assert MAX_WORKING_IMAGE_SIZE == 2048
    assert MAX_IMAGE_SIZE == 8192
    assert RunOptions.from_mapping({"max_size": 99999}).max_size == MAX_WORKING_IMAGE_SIZE
    assert RunOptions.from_mapping({"steps": 99999}).steps == 4096
    assert RunOptions.from_mapping({"export_size": 99999}).export_size == MAX_IMAGE_SIZE
