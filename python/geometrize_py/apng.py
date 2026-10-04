"""Correct APNG source-over using ordinary Pillow PNG frame decoding.

Pillow's animated PNG paste(mask) blends alpha twice. Rebuild only the visited
frames as ordinary PNGs, then use its RGBA alpha_composite primitive. Original
encoded bytes remain the source of the experiment/project.
"""

from __future__ import annotations

import io
import struct
import zlib
from contextlib import closing

from PIL import Image


def _chunk(kind: bytes, payload: bytes | memoryview) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + bytes(payload)
            + struct.pack(">I", zlib.crc32(payload, zlib.crc32(kind))))


def select_apng(data: bytes, frame: int, *, default_image: bool) -> Image.Image:
    """Called only after bounded container validation and decode admission."""
    raw = memoryview(data)
    header = raw[16:29]
    width, height = struct.unpack(">II", header[:8])
    common = []
    orientation = []
    poster = []
    frames = []
    control = None
    parts = []
    animation_index = 0
    selected_animation = frame - int(default_image)
    offset = 8
    while offset < len(raw):
        length = int.from_bytes(raw[offset:offset + 4], "big")
        kind = bytes(raw[offset + 4:offset + 8])
        payload = raw[offset + 8:offset + 8 + length]
        if kind == b"fcTL":
            if control is not None and animation_index - 1 <= selected_animation:
                frames.append((control, parts))
            control = struct.unpack(">IIIIIHHBB", payload)[1:]
            parts = []
            animation_index += 1
        elif kind == b"IDAT":
            if default_image:
                poster.append(payload)
            elif animation_index - 1 <= selected_animation:
                parts.append(payload)
        elif kind == b"fdAT" and animation_index - 1 <= selected_animation:
            parts.append(payload[4:])
        elif kind in {b"PLTE", b"tRNS"}:
            common.append(raw[offset:offset + length + 12])
        elif kind == b"eXIf" or (kind in {b"iTXt", b"tEXt", b"zTXt"} and bytes(payload[:80]).split(b"\0", 1)[0]
                                 in {b"XML:com.adobe.xmp", b"exif"}):
            orientation.append(raw[offset:offset + length + 12])
        offset += length + 12
        if kind == b"IEND":
            break
    if control is not None and animation_index - 1 <= selected_animation:
        frames.append((control, parts))

    def decode(size: tuple[int, int], compressed: list[memoryview], *, selected: bool = False) -> Image.Image:
        encoded = b"".join([b"\x89PNG\r\n\x1a\n", _chunk(b"IHDR", struct.pack(">II", *size) + header[8:]),
                            *common, *(orientation if selected else []),
                            *(_chunk(b"IDAT", part) for part in compressed), _chunk(b"IEND", b"")])
        with closing(Image.open(io.BytesIO(encoded))) as image:
            image.load()
            return image.convert("RGBA")

    if default_image and frame == 0:
        output = decode((width, height), poster, selected=True)
        output.info["duration"] = None
        return output
    canvas = Image.new("RGBA", (width, height))
    previous = None
    try:
        # The APNG output starts transparent; a poster never participates. Only
        # the selected frame decodes orientation text; other ancillary metadata
        # is unnecessary for pixel conversion and is never repeatedly inflated.
        for index, (control, compressed) in enumerate(frames):
            fw, fh, x, y, delay_num, delay_den, dispose, blend = control
            previous = canvas.copy() if dispose == 2 else None
            with closing(decode((fw, fh), compressed, selected=index == selected_animation)) as patch:
                canvas.info = patch.info.copy()
                if blend == 0:  # SOURCE replaces all four channels.
                    canvas.paste(patch, (x, y))
                else:  # OVER includes destination alpha.
                    canvas.alpha_composite(patch, dest=(x, y))
            if index == selected_animation:
                canvas.info["duration"] = delay_num / (delay_den or 100) * 1000
                output, canvas = canvas, None
                return output
            if dispose == 1 or (dispose == 2 and index == 0):
                canvas.paste((0, 0, 0, 0), (x, y, x + fw, y + fh))
            elif dispose == 2:
                canvas.close()
                canvas, previous = previous, None
            if previous is not None:
                previous.close()
                previous = None
        raise ValueError("Requested APNG source frame is unavailable")
    finally:
        if previous is not None:
            previous.close()
        if canvas is not None:
            canvas.close()
