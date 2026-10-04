"""Bounded JSON admission estimates, without decoding or copying scalar spans."""

from __future__ import annotations

import re

JSON_MEMORY_FLOOR = 64 * 1024
JSON_EXPANSION_FACTOR = 32
IMAGE_STRING_FACTOR = 4

# A single payload class avoids a regex stack for every escaped CR/LF. Raw
# quotes terminate the span; the guards below reject escaped quote endpoints.
_IMAGE_SCALAR = re.compile(
    rb'"(image|data_url|preview_data_url)"\s*:\s*"'
    rb'((?i:data:(?:image/[^"\\]{0,128}|application/octet-stream);base64,)[A-Za-z0-9+/=\\]*)"'
)


def json_read_memory(body_bytes: int, *, image_strings: bool = False) -> int:
    """Reserve the bounded raw read; inspect its bytes before allowing parsing."""
    factor = IMAGE_STRING_FACTOR if image_strings else JSON_EXPANSION_FACTOR
    return max(JSON_MEMORY_FLOOR, factor * body_bytes)


def json_memory_bytes(body: bytes, *, image_fields: tuple[bytes, ...] = ()) -> int:
    """Only named ASCII raster scalars receive the smaller string allowance."""
    image_bytes = 0
    if image_fields and body.isascii():
        for match in _IMAGE_SCALAR.finditer(body):
            if match.group(1) not in image_fields:
                continue
            start, end = match.span(2)
            # Raw Unicode widens decoded JSON; a Unicode escape can widen a
            # parsed scalar. A trailing backslash may hide a Unicode suffix
            # after a quote that the scanner cannot safely treat as its end.
            if body[end - 1] != 92 and body.find(b"\\u", start, end) < 0:
                image_bytes += end - start
    return max(
        JSON_MEMORY_FLOOR,
        IMAGE_STRING_FACTOR * image_bytes + JSON_EXPANSION_FACTOR * (len(body) - image_bytes),
    )
