from __future__ import annotations

import json

import pytest

from geometrize_py.json_memory import JSON_EXPANSION_FACTOR, json_memory_bytes, json_read_memory


@pytest.mark.parametrize("field", ["image", "data_url", "preview_data_url"])
def test_only_explicit_raster_fields_receive_string_allowance(field: str) -> None:
    url = "DATA:IMAGE/PNG;BASE64," + "AAAA\r\n" * 10_000
    body = json.dumps({field: url, "unused": url}).encode()
    scalar_size = len(json.dumps(url)) - 2
    assert json_memory_bytes(body) == len(body) * JSON_EXPANSION_FACTOR
    assert json_memory_bytes(body, image_fields=(field.encode(),)) == scalar_size * 4 + (len(body) - scalar_size) * 32
    assert json_memory_bytes(body, image_fields=(b"other",)) == len(body) * JSON_EXPANSION_FACTOR


def test_raw_read_and_inspected_body_use_distinct_admission() -> None:
    body = b'{"unused":[' + b"[0]," * 10_000 + b"0]}"
    assert json_read_memory(len(body), image_strings=True) == len(body) * 4
    assert json_read_memory(len(body)) == json_memory_bytes(body) == len(body) * 32


def test_multiple_original_and_preview_scalars_share_the_same_policy() -> None:
    url = "data:image/png;base64," + "a" * 40_000
    raw = json.dumps({"source": {"data_url": url}, "branches": [{"preview_data_url": url}] * 8}).encode()
    assert len(raw) * 4 <= json_memory_bytes(raw, image_fields=(b"data_url", b"preview_data_url")) < len(raw) * 5
