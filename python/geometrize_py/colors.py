"""Shared strict RGB byte colors; alpha belongs to the calling policy."""

from __future__ import annotations

from typing import Any

RGB = tuple[int, int, int]


def normalize_rgb(value: Any, label: str) -> RGB:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must be an RGB triple")
    if any(isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255
           for channel in value):
        raise ValueError(f"{label} channels must be integers from 0 to 255")
    return tuple(value)


def normalize_optional_rgb(value: Any, label: str) -> RGB | None:
    return None if value is None else normalize_rgb(value, label)
