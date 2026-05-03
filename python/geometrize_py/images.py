from __future__ import annotations

import base64
import io

from PIL import Image
from PIL import ImageStat


def open_image_bytes(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGBA")


def fit_image(image: Image.Image, max_size: int) -> Image.Image:
    fitted = image.copy()
    fitted.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return fitted


def image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def image_to_data_url(image: Image.Image) -> str:
    encoded = base64.b64encode(image_to_png_bytes(image)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def image_from_data_url(data_url: str) -> Image.Image:
    marker = "base64,"
    if marker not in data_url:
        raise ValueError("Expected a base64 image data URL")
    payload = data_url.split(marker, 1)[1]
    return open_image_bytes(base64.b64decode(payload))


def average_color(image: Image.Image) -> tuple[int, int, int, int]:
    rgba = image.convert("RGBA")
    if rgba.width == 0 or rgba.height == 0:
        return (255, 255, 255, 255)
    red, green, blue, alpha = ImageStat.Stat(rgba).mean
    return (int(red), int(green), int(blue), int(alpha))
