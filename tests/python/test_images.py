from __future__ import annotations

import pytest
from PIL import Image

from geometrize_py.images import (
    MAX_SOURCE_DIMENSION,
    MAX_SOURCE_PIXELS,
    _validate_source_size,
    image_from_data_url,
    image_to_data_url,
)


def test_image_data_url_round_trips_png() -> None:
    image = Image.new("RGBA", (2, 3), (10, 20, 30, 255))

    decoded = image_from_data_url(image_to_data_url(image))

    assert decoded.mode == "RGBA"
    assert decoded.size == (2, 3)


def test_image_data_url_rejects_non_image_payload() -> None:
    with pytest.raises(ValueError, match="base64 image data URL"):
        image_from_data_url("data:text/plain;base64,aGVsbG8=")


def test_source_size_rejects_oversized_dimensions_and_pixel_counts() -> None:
    _validate_source_size(8192, 8192)

    with pytest.raises(ValueError, match="dimensions cannot exceed"):
        _validate_source_size(MAX_SOURCE_DIMENSION + 1, 1)

    with pytest.raises(ValueError, match="cannot exceed"):
        _validate_source_size(8193, MAX_SOURCE_PIXELS // 8193 + 1)
