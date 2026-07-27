from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

Shape = Mapping[str, Any]


def shapes_to_svg(
    shapes: Sequence[Shape],
    width: int,
    height: int,
    background: tuple[int, int, int, int] | None = None,
    output_width: int | None = None,
    output_height: int | None = None,
) -> str:
    output_width = width if output_width is None else output_width
    output_height = height if output_height is None else output_height
    parts = [
        '<?xml version="1.0" standalone="no"?>',
        (
            '<svg xmlns="http://www.w3.org/2000/svg" version="1.2" '
            f'baseProfile="tiny" width="{output_width}" height="{output_height}" viewBox="0 0 {width} {height}">'
        ),
    ]
    if background is not None:
        parts.append(
            f'<rect x="0" y="0" width="{width}" height="{height}" '
            f'fill="{_rgb(background)}" fill-opacity="{_alpha(background)}" />'
        )
    for index, shape in enumerate(shapes):
        parts.append(_shape_to_svg(shape, index))
    parts.append("</svg>")
    return "\n".join(parts)


def _shape_to_svg(shape: Shape, index: int) -> str:
    shape_type = str(shape.get("type", ""))
    color = _shape_color(shape)
    style = _style(shape_type, color, index)
    data = shape.get("data", {})

    if shape_type == "circle":
        return f'<circle cx="{_num(data["x"])}" cy="{_num(data["y"])}" r="{_num(data["r"])}" {style} />'
    if shape_type == "ellipse":
        return (
            f'<ellipse cx="{_num(data["x"])}" cy="{_num(data["y"])}" '
            f'rx="{_num(data["rx"])}" ry="{_num(data["ry"])}" {style} />'
        )
    if shape_type == "rotated_ellipse":
        return (
            f'<g transform="translate({_num(data["x"])} {_num(data["y"])}) '
            f'rotate({_num(data["angle"])}) scale({_num(data["rx"])} {_num(data["ry"])})">'
            f'<ellipse cx="0" cy="0" rx="1" ry="1" {style} /></g>'
        )
    if shape_type == "rectangle":
        x1, y1, x2, y2 = data["x1"], data["y1"], data["x2"], data["y2"]
        x, y, rect_width, rect_height = _rect_bounds(x1, y1, x2, y2)
        return (
            f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(rect_width)}" '
            f'height="{_num(rect_height)}" {style} />'
        )
    if shape_type == "rotated_rectangle":
        x1, y1, x2, y2 = data["x1"], data["y1"], data["x2"], data["y2"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        x, y, rect_width, rect_height = _rect_bounds(x1, y1, x2, y2)
        return (
            f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(rect_width)}" '
            f'height="{_num(rect_height)}" transform="rotate({_num(data["angle"])} {_num(cx)} {_num(cy)})" {style} />'
        )
    if shape_type == "triangle":
        points = (
            f'{_num(data["x1"])},{_num(data["y1"])} '
            f'{_num(data["x2"])},{_num(data["y2"])} '
            f'{_num(data["x3"])},{_num(data["y3"])}'
        )
        return f'<polygon points="{points}" {style} />'
    if shape_type == "line":
        return (
            f'<line x1="{_num(data["x1"])}" y1="{_num(data["y1"])}" '
            f'x2="{_num(data["x2"])}" y2="{_num(data["y2"])}" {style} />'
        )
    if shape_type == "quadratic_bezier":
        path = (
            f'M{_num(data["x1"])} {_num(data["y1"])} '
            f'Q {_num(data["cx"])} {_num(data["cy"])} {_num(data["x2"])} {_num(data["y2"])}'
        )
        return f'<path d="{path}" {style} />'
    if shape_type == "polyline":
        points = " ".join(f'{_num(x)},{_num(y)}' for x, y in data.get("points", []))
        return f'<polyline points="{escape(points)}" {style} />'
    raise ValueError(f"Cannot export unknown shape type '{shape_type}'")


def _shape_color(shape: Shape) -> tuple[int, int, int, int]:
    color = shape.get("color", {})
    return (_channel(color["r"]), _channel(color["g"]), _channel(color["b"]), _channel(color["a"]))


def _style(shape_type: str, color: tuple[int, int, int, int], index: int) -> str:
    if shape_type in {"line", "polyline", "quadratic_bezier"}:
        return (
            f'id="shape-{index}" stroke="{_rgb(color)}" stroke-width="1" '
            f'stroke-opacity="{_alpha(color)}" fill="none"'
        )
    return f'id="shape-{index}" fill="{_rgb(color)}" fill-opacity="{_alpha(color)}"'


def _rgb(color: tuple[int, int, int, int]) -> str:
    return f"rgb({color[0]},{color[1]},{color[2]})"


def _alpha(color: tuple[int, int, int, int]) -> str:
    return f"{color[3] / 255:.4g}"


def _num(value: float) -> str:
    return f"{float(value):.4g}"


def _rect_bounds(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float, float]:
    return (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))


def _channel(value: Any) -> int:
    return max(0, min(255, int(value)))
