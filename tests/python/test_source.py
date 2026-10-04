from __future__ import annotations

import io
import struct
import zlib
from dataclasses import FrozenInstanceError

import pytest
from PIL import Image, features

from geometrize_py.contracts import SOURCE_MAX_DECODE_WORK, app_contract
from geometrize_py.image_probe import probe_source
from geometrize_py.images import apply_matte, image_bytes_size, image_data_url_bytes, open_image_bytes
from geometrize_py.native import RunOptions
from geometrize_py.source import SourceOptions, normalize_source


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def _pixels(size, color) -> bytes:
    return zlib.compress(b"".join(b"\x00" + bytes(color) * size[0] for _ in range(size[1])))


def _exif(orientation=1, *, aliases=0, value_bytes=0, inline_entries=0) -> bytes:
    count = 1 + aliases + inline_entries
    offset = 8 + 2 + count * 12 + 4
    entries = struct.pack("<HHII", 274, 3, 1, orientation)
    entries += b"".join(struct.pack("<HHII", 1000 + i, 7, value_bytes, offset) for i in range(aliases))
    entries += b"".join(struct.pack("<HHII", 2000 + i, 4, 1, i) for i in range(inline_entries))
    return b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", count) + entries + b"\0" * 4 + b"A" * value_bytes


def _with_exif(data: bytes, exif: bytes, image_format: str) -> bytes:
    if image_format in {"PNG", "APNG"}:
        return data[:33] + _chunk(b"eXIf", exif) + data[33:]
    if image_format == "JPEG":
        segment = b"Exif\0\0" + exif
        return data[:2] + b"\xff\xe1" + struct.pack(">H", len(segment) + 2) + segment + data[2:]
    chunk = b"EXIF" + struct.pack("<I", len(exif)) + exif + (b"\0" if len(exif) & 1 else b"")
    probe = probe_source(data)
    extended = bytes((8, 0, 0, 0)) + (probe.width - 1).to_bytes(3, "little")
    extended += (probe.height - 1).to_bytes(3, "little")
    payload = b"WEBPVP8X" + struct.pack("<I", 10) + extended + data[12:] + chunk
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def _apng(frames, *, poster=None, size=(4, 4)) -> bytes:
    raw = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", *size, 8, 6, 0, 0, 0))
    raw += _chunk(b"acTL", struct.pack(">II", len(frames), 0))
    if poster is not None:
        raw += _chunk(b"IDAT", _pixels(size, poster))
    sequence = 0
    for index, (dimensions, offset, color, dispose, blend) in enumerate(frames):
        raw += _chunk(b"fcTL", struct.pack(">IIIIIHHBB", sequence, *dimensions, *offset, 1, 10, dispose, blend))
        sequence += 1
        compressed = _pixels(dimensions, color)
        if index == 0 and poster is None:
            raw += _chunk(b"IDAT", compressed)
        else:
            raw += _chunk(b"fdAT", struct.pack(">I", sequence) + compressed)
            sequence += 1
    return raw + _chunk(b"IEND", b"")


def _gif(frames, *, size=(8, 8)) -> bytes:
    palette = [0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255] + [0] * (768 - 12)
    raw = None
    for index, (dimensions, offset, color, dispose) in enumerate(frames):
        image = Image.new("P", dimensions, color)
        image.putpalette(palette)
        encoded = io.BytesIO()
        image.save(encoded, format="GIF", optimize=False)
        encoded = encoded.getvalue()
        start = 13 + 3 * 2 ** ((encoded[10] & 7) + 1)
        assert encoded[start] == 0x2C
        if raw is None:
            raw = encoded[:6] + struct.pack("<HH", *size) + encoded[10:start]
        raw += b"\x21\xf9\x04" + bytes([(dispose << 2) | 1]) + struct.pack("<H", index + 1) + b"\x00\x00"
        raw += b"\x2c" + struct.pack("<HHHH", *offset, *dimensions) + b"\x00" + encoded[start + 10:-1]
    return raw + b";"


@pytest.mark.parametrize("frame", [True, False, None, -1, 256, 1.0, "1", 10**400])
def test_source_frame_is_strict_bounded_integer(frame) -> None:
    with pytest.raises(ValueError, match="source.frame"):
        SourceOptions(frame)


@pytest.mark.parametrize("value", [False, [], "white", {"mode": "auto"}, {"matte": [0, True, 0]},
                                   {"matte": [0.0, 0, 0]}, {"matte": [0, 0]}, {"matte": [256, 0, 0]}])
def test_source_rejects_wrong_kinds_and_colors(value) -> None:
    with pytest.raises(ValueError, match="source"):
        normalize_source(value)


def test_source_policy_is_immutable_and_legacy_defaults_are_explicit() -> None:
    raw = {"frame": 2, "matte": [1, 2, 3]}
    options = RunOptions(source=raw, background=[4, 5, 6])
    raw["matte"][0] = 99
    assert options.source == SourceOptions(2, (1, 2, 3))
    with pytest.raises(FrozenInstanceError):
        options.source.frame = 3
    result = options.to_native_dict()
    result["source"]["matte"][0] = 99
    assert options.source.matte == (1, 2, 3)
    assert options.background == (4, 5, 6)
    assert RunOptions().source == RunOptions.from_mapping({"source": None}).source == SourceOptions()
    assert app_contract()["defaults"]["source"] == {"frame": 0, "matte": None}
    assert "image/apng" in app_contract()["images"]["mime_types"]
    assert ".apng" in app_contract()["images"]["extensions"]


@pytest.mark.parametrize("color", [True, [], [1, 2], [1, 2, 3, 4], [True, 0, 0], [0.0, 0, 0], [10**400, 0, 0]])
def test_background_is_nullable_rgb(color) -> None:
    with pytest.raises(ValueError, match="background"):
        RunOptions(background=color)


@pytest.mark.parametrize("alpha", [0, 1, 64, 128, 254, 255])
def test_matte_matches_integer_source_over_and_is_idempotent(alpha) -> None:
    image = Image.new("RGBA", (1, 1), (21, 110, 230, alpha))
    matte = (250, 40, 70)
    result = apply_matte(image, matte)
    expected = tuple((source * alpha + background * (255 - alpha) + 127) // 255
                     for source, background in zip((21, 110, 230), matte, strict=True)) + (255,)
    assert result.getpixel((0, 0)) == expected
    assert apply_matte(result, matte).tobytes() == result.tobytes()
    assert image.getpixel((0, 0))[3] == alpha
    assert apply_matte(image, None) is image


@pytest.mark.parametrize("disposal, expected", [(2, (0, 0, 0, 0)), (3, (255, 0, 0, 255))])
def test_gif_composes_partial_frames_with_background_and_previous_disposal(disposal, expected) -> None:
    data = _gif([((8, 8), (0, 0), 1, 1), ((2, 2), (0, 0), 2, disposal), ((2, 2), (6, 6), 3, 1)])
    image = open_image_bytes(data, SourceOptions(2))
    assert image.getpixel((0, 0)) == expected
    assert image.getpixel((3, 3)) == (255, 0, 0, 255)
    assert image.getpixel((6, 6)) == (0, 0, 255, 255)
    assert image.info["geometrize_source"]["frame_duration_ms"] == 30


def test_apng_poster_is_separate_and_over_preserves_correct_alpha_before_matte() -> None:
    data = _apng([((4, 4), (0, 0), (255, 0, 0, 255), 0, 0),
                  ((2, 2), (0, 0), (0, 255, 0, 128), 0, 1)], poster=(255, 255, 255, 255))
    assert open_image_bytes(data).getpixel((0, 0)) == (255, 255, 255, 255)
    for matte in (None, (255, 255, 255)):
        image = open_image_bytes(data, SourceOptions(2, matte))
        assert image.getpixel((0, 0)) == (127, 128, 0, 255)
        assert image.getpixel((3, 3)) == (255, 0, 0, 255)
        assert image.info["geometrize_source"]["default_image"] is True
        assert image.info["geometrize_source"]["frame_count"] == 3
        assert image.info["geometrize_source"]["frame_duration_ms"] == 100


@pytest.mark.parametrize("alpha, expected", [(0, (0, 255, 0, 128)), (128, (85, 170, 0, 192))])
def test_apng_over_handles_transparent_destination(alpha, expected) -> None:
    data = _apng([((4, 4), (0, 0), (255, 0, 0, alpha), 0, 0),
                  ((2, 2), (0, 0), (0, 255, 0, 128), 0, 1)])
    assert open_image_bytes(data, SourceOptions(1)).getpixel((0, 0)) == expected


@pytest.mark.parametrize("dispose, expected", [(1, (0, 0, 0, 0)), (2, (255, 0, 0, 255))])
def test_apng_disposal_restores_background_or_previous_rectangle(dispose, expected) -> None:
    data = _apng([((4, 4), (0, 0), (255, 0, 0, 255), 0, 0),
                  ((2, 2), (0, 0), (0, 255, 0, 255), dispose, 0),
                  ((2, 2), (2, 2), (0, 0, 255, 255), 0, 0)])
    image = open_image_bytes(data, SourceOptions(2))
    assert image.getpixel((0, 0)) == expected
    assert image.getpixel((3, 3)) == (0, 0, 255, 255)


def test_apng_first_previous_disposal_and_over_never_use_poster_as_animation_background() -> None:
    data = _apng([((2, 2), (0, 0), (255, 0, 0, 128), 2, 1),
                  ((2, 2), (2, 2), (0, 255, 0, 128), 0, 1)], poster=(0, 0, 255, 255))
    first = open_image_bytes(data, SourceOptions(1))
    assert first.getpixel((0, 0)) == (255, 0, 0, 128)
    assert first.getpixel((3, 3)) == (0, 0, 0, 0)
    after = open_image_bytes(data, SourceOptions(2))
    assert after.getpixel((0, 0)) == (0, 0, 0, 0)
    assert after.getpixel((3, 3)) == (0, 255, 0, 128)


@pytest.mark.skipif(not features.check("webp"), reason="animated WebP codec is unavailable")
def test_webp_lossless_selected_alpha_and_duration_are_from_loaded_frame() -> None:
    first, second = Image.new("RGBA", (4, 4), (255, 0, 0, 128)), Image.new("RGBA", (4, 4), (0, 255, 0, 64))
    stream = io.BytesIO()
    first.save(stream, format="WEBP", save_all=True, append_images=[second], lossless=True, duration=[70, 90])
    image = open_image_bytes(stream.getvalue(), SourceOptions(1))
    assert image.getpixel((0, 0)) == (0, 255, 0, 64)
    assert image.info["geometrize_source"]["frame_duration_ms"] == 90


def test_long_animation_has_bounded_selectable_prefix_and_first_frame_still_works() -> None:
    data = _gif([((2, 2), (0, 0), 1 + i % 3, 1) for i in range(300)], size=(2, 2))
    for frame in (0, 255):
        info = probe_source(data, SourceOptions(frame))
        assert info.frame_count == 256 and info.frames_truncated
        assert open_image_bytes(data, SourceOptions(frame)).size == (2, 2)


def test_cumulative_canvas_work_is_rejected_before_pillow_open(monkeypatch) -> None:
    data = _gif([((1, 1), (0, 0), 1, 1), ((1, 1), (0, 0), 2, 1)], size=(8192, 8192))
    assert probe_source(data).decode_work == 8192 * 8192 < SOURCE_MAX_DECODE_WORK
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: pytest.fail("Unadmitted frames were opened"))
    with pytest.raises(ValueError, match="cumulative decode work"):
        open_image_bytes(data, SourceOptions(1))


@pytest.mark.parametrize("kind", ["duplicate_ihdr", "long_text_keyword", "extent"])
def test_hostile_png_metadata_is_rejected_before_open(kind, monkeypatch) -> None:
    header = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    if kind == "duplicate_ihdr":
        extra = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 1024, 8, 6, 0, 0, 0))
    elif kind == "long_text_keyword":
        extra = _chunk(b"iTXt", b"k" * 80 + b"\0\1\0\0\0" + zlib.compress(b"a" * (1024 * 1024 - 1)))
    else:
        extra = _chunk(b"acTL", struct.pack(">II", 1, 0)) + _chunk(
            b"fcTL", struct.pack(">IIIIIHHBB", 0, 1024, 1024, 0, 0, 1, 10, 0, 0),
        )
    data = b"\x89PNG\r\n\x1a\n" + header + extra + _chunk(b"IDAT", _pixels((1, 1), (0, 0, 0, 255)))
    data += _chunk(b"IEND", b"")
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: pytest.fail("Unsafe PNG was opened"))
    with pytest.raises(ValueError):
        open_image_bytes(data)


def test_header_probe_does_not_invoke_pillow_and_unknown_upload_bytes_are_unchanged(monkeypatch) -> None:
    source = io.BytesIO()
    Image.new("RGB", (7, 3)).save(source, format="PNG")
    data = source.getvalue()
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: pytest.fail("Header probe initialized decoder"))
    assert image_bytes_size(data) == (7, 3)
    import base64
    assert image_data_url_bytes("data:application/octet-stream;base64," + base64.b64encode(data).decode()) == data


@pytest.mark.parametrize("image_format, frame", [("PNG", 0), ("JPEG", 0), ("WEBP", 0), ("APNG", 1)])
def test_standard_exif_is_admitted_and_applied_after_frame_selection(image_format, frame) -> None:
    if image_format == "WEBP" and not features.check("webp"):
        pytest.skip("WebP codec is unavailable")
    if image_format == "APNG":
        data = _apng([((4, 2), (0, 0), (255, 0, 0, 255), 0, 0),
                      ((2, 1), (0, 0), (0, 255, 0, 255), 0, 0)], size=(4, 2))
    else:
        pixels = Image.new("RGB", (4, 2), (255, 0, 0))
        pixels.putpixel((0, 0), (0, 255, 0))
        output = io.BytesIO()
        pixels.save(output, format=image_format)
        data = output.getvalue()
    expected = open_image_bytes(data, SourceOptions(frame)).transpose(Image.Transpose.ROTATE_270)
    data = _with_exif(data, _exif(6), image_format)
    assert probe_source(data, SourceOptions(frame)).metadata_memory >= 1024
    with open_image_bytes(data, SourceOptions(frame)) as prepared:
        assert prepared.size == (2, 4)
        assert prepared.tobytes() == expected.tobytes()


@pytest.mark.parametrize("image_format", ["PNG", "JPEG", "WEBP", "APNG"])
def test_exif_aliases_are_charged_per_logical_value_without_loading_pillow(image_format, monkeypatch) -> None:
    if image_format == "WEBP" and not features.check("webp"):
        pytest.skip("WebP codec is unavailable")
    if image_format == "APNG":
        data = _apng([((1, 1), (0, 0), (0, 0, 0, 255), 0, 0)], size=(1, 1))
    else:
        output = io.BytesIO()
        Image.new("RGB", (1, 1)).save(output, format=image_format)
        data = output.getvalue()
    aliases, value_bytes = 32, 1024
    data = _with_exif(data, _exif(aliases=aliases, value_bytes=value_bytes), image_format)
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: pytest.fail("Probe loaded EXIF in Pillow"))
    assert probe_source(data).metadata_memory >= aliases * value_bytes * 8


@pytest.mark.parametrize("exif", [
    _exif(inline_entries=4096),
    _exif(aliases=300, value_bytes=262144),
    b"II*\0\xff\xff\xff\xff",
    b"II*\0\x08\0\0\0\x01\0" + struct.pack("<HHII", 34665, 4, 1, 8) + b"\0" * 4,
], ids=["entries", "aliased-values", "offset", "directory-cycle"])
def test_unsafe_exif_is_rejected_before_decoder_open(exif, monkeypatch) -> None:
    output = io.BytesIO()
    Image.new("RGB", (1, 1)).save(output, format="PNG")
    data = _with_exif(output.getvalue(), exif, "PNG")
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: pytest.fail("Unsafe EXIF initialized Pillow"))
    with pytest.raises(ValueError, match="EXIF"):
        open_image_bytes(data)


@pytest.mark.parametrize("kind", [b"tEXt", b"zTXt", b"iTXt"])
def test_legacy_png_exif_text_is_rejected_before_inflation(kind, monkeypatch) -> None:
    keyword = b"Raw profile type exif\0"
    text = b"\nexif\n      26\n" + _exif(6).hex().encode("ascii")
    if kind == b"zTXt":
        text = b"\0" + zlib.compress(text)
    elif kind == b"iTXt":
        text = b"\1\0\0\0" + zlib.compress(text)
    output = io.BytesIO()
    Image.new("RGB", (1, 1)).save(output, format="PNG")
    raw = output.getvalue()
    data = raw[:33] + _chunk(kind, keyword + text) + raw[33:]
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: pytest.fail("Legacy EXIF text initialized Pillow"))
    with pytest.raises(ValueError, match="raw-profile EXIF text"):
        open_image_bytes(data)


@pytest.mark.parametrize("flag, method", [(2, 0), (255, 0), (1, 1)])
def test_nonstandard_itxt_compression_is_rejected_before_open(flag, method, monkeypatch) -> None:
    output = io.BytesIO()
    Image.new("RGB", (1, 1)).save(output, format="PNG")
    raw = output.getvalue()
    metadata = b"K\0" + bytes((flag, method)) + b"\0\0" + zlib.compress(b"A" * 1_000_000)
    data = raw[:33] + _chunk(b"iTXt", metadata) + raw[33:]
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: pytest.fail("Invalid text initialized Pillow"))
    with pytest.raises(ValueError, match="compression flag or method"):
        open_image_bytes(data)


@pytest.mark.parametrize("apng", [False, True])
def test_png_raw_exif_text_is_guarded_and_selected_frame_orientation_is_preserved(apng) -> None:
    if apng:
        raw = _apng([((4, 2), (0, 0), (255, 0, 0, 255), 0, 0),
                     ((2, 1), (0, 0), (0, 255, 0, 255), 0, 0)], size=(4, 2))
        source = SourceOptions(1)
    else:
        output = io.BytesIO()
        Image.new("RGB", (4, 2), (255, 0, 0)).save(output, format="PNG")
        raw, source = output.getvalue(), SourceOptions()
    expected = open_image_bytes(raw, source).transpose(Image.Transpose.ROTATE_270)
    exif = _exif(6, aliases=32, value_bytes=1024)
    data = raw[:33] + _chunk(b"tEXt", b"exif\0Exif\0\0" + exif) + raw[33:]
    assert probe_source(data, source).metadata_memory >= 32 * 1024 * 8
    with open_image_bytes(data, source) as image:
        assert image.tobytes() == expected.tobytes()
        assert image.size == (2, 4)


@pytest.mark.parametrize("kind", [b"zTXt", b"iTXt"])
def test_compressed_exif_text_is_rejected_before_pillow_opens_it(kind, monkeypatch) -> None:
    output = io.BytesIO()
    Image.new("RGB", (1, 1)).save(output, format="PNG")
    raw = output.getvalue()
    control = b"\0" if kind == b"zTXt" else b"\1\0\0\0"
    data = raw[:33] + _chunk(kind, b"exif\0" + control + zlib.compress(_exif(6))) + raw[33:]
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: pytest.fail("EXIF strings initialized Pillow"))
    with pytest.raises(ValueError, match="compressed EXIF text"):
        open_image_bytes(data)


@pytest.mark.parametrize("pointer", [330, 34665, 34853, 40965, None])
@pytest.mark.parametrize("order, signature", [("<", b"II*\0"), (">", b"MM\0*")])
def test_exif_nested_directory_values_and_next_links_are_included_in_admission(pointer, order, signature) -> None:
    # Two IFDs share one encoded payload among four logical values. Traverse
    # pointer tags or the next-directory link in either TIFF byte order.
    if pointer is None:
        first = struct.pack(order + "H", 0) + struct.pack(order + "I", 14)
        second_offset = 14
    else:
        first = struct.pack(order + "H", 1) + struct.pack(order + "HHII", pointer, 4, 1, 26) + b"\0" * 4
        second_offset = 26
    payload_offset = second_offset + 2 + 4 * 12 + 4
    entries = b"".join(struct.pack(order + "HHII", 1000 + i, 7, 1024, payload_offset) for i in range(4))
    exif = signature + struct.pack(order + "I", 8) + first
    exif += struct.pack(order + "H", 4) + entries + b"\0" * 4 + b"A" * 1024
    output = io.BytesIO()
    Image.new("RGB", (1, 1)).save(output, format="PNG")
    data = _with_exif(output.getvalue(), exif, "PNG")
    assert probe_source(data).metadata_memory >= 4 * 1024 * 8
