from __future__ import annotations

import pytest
from PIL import Image

from geometrize_py.images import image_from_data_url, image_to_data_url


def test_image_data_url_round_trips_png() -> None:
    image = Image.new("RGBA", (2, 3), (10, 20, 30, 255))

    decoded = image_from_data_url(image_to_data_url(image))

    assert decoded.mode == "RGBA"
    assert decoded.size == (2, 3)


def test_image_data_url_rejects_non_image_payload() -> None:
    with pytest.raises(ValueError, match="base64 image data URL"):
        image_from_data_url("data:text/plain;base64,aGVsbG8=")
