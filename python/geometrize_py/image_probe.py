"""Bounded container inspection before Pillow can allocate decoding canvases.

These helpers inspect supported raster headers, not compressed pixel streams.
Pillow remains responsible for frame composition, disposal and pixel decoding.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from .contracts import (
    MAX_REQUEST_BYTES,
    MAX_SOURCE_DIMENSION,
    MAX_SOURCE_PIXELS,
    SOURCE_MAX_DECODE_WORK,
    SOURCE_MAX_FRAMES,
    SOURCE_MAX_METADATA_BYTES,
    SOURCE_MAX_RECORDS,
)
from .source import SourceOptions, normalize_source


def validate_source_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    if max(width, height) > MAX_SOURCE_DIMENSION:
        raise ValueError(f"Image dimensions cannot exceed {MAX_SOURCE_DIMENSION} pixels")
    if width * height > MAX_SOURCE_PIXELS:
        raise ValueError(f"Image cannot exceed {MAX_SOURCE_PIXELS:,} pixels")


@dataclass(frozen=True)
class SourceProbe:
    width: int
    height: int
    mime_type: str
    frame_count: int = 1
    frames_truncated: bool = False
    default_image: bool = False
    decode_work: int = 0
    records: int = 1
    metadata_memory: int = 0

    @property
    def animated(self) -> bool:
        return self.frame_count > 1 or self.frames_truncated


class _Scan:
    def __init__(self, data: bytes) -> None:
        self.data = memoryview(data)
        self.records = 0
        self.metadata_bytes = 0
        self.text_allowance = 0
        self.exif_allowance = 0

    def part(self, offset: int, length: int) -> memoryview:
        if offset < 0 or length < 0 or offset + length > len(self.data):
            raise ValueError("Truncated raster image container")
        return self.data[offset:offset + length]

    def integer(self, offset: int, length: int, order: str = "little", *, signed: bool = False) -> int:
        return int.from_bytes(self.part(offset, length), order, signed=signed)

    def record(self) -> None:
        self.records += 1
        if self.records > SOURCE_MAX_RECORDS:
            raise ValueError("Source container has too many metadata records")

    def metadata(self, length: int, *, compressed_text: bool = False) -> None:
        self.metadata_bytes += length
        if compressed_text:
            # Pillow limits each inflated PNG text chunk to 1 MiB and their
            # cumulative text to 64 MiB. Reserve that bound, not compressed size.
            self.text_allowance += 1024 * 1024
        if max(self.metadata_bytes, self.text_allowance) > SOURCE_MAX_METADATA_BYTES:
            raise ValueError("Source text metadata exceeds the preparation limit")

    def exif(self, payload: memoryview) -> None:
        self.exif_allowance += _exif_memory(payload)
        if self.exif_allowance > SOURCE_MAX_METADATA_BYTES * 16:
            raise ValueError("Source EXIF metadata exceeds the preparation limit")

    def finish(self, width: int, height: int, mime: str, frames: int, source: SourceOptions,
               *, default_image: bool = False, work: int | None = None) -> SourceProbe:
        validate_source_size(width, height)
        if source.frame >= min(frames, SOURCE_MAX_FRAMES):
            raise ValueError("Requested source frame is unavailable")
        work = width * height * (source.frame + 1) if work is None else work
        if work > SOURCE_MAX_DECODE_WORK:
            raise ValueError("Selected source frame exceeds the cumulative decode work limit")
        return SourceProbe(width, height, mime, min(frames, SOURCE_MAX_FRAMES), frames > SOURCE_MAX_FRAMES,
                           default_image, work, self.records,
                           self.records * 512 + self.metadata_bytes * 2 + self.text_allowance * 4
                           + self.exif_allowance)


def probe_source(data: bytes, source: SourceOptions | dict | None = None) -> SourceProbe:
    source = normalize_source(source)
    if not data or len(data) > MAX_REQUEST_BYTES:
        raise ValueError("Encoded source is empty or exceeds the source byte limit")
    scan = _Scan(data)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png(scan, source)
    if data.startswith((b"GIF87a", b"GIF89a")):
        return _gif(scan, source)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return _webp(scan, source)
    if data.startswith(b"\xff\xd8"):
        return _jpeg(scan, source)
    if data.startswith(b"BM"):
        header = scan.integer(14, 4)
        if header == 12:
            width, height = scan.integer(18, 2), scan.integer(20, 2)
        elif header in {40, 52, 56, 64, 108, 124}:
            width, height = scan.integer(18, 4, signed=True), abs(scan.integer(22, 4, signed=True))
        else:
            raise ValueError("Unsupported BMP header")
        return scan.finish(width, height, "image/bmp", 1, source)
    raise ValueError("Supported sources are PNG, JPEG, WebP, BMP, GIF and APNG")


def _jpeg(scan: _Scan, source: SourceOptions) -> SourceProbe:
    offset = 2
    dimensions = None
    while offset < len(scan.data):
        scan.record()
        if scan.integer(offset, 1) != 255:
            raise ValueError("Invalid JPEG marker")
        while scan.integer(offset, 1) == 255:
            offset += 1
        marker = scan.integer(offset, 1)
        offset += 1
        if marker in {0xD9, 0xDA}:
            break
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        length = scan.integer(offset, 2, "big")
        if length < 2:
            raise ValueError("Invalid JPEG segment")
        scan.part(offset, length)
        if marker == 0xE1 and scan.part(offset + 2, min(length - 2, 6)) == b"Exif\0\0":
            scan.exif(scan.part(offset + 8, length - 8))
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            dimensions = scan.integer(offset + 5, 2, "big"), scan.integer(offset + 3, 2, "big")
        scan.metadata(length)
        offset += length
    if dimensions is None:
        raise ValueError("JPEG has no image dimensions")
    return scan.finish(*dimensions, "image/jpeg", 1, source)


def _png(scan: _Scan, source: SourceOptions) -> SourceProbe:
    if scan.part(12, 4) != b"IHDR" or scan.integer(8, 4, "big") != 13:
        raise ValueError("PNG has no valid IHDR")
    width, height = scan.integer(16, 4, "big"), scan.integer(20, 4, "big")
    validate_source_size(width, height)
    offset, declared, frames = 8, None, 0
    seen_data, first_control_before_data, sequence = False, False, 0
    while offset < len(scan.data):
        scan.record()
        length = scan.integer(offset, 4, "big")
        kind = bytes(scan.part(offset + 4, 4))
        payload = offset + 8
        scan.part(payload, length + 4)
        if zlib.crc32(scan.part(offset + 4, length + 4)) != scan.integer(payload + length, 4, "big"):
            raise ValueError("PNG chunk checksum is invalid")
        if kind == b"IHDR":
            if offset != 8 or length != 13:
                raise ValueError("PNG contains a duplicate or out-of-order IHDR")
        elif kind == b"acTL":
            if declared is not None or length != 8 or seen_data:
                raise ValueError("Invalid APNG animation control")
            declared = scan.integer(payload, 4, "big")
            if not 1 <= declared <= 2**31:
                raise ValueError("Invalid APNG frame count")
        elif kind == b"fcTL":
            if declared is None or length != 26:
                raise ValueError("Invalid APNG frame control")
            fw, fh = scan.integer(payload + 4, 4, "big"), scan.integer(payload + 8, 4, "big")
            x, y = scan.integer(payload + 12, 4, "big"), scan.integer(payload + 16, 4, "big")
            if fw <= 0 or fh <= 0 or x + fw > width or y + fh > height:
                raise ValueError("APNG frame extent exceeds its canvas")
            if (scan.integer(payload, 4, "big") != sequence or scan.integer(payload + 24, 1) > 2
                    or scan.integer(payload + 25, 1) > 1):
                raise ValueError("Invalid APNG sequence, disposal or blend operation")
            sequence += 1
            if not frames:
                first_control_before_data = not seen_data
                if first_control_before_data and (fw, fh, x, y) != (width, height, 0, 0):
                    raise ValueError("APNG default frame must fill its canvas")
            frames += 1
        elif kind == b"fdAT":
            if not frames or length < 4 or scan.integer(payload, 4, "big") != sequence:
                raise ValueError("Invalid APNG frame data sequence")
            sequence += 1
        elif kind == b"IDAT":
            seen_data = True
        elif kind in {b"zTXt", b"iTXt", b"tEXt", b"eXIf", b"iCCP"}:
            compressed = kind in {b"zTXt", b"iCCP"}
            if kind == b"eXIf":
                scan.exif(scan.part(payload, length))
            if kind in {b"zTXt", b"iTXt", b"tEXt"}:
                # keyword NUL then compression flag/method, all bounded by the
                # container length. No text inflation or retained copy here.
                keyword_end = next((i for i in range(min(length, 80)) if scan.data[payload + i] == 0), None)
                if keyword_end is None or not 1 <= keyword_end <= 79:
                    raise ValueError("PNG text keyword is invalid")
                if scan.part(payload, keyword_end) == b"Raw profile type exif":
                    # Pillow interprets this legacy text as another TIFF file.
                    # Do not inflate it under the small raw-container lease.
                    raise ValueError("PNG raw-profile EXIF text is unsupported; use standard eXIf metadata")
                if scan.part(payload, keyword_end) == b"exif":
                    if kind != b"tEXt":
                        raise ValueError("PNG compressed EXIF text is unsupported; use standard eXIf metadata")
                    # Pillow treats this one tEXt key as raw EXIF bytes.
                    scan.exif(scan.part(payload + keyword_end + 1, length - keyword_end - 1))
            if kind == b"iTXt":
                if keyword_end + 2 >= length:
                    raise ValueError("PNG text compression fields are incomplete")
                if (scan.data[payload + keyword_end + 1] not in {0, 1}
                        or scan.data[payload + keyword_end + 2] != 0):
                    raise ValueError("PNG text compression flag or method is invalid")
                compressed = keyword_end is not None and keyword_end + 2 < length and scan.data[
                    payload + keyword_end + 1
                ] == 1
            scan.metadata(length, compressed_text=compressed)
        elif kind == b"IEND":
            break
        offset += length + 12
    if not seen_data or (declared is not None and frames != declared):
        raise ValueError("PNG/APNG frame data is incomplete")
    poster = declared is not None and not first_control_before_data
    return scan.finish(width, height, "image/apng" if declared is not None else "image/png",
                       frames + int(poster) if declared is not None else 1, source, default_image=poster)


def _gif(scan: _Scan, source: SourceOptions) -> SourceProbe:
    width, height = scan.integer(6, 2), scan.integer(8, 2)
    validate_source_size(width, height)
    flags = scan.integer(10, 1)
    offset = 13 + (3 * (2 ** ((flags & 7) + 1)) if flags & 128 else 0)
    frames, work, selected_size = 0, 0, (width, height)
    comment_work, comment_total = 0, 0
    while offset < len(scan.data):
        scan.record()
        kind = scan.integer(offset, 1)
        offset += 1
        if kind == 0x3B:
            break
        comment = False
        if kind == 0x21:
            comment = scan.integer(offset, 1) == 0xFE
            offset += 1
        elif kind == 0x2C:
            scan.part(offset, 9)
            x, y, fw, fh = (scan.integer(offset + i, 2) for i in (0, 2, 4, 6))
            if fw <= 0 or fh <= 0:
                raise ValueError("GIF frame dimensions must be positive")
            width, height = max(width, x + fw), max(height, y + fh)
            validate_source_size(width, height)
            frames += 1
            if frames <= source.frame + 1:
                work += width * height
                selected_size = width, height
            if frames > SOURCE_MAX_FRAMES:
                break
            flags = scan.integer(offset + 8, 1)
            offset += 9 + (3 * (2 ** ((flags & 7) + 1)) if flags & 128 else 0)
            scan.part(offset, 1)  # LZW minimum code size
            offset += 1
        else:
            raise ValueError("Invalid GIF block")
        comment_size = 0
        while True:
            scan.record()
            size = scan.integer(offset, 1)
            offset += 1
            scan.part(offset, size)
            offset += size
            if comment:
                comment_size += size
                comment_work += comment_size
                if comment_work > SOURCE_MAX_DECODE_WORK:
                    raise ValueError("GIF comment metadata exceeds the preparation work limit")
            if not size:
                break
        if comment:
            comment_total += comment_size
            comment_work += comment_total
            scan.metadata(comment_size)
    if not frames:
        raise ValueError("GIF has no frames")
    return scan.finish(*selected_size, "image/gif", frames, source, work=work)


def _webp(scan: _Scan, source: SourceOptions) -> SourceProbe:
    end = scan.integer(4, 4) + 8
    if end > len(scan.data) or end < 20:
        raise ValueError("Truncated WebP RIFF container")
    offset, dimensions, frames, extended = 12, None, 0, False
    while offset < end:
        scan.record()
        kind = bytes(scan.part(offset, 4))
        length = scan.integer(offset + 4, 4)
        payload = offset + 8
        if payload + length > end:
            raise ValueError("Truncated WebP chunk")
        if kind == b"VP8X":
            if length != 10 or extended or offset != 12:
                raise ValueError("Invalid WebP extended header")
            dimensions = scan.integer(payload + 4, 3) + 1, scan.integer(payload + 7, 3) + 1
            extended = True
            validate_source_size(*dimensions)
        elif kind in {b"VP8 ", b"VP8L"}:
            coded = _webp_dimensions(scan, kind, payload, length)
            if dimensions is not None and coded != dimensions:
                raise ValueError("WebP bitstream dimensions differ from its canvas")
            dimensions = coded
        elif kind == b"ANMF":
            if dimensions is None or length < 16:
                raise ValueError("Invalid WebP animation frame")
            x, y = scan.integer(payload, 3) * 2, scan.integer(payload + 3, 3) * 2
            fw, fh = scan.integer(payload + 6, 3) + 1, scan.integer(payload + 9, 3) + 1
            if x + fw > dimensions[0] or y + fh > dimensions[1]:
                raise ValueError("WebP frame extent exceeds its canvas")
            frames += 1
            # Demux parses all nested encoded records, even when only frame0
            # will be decoded. Charge all of them without retaining any list.
            nested = payload + 16
            while nested < payload + length:
                scan.record()
                nested_kind = bytes(scan.part(nested, 4))
                block = scan.integer(nested + 4, 4)
                next_block = nested + 8 + block + (block & 1)
                if next_block > payload + length:
                    raise ValueError("Truncated WebP animation payload")
                if nested_kind in {b"VP8 ", b"VP8L"} and _webp_dimensions(
                    scan, nested_kind, nested + 8, block
                ) != (fw, fh):
                    raise ValueError("WebP bitstream dimensions differ from its frame extent")
                nested = next_block
        elif kind in {b"EXIF", b"ICCP", b"XMP "}:
            scan.metadata(length)
            if kind == b"EXIF":
                scan.exif(scan.part(payload, length))
        offset = payload + length + (length & 1)
    if dimensions is None:
        raise ValueError("WebP has no image dimensions")
    return scan.finish(*dimensions, "image/webp", frames or 1, source)


def _webp_dimensions(scan: _Scan, kind: bytes, payload: int, length: int) -> tuple[int, int]:
    if kind == b"VP8 ":
        if length < 10 or scan.part(payload + 3, 3) != b"\x9d\x01\x2a":
            raise ValueError("Invalid WebP lossy header")
        dimensions = scan.integer(payload + 6, 2) & 0x3FFF, scan.integer(payload + 8, 2) & 0x3FFF
    else:
        if length < 5 or scan.integer(payload, 1) != 0x2F:
            raise ValueError("Invalid WebP lossless header")
        bits = scan.integer(payload + 1, 4)
        dimensions = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    validate_source_size(*dimensions)
    return dimensions


def _exif_memory(payload: memoryview) -> int:
    """Bound TIFF references before Pillow expands them into Python objects.

    Out-of-line values may alias the same encoded bytes. Charge each reference,
    since Pillow materializes each tag independently, including on JPEG open.
    Only headers and IFD pointer values are read; no metadata values are decoded.
    """
    if payload[:6] == b"Exif\0\0":
        payload = payload[6:]
    if payload[:2] not in {b"II", b"MM"}:
        raise ValueError("EXIF has an invalid TIFF byte order")
    order = "little" if payload[:2] == b"II" else "big"

    def integer(offset: int, length: int) -> int:
        if offset < 0 or offset + length > len(payload):
            raise ValueError("EXIF contains an out-of-bounds TIFF reference")
        return int.from_bytes(payload[offset:offset + length], order)

    if integer(2, 2) != 42:
        raise ValueError("EXIF must use a classic TIFF header")
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
    pointers = {330, 34665, 34853, 40965}  # SubIFDs, Exif, GPS, interoperability.
    pending = [integer(4, 4)]
    seen: set[int] = set()
    entries = logical_bytes = numeric_values = allowance = 0
    while pending:
        offset = pending.pop()
        if not offset:
            continue
        if offset in seen or len(seen) == 64:
            raise ValueError("EXIF has cyclic, duplicate or too many TIFF directories")
        seen.add(offset)
        count = integer(offset, 2)
        entries += count
        if entries > 4096:
            raise ValueError("EXIF has too many TIFF entries")
        end = offset + 2 + count * 12
        next_ifd = integer(end, 4)
        if next_ifd:
            pending.append(next_ifd)
        for index in range(count):
            entry = offset + 2 + index * 12
            tag, kind, values = integer(entry, 2), integer(entry + 2, 2), integer(entry + 4, 4)
            if kind not in sizes:
                raise ValueError("EXIF contains an unsupported TIFF value type")
            size = values * sizes[kind]
            logical_bytes += size
            if logical_bytes > SOURCE_MAX_METADATA_BYTES:
                raise ValueError("EXIF logical values exceed the preparation limit")
            value_offset = entry + 8 if size <= 4 else integer(entry + 8, 4)
            if value_offset + size > len(payload):
                raise ValueError("EXIF contains an out-of-bounds TIFF value")
            # Dictionary/tag/tuple overhead and duplicated byte/string values;
            # numeric arrays additionally allocate Python scalar objects.
            allowance += 1024 + size * 8
            if kind not in {2, 7}:
                numeric_values += values
                if numeric_values > 1_000_000:
                    raise ValueError("EXIF has too many numeric TIFF values")
                allowance += values * 64
            if tag in pointers:
                if kind not in {4, 13} or values > 64 or len(pending) + values > 64:
                    raise ValueError("EXIF has invalid or too many TIFF directory pointers")
                pending.extend(integer(value_offset + i * 4, 4) for i in range(values))
    return allowance
