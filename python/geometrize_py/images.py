from __future__ import annotations

import base64
import binascii
import io
from contextlib import closing
from math import isfinite

from PIL import Image, ImageOps, ImageStat

from .apng import select_apng
from .contracts import MAX_SOURCE_DIMENSION as MAX_SOURCE_DIMENSION
from .contracts import MAX_SOURCE_PIXELS as MAX_SOURCE_PIXELS
from .image_probe import SourceProbe, probe_source, validate_source_size
from .source import SourceOptions, normalize_source


def open_image_bytes(
    data: bytes, source: SourceOptions | dict | None = None, *, _probe: SourceProbe | None = None,
) -> Image.Image:
    """Select and compose a byte-source frame, orient it, then apply its matte.

    The returned image is a detached still. APNG uses explicit source-over
    composition; Pillow decodes the other formats. HTTP callers admit memory
    before using this helper.
    """
    source = normalize_source(source)
    probe = _probe or probe_source(data, source)
    if probe.mime_type == "image/apng":
        image_context = select_apng(data, source.frame, default_image=probe.default_image)
        selected_frame = True
    else:
        image_context = Image.open(io.BytesIO(data))
        selected_frame = False
    with closing(image_context) as image:
        _validate_source_size(image.width, image.height)
        if image.width > probe.width or image.height > probe.height:
            raise ValueError("Opened source dimensions differ from the inspected canvas")
        try:
            if not selected_frame:
                image.seek(source.frame)
            image.load()
        except EOFError as exc:
            raise ValueError("Requested source frame is unavailable or incomplete") from exc
        _validate_source_size(image.width, image.height)
        if image.size != (probe.width, probe.height):
            raise ValueError("Decoded source dimensions differ from the inspected canvas")
        duration = image.info.get("duration")
        duration = (float(duration)
                    if isinstance(duration, (int, float)) and isfinite(duration) and duration >= 0 else None)
        selected = image.convert("RGBA")
    try:
        oriented = ImageOps.exif_transpose(selected)
    finally:
        selected.close()
    try:
        prepared = apply_matte(oriented, source.matte)
    except BaseException:
        oriented.close()
        raise
    if prepared is not oriented:
        oriented.close()
    prepared.info["geometrize_source"] = {
        "mime_type": probe.mime_type, "frame_count": probe.frame_count,
        "frames_truncated": probe.frames_truncated, "default_image": probe.default_image,
        "frame_duration_ms": duration,
    }
    return prepared


def apply_matte(image: Image.Image, matte: tuple[int, int, int] | None) -> Image.Image:
    """Apply to an already selected still; an opaque matte is idempotent."""
    _validate_source_size(image.width, image.height)
    if matte is None:
        return image
    rgba = image.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, (*matte, 255))
    try:
        return Image.alpha_composite(canvas, rgba)
    finally:
        rgba.close()
        canvas.close()


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


def image_from_data_url(data_url: str, source: SourceOptions | dict | None = None) -> Image.Image:
    return open_image_bytes(image_data_url_bytes(data_url), source)


def image_data_url_bytes(data_url: str) -> bytes:
    header, separator, payload = data_url.partition(",")
    if (not separator or not header.lower().startswith(("data:image/", "data:application/octet-stream;"))
            or ";base64" not in header.lower()):
        raise ValueError("Expected a base64 image data URL")
    # Project data URLs allow CR/LF line wrapping. Remove only those characters;
    # validate=True still rejects spaces, tabs and other malformed base64.
    payload = payload.replace("\r", "").replace("\n", "")
    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise ValueError("Expected a valid base64 image data URL") from exc
    return image_bytes


def image_bytes_size(data: bytes, source: SourceOptions | dict | None = None) -> tuple[int, int]:
    """Inspect dimensions before reserving memory for a full image decode."""
    probe = probe_source(data, source)
    return probe.width, probe.height


def average_color(image: Image.Image) -> tuple[int, int, int, int]:
    rgba = image.convert("RGBA")
    if rgba.width == 0 or rgba.height == 0:
        return (255, 255, 255, 255)
    red, green, blue, _alpha = ImageStat.Stat(rgba).mean
    # The native model starts from an opaque RGB average even when the source
    # contains transparency. Exports must use the same background the optimizer
    # evaluated shapes against.
    return (int(red), int(green), int(blue), 255)


def _validate_source_size(width: int, height: int) -> None:
    validate_source_size(width, height)
