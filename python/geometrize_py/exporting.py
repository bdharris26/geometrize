"""Validated frozen render scenes and a bounded cache of lossless exports."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

from .contracts import (
    MAX_SOURCE_DIMENSION,
    PROJECT_MAX_COORDINATE,
    PROJECT_MAX_GEOMETRY_FACTOR,
    PROJECT_MAX_POINTS,
    PROJECT_MAX_SHAPES,
    PROJECT_MAX_TOTAL_POINTS,
    SHAPE_DATA_FIELDS,
    SHAPE_RADIUS_FIELDS,
    SHAPE_TYPES,
)
from .errors import APIError
from .images import image_to_png_bytes
from .render import export_dimensions, render_shapes_to_image
from .svg import shapes_to_svg


@dataclass(frozen=True)
class RenderScene:
    width: int
    height: int
    background: tuple[int, int, int, int]
    shapes: list[dict[str, Any]]
    attempts: int = 0
    revision: int = 0
    target_digest: str | None = None

    def fingerprint(self) -> str:
        content = json.dumps(
            [self.width, self.height, self.background, self.shapes],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()


def inspect_scene(raw: RenderScene | dict[str, Any]) -> tuple[int, int]:
    """Count shapes and polyline vertices before copying or rendering a scene."""
    if isinstance(raw, RenderScene):
        shapes = raw.shapes
    elif isinstance(raw, dict):
        shapes = raw.get("shapes")
    else:
        shapes = None
    if not isinstance(shapes, list) or len(shapes) > PROJECT_MAX_SHAPES:
        raise APIError("invalid_result", f"Result shapes must be an array of at most {PROJECT_MAX_SHAPES} items")
    total_points = 0
    for index, shape in enumerate(shapes, 1):
        if not isinstance(shape, dict) or not isinstance(shape.get("type"), str) or shape["type"] not in SHAPE_TYPES:
            raise APIError("invalid_result", f"Shape {index} has an unsupported type")
        if shape["type"] != "polyline":
            continue
        data = shape.get("data")
        points = data.get("points") if isinstance(data, dict) else None
        if not isinstance(points, list) or len(points) > PROJECT_MAX_POINTS:
            raise APIError("invalid_result", f"Shape {index} has invalid polyline points")
        total_points += len(points)
        if total_points > PROJECT_MAX_TOTAL_POINTS:
            raise APIError("invalid_result", f"Result cannot exceed {PROJECT_MAX_TOTAL_POINTS} polyline points")
    return len(shapes), total_points


@dataclass(frozen=True)
class ExportArtifact:
    png: bytes
    svg: str
    width: int
    height: int

    @property
    def size_bytes(self) -> int:
        return len(self.png) + len(self.svg.encode("utf-8"))


class ExportCache:
    def __init__(self, max_bytes: int = 64 * 1024 * 1024, max_entries: int = 16) -> None:
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[str, int], ExportArtifact] = OrderedDict()
        self._size_bytes = 0
        self._lock = Lock()

    def get(self, key: tuple[str, int]) -> ExportArtifact | None:
        with self._lock:
            artifact = self._entries.get(key)
            if artifact is not None:
                self._entries.move_to_end(key)
            return artifact

    def store(self, key: tuple[str, int], artifact: ExportArtifact) -> None:
        size = artifact.size_bytes
        if size > self.max_bytes or self.max_entries < 1:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._size_bytes -= previous.size_bytes
            self._entries[key] = artifact
            self._size_bytes += size
            while len(self._entries) > self.max_entries or self._size_bytes > self.max_bytes:
                _key, removed = self._entries.popitem(last=False)
                self._size_bytes -= removed.size_bytes


def build_export(scene: RenderScene, longest: int) -> ExportArtifact:
    width, height = export_dimensions(scene.width, scene.height, longest)
    image = render_shapes_to_image(scene.shapes, scene.width, scene.height, scene.background, width, height)
    return ExportArtifact(
        image_to_png_bytes(image),
        shapes_to_svg(scene.shapes, scene.width, scene.height, scene.background, width, height),
        width,
        height,
    )


def validate_scene(raw: Any) -> RenderScene:
    if not isinstance(raw, dict):
        raise APIError("invalid_result", "Export requires a result object")
    width = _integer(raw.get("width"), 1, MAX_SOURCE_DIMENSION, "result width")
    height = _integer(raw.get("height"), 1, MAX_SOURCE_DIMENSION, "result height")
    background = _color(raw.get("background"), "result background")
    normalized = validate_shapes(raw.get("shapes"), width, height)
    digest = raw.get("target_digest")
    if "target_digest" in raw and (not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None):
        raise APIError("invalid_result", "target_digest must be a SHA256 hexadecimal string")
    return RenderScene(
        width,
        height,
        background,
        normalized,
        _integer(raw.get("attempts", 0), 0, 2**53 - 1, "attempts"),
        _integer(raw.get("revision", len(normalized)), 0, 2**53 - 1, "revision"),
        digest.lower() if digest is not None else None,
    )


def validate_shapes(shapes: Any, width: int = 0, height: int = 0) -> list[dict[str, Any]]:
    """Canonicalize bounded geometry before complete canvas validation."""
    width = _integer(width, 0, MAX_SOURCE_DIMENSION, "result width")
    height = _integer(height, 0, MAX_SOURCE_DIMENSION, "result height")
    inspect_scene({"shapes": shapes})
    longest = max(width, height)
    geometry_limit = PROJECT_MAX_GEOMETRY_FACTOR * longest if longest else PROJECT_MAX_COORDINATE
    return [_shape(shape, index + 1, geometry_limit) for index, shape in enumerate(shapes)]


def _shape(raw: Any, index: int, geometry_limit: float) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str) or raw["type"] not in SHAPE_TYPES:
        raise APIError("invalid_result", f"Shape {index} has an unsupported type")
    name = raw["type"]
    data = raw.get("data")
    if not isinstance(data, dict):
        raise APIError("invalid_result", f"Shape {index} requires geometry data")
    if name == "polyline":
        points = data.get("points")
        if not isinstance(points, list) or len(points) > PROJECT_MAX_POINTS:
            raise APIError("invalid_result", f"Shape {index} has invalid polyline points")
        geometry: dict[str, Any] = {"points": []}
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise APIError("invalid_result", f"Shape {index} has an invalid polyline point")
            geometry["points"].append(
                [_number(point[0], "point x", geometry_limit), _number(point[1], "point y", geometry_limit)]
            )
    else:
        geometry = {
            field: _number(
                data.get(field),
                f"shape {index} {field}",
                PROJECT_MAX_COORDINATE if field == "angle" else geometry_limit,
            )
            for field in SHAPE_DATA_FIELDS[name]
        }
    for field in SHAPE_RADIUS_FIELDS.get(name, ()):
        if geometry[field] < 0:
            raise APIError("invalid_result", f"Shape {index} {field} cannot be negative")
    channels = _color(raw.get("color"), f"shape {index} color")
    shape = {"type": name, "color": dict(zip(("r", "g", "b", "a"), channels, strict=True)), "data": geometry}
    if "score" in raw:
        shape["score"] = _number(raw["score"], f"shape {index} score")
    if "type_id" in raw:
        shape["type_id"] = _integer(raw["type_id"], 0, 2**31 - 1, f"shape {index} type id")
    return shape


def _color(raw: Any, label: str) -> tuple[int, int, int, int]:
    if isinstance(raw, dict):
        raw = [raw.get(channel) for channel in ("r", "g", "b", "a")]
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise APIError("invalid_result", f"{label} requires four RGBA channels")
    return tuple(_integer(channel, 0, 255, label) for channel in raw)


def _integer(value: Any, lower: int, upper: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise APIError("invalid_result", f"{label} must be an integer from {lower} to {upper}")
    return value


def _number(value: Any, label: str, limit: float = PROJECT_MAX_COORDINATE) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise APIError("invalid_result", f"{label} must be a finite number")
    if abs(value) > limit or not math.isfinite(value):
        raise APIError("invalid_result", f"{label} must be a finite number within +/-{limit:g}")
    return float(value)
