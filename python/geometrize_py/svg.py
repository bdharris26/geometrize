from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import escape
from math import cos, pi, sin
from pathlib import Path
from typing import Any


STROKED_SHAPES = {"line", "polyline", "quadratic_bezier"}


def render_svg(width: int, height: int, shapes: Iterable[Mapping[str, Any]]) -> str:
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be greater than zero")

    lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_number(width)}" '
            f'height="{_number(height)}" viewBox="0 0 {_number(width)} {_number(height)}">'
        )
    ]
    for item_id, shape in enumerate(shapes):
        lines.append(f"  {shape_to_svg_element(shape, item_id)}")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def export_svg(width: int, height: int, shapes: Iterable[Mapping[str, Any]]) -> str:
    return render_svg(width, height, shapes)


def save_svg(width: int, height: int, shapes: Iterable[Mapping[str, Any]], output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_svg(width, height, shapes), encoding="utf-8")


def shape_to_svg_element(shape: Mapping[str, Any], item_id: int = 0) -> str:
    shape_type = str(shape["type"])
    data = _mapping(shape["data"])
    style = _style(shape_type, _mapping(shape["color"]), item_id)

    if shape_type == "rectangle":
        x1, y1, x2, y2 = _rect_bounds(data)
        return (
            f'<rect x="{_number(x1)}" y="{_number(y1)}" width="{_number(x2 - x1)}" '
            f'height="{_number(y2 - y1)}" {style} />'
        )
    if shape_type == "rotated_rectangle":
        points = " ".join(f"{_number(x)},{_number(y)}" for x, y in _rotated_rectangle_points(data))
        return f'<polygon points="{escape(points)}" {style} />'
    if shape_type == "triangle":
        return (
            '<polygon points="'
            f'{_number(data["x1"])},{_number(data["y1"])} '
            f'{_number(data["x2"])},{_number(data["y2"])} '
            f'{_number(data["x3"])},{_number(data["y3"])}" {style} />'
        )
    if shape_type == "ellipse":
        return (
            f'<ellipse cx="{_number(data["x"])}" cy="{_number(data["y"])}" '
            f'rx="{_number(data["rx"])}" ry="{_number(data["ry"])}" {style} />'
        )
    if shape_type == "rotated_ellipse":
        return (
            f'<g transform="translate({_number(data["x"])} {_number(data["y"])}) '
            f'rotate({_number(data["angle"])}) scale({_number(data["rx"])} {_number(data["ry"])})">'
            f'<ellipse cx="0" cy="0" rx="1" ry="1" {style} /></g>'
        )
    if shape_type == "circle":
        return (
            f'<circle cx="{_number(data["x"])}" cy="{_number(data["y"])}" '
            f'r="{_number(data["r"])}" {style} />'
        )
    if shape_type == "line":
        return (
            f'<line x1="{_number(data["x1"])}" y1="{_number(data["y1"])}" '
            f'x2="{_number(data["x2"])}" y2="{_number(data["y2"])}" {style} />'
        )
    if shape_type == "polyline":
        points = " ".join(f"{_number(point[0])},{_number(point[1])}" for point in data["points"])
        return f'<polyline points="{escape(points)}" {style} />'
    if shape_type == "quadratic_bezier":
        path = (
            f'M{_number(data["x1"])} {_number(data["y1"])} '
            f'Q {_number(data["cx"])} {_number(data["cy"])} {_number(data["x2"])} {_number(data["y2"])}'
        )
        return f'<path d="{escape(path)}" {style} />'

    raise ValueError(f"unsupported shape type for SVG export: {shape_type}")


def _style(shape_type: str, color: Mapping[str, Any], item_id: int) -> str:
    rgb = f'rgb({int(color["r"])},{int(color["g"])},{int(color["b"])})'
    opacity = _number(int(color["a"]) / 255)
    if shape_type in STROKED_SHAPES:
        return f'id="{item_id}" stroke="{rgb}" stroke-width="1" fill="none" stroke-opacity="{opacity}"'
    return f'id="{item_id}" fill="{rgb}" fill-opacity="{opacity}"'


def _rect_bounds(data: Mapping[str, Any]) -> tuple[float, float, float, float]:
    x1 = float(data["x1"])
    y1 = float(data["y1"])
    x2 = float(data["x2"])
    y2 = float(data["y2"])
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _rotated_rectangle_points(data: Mapping[str, Any]) -> list[tuple[float, float]]:
    x1, y1, x2, y2 = _rect_bounds(data)
    cx = (x2 + x1) / 2
    cy = (y2 + y1) / 2
    ox1 = x1 - cx
    ox2 = x2 - cx
    oy1 = y1 - cy
    oy2 = y2 - cy
    rads = float(data["angle"]) * pi / 180
    cosine = cos(rads)
    sine = sin(rads)
    return [
        (ox1 * cosine - oy1 * sine + cx, ox1 * sine + oy1 * cosine + cy),
        (ox2 * cosine - oy1 * sine + cx, ox2 * sine + oy1 * cosine + cy),
        (ox2 * cosine - oy2 * sine + cx, ox2 * sine + oy2 * cosine + cy),
        (ox1 * cosine - oy2 * sine + cx, ox1 * sine + oy2 * cosine + cy),
    ]


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("shape data must be a mapping")
    return value


def _number(value: Any) -> str:
    numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.6f}".rstrip("0").rstrip(".")
