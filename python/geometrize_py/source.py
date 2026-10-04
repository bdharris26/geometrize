"""Immutable byte-opening policy, also recorded on each fitted experiment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .colors import RGB, normalize_optional_rgb
from .contracts import SOURCE_MAX_FRAMES


@dataclass(frozen=True)
class SourceOptions:
    frame: int = 0
    matte: RGB | None = None

    def __post_init__(self) -> None:
        if isinstance(self.frame, bool) or not isinstance(self.frame, int) or not 0 <= self.frame < SOURCE_MAX_FRAMES:
            raise ValueError(f"source.frame must be an integer from 0 to {SOURCE_MAX_FRAMES - 1}")
        object.__setattr__(self, "matte", normalize_optional_rgb(self.matte, "source.matte"))

    def to_dict(self) -> dict[str, Any]:
        return {"frame": self.frame, "matte": list(self.matte) if self.matte is not None else None}


def normalize_source(value: Any) -> SourceOptions:
    if isinstance(value, SourceOptions):
        return value
    if value is None:
        return SourceOptions()
    if not isinstance(value, dict) or set(value) - {"frame", "matte"}:
        raise ValueError("source must be an object containing only frame and matte")
    return SourceOptions(value.get("frame", 0), value.get("matte"))
