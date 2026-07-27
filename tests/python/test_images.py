from __future__ import annotations

import io

import pytest
from PIL import Image

from geometrize_py.images import (
    MAX_SOURCE_DIMENSION,
    MAX_SOURCE_PIXELS,
    _validate_source_size,
    average_color,
    image_from_data_url,
    image_to_data_url,
    open_image_bytes,
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


def test_open_image_bytes_applies_exif_orientation() -> None:
    source = Image.new("RGB", (2, 3))
    source.putdata(
        [
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (0, 255, 255),
            (255, 0, 255),
        ]
    )
    exif = source.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", quality=100, subsampling=0, exif=exif)

    oriented = open_image_bytes(buffer.getvalue())

    assert oriented.size == (3, 2)
    assert oriented.getpixel((0, 0))[1:3] == pytest.approx((255, 255), abs=15)
    assert oriented.getpixel((2, 0))[0] == pytest.approx(255, abs=15)


def test_average_color_matches_native_opaque_background() -> None:
    assert average_color(Image.new("RGBA", (2, 2), (20, 40, 60, 0))) == (20, 40, 60, 255)
