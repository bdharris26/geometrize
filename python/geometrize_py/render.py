from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from PIL import Image
from PIL import ImageDraw

Shape = Mapping[str, Any]


def export_dimensions(width: int, height: int, longest: int) -> tuple[int, int]:
    source_longest = max(width, height)
    if source_longest <= 0:
        return (width, height)
    scale = longest / source_longest
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def render_shapes_to_image(
    shapes: Sequence[Shape],
    width: int,
    height: int,
    background: tuple[int, int, int, int],
    output_width: int,
    output_height: int,
) -> Image.Image:
    if width <= 0 or height <= 0 or output_width <= 0 or output_height <= 0:
        raise ValueError("Image dimensions must be positive")
    scale_x = output_width / width
    scale_y = output_height / height
    image = Image.new("RGBA", (output_width, output_height), _scaled_color(background))
    for shape in shapes:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        _draw_shape(draw, shape, scale_x, scale_y)
        image.alpha_composite(overlay)
    return image


def _draw_shape(draw: ImageDraw.ImageDraw, shape: Shape, scale_x: float, scale_y: float) -> None:
    shape_type = str(shape.get("type", ""))
    data = shape.get("data", {})
    color = _shape_color(shape)
    line_width = max(1, round(min(abs(scale_x), abs(scale_y))))

    if shape_type == "circle":
        x, y, r = _sx(data["x"], scale_x), _sy(data["y"], scale_y), data["r"] * min(scale_x, scale_y)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
    elif shape_type == "ellipse":
        _draw_ellipse(draw, data["x"], data["y"], data["rx"], data["ry"], 0, scale_x, scale_y, color)
    elif shape_type == "rotated_ellipse":
        _draw_ellipse(draw, data["x"], data["y"], data["rx"], data["ry"], data["angle"], scale_x, scale_y, color)
    elif shape_type == "rectangle":
        draw.rectangle(_rect(data["x1"], data["y1"], data["x2"], data["y2"], scale_x, scale_y), fill=color)
    elif shape_type == "rotated_rectangle":
        draw.polygon(_rotated_rect_points(data, scale_x, scale_y), fill=color)
    elif shape_type == "triangle":
        draw.polygon(
            [
                (_sx(data["x1"], scale_x), _sy(data["y1"], scale_y)),
                (_sx(data["x2"], scale_x), _sy(data["y2"], scale_y)),
                (_sx(data["x3"], scale_x), _sy(data["y3"], scale_y)),
            ],
            fill=color,
        )
    elif shape_type == "line":
        draw.line(
            [(_sx(data["x1"], scale_x), _sy(data["y1"], scale_y)), (_sx(data["x2"], scale_x), _sy(data["y2"], scale_y))],
            fill=color,
            width=line_width,
        )
    elif shape_type == "quadratic_bezier":
        draw.line(_quadratic_points(data, scale_x, scale_y), fill=color, width=line_width)
    elif shape_type == "polyline":
        points = [(_sx(x, scale_x), _sy(y, scale_y)) for x, y in data.get("points", [])]
        if len(points) >= 2:
            draw.line(points, fill=color, width=line_width)
    else:
        raise ValueError(f"Cannot render unknown shape type '{shape_type}'")


def _draw_ellipse(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    rx: float,
    ry: float,
    angle: float,
    scale_x: float,
    scale_y: float,
    color: tuple[int, int, int, int],
) -> None:
    if not angle:
        draw.ellipse(
            (
                _sx(x - rx, scale_x),
                _sy(y - ry, scale_y),
                _sx(x + rx, scale_x),
                _sy(y + ry, scale_y),
            ),
            fill=color,
        )
        return
    points = []
    radians = math.radians(angle)
    for index in range(48):
        theta = math.tau * index / 48
        px = math.cos(theta) * rx
        py = math.sin(theta) * ry
        rotated_x = x + px * math.cos(radians) - py * math.sin(radians)
        rotated_y = y + px * math.sin(radians) + py * math.cos(radians)
        points.append((_sx(rotated_x, scale_x), _sy(rotated_y, scale_y)))
    draw.polygon(points, fill=color)


def _rotated_rect_points(data: Mapping[str, Any], scale_x: float, scale_y: float) -> list[tuple[float, float]]:
    x1, y1, x2, y2 = data["x1"], data["y1"], data["x2"], data["y2"]
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half_width, half_height = abs(x2 - x1) / 2, abs(y2 - y1) / 2
    radians = math.radians(data["angle"])
    points = []
    for px, py in [(-half_width, -half_height), (half_width, -half_height), (half_width, half_height), (-half_width, half_height)]:
        rotated_x = cx + px * math.cos(radians) - py * math.sin(radians)
        rotated_y = cy + px * math.sin(radians) + py * math.cos(radians)
        points.append((_sx(rotated_x, scale_x), _sy(rotated_y, scale_y)))
    return points


def _quadratic_points(data: Mapping[str, Any], scale_x: float, scale_y: float) -> list[tuple[float, float]]:
    points = []
    for index in range(33):
        t = index / 32
        inv = 1 - t
        x = inv * inv * data["x1"] + 2 * inv * t * data["cx"] + t * t * data["x2"]
        y = inv * inv * data["y1"] + 2 * inv * t * data["cy"] + t * t * data["y2"]
        points.append((_sx(x, scale_x), _sy(y, scale_y)))
    return points


def _shape_color(shape: Shape) -> tuple[int, int, int, int]:
    color = shape.get("color", {})
    return _scaled_color((int(color["r"]), int(color["g"]), int(color["b"]), int(color["a"])))


def _scaled_color(color: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return tuple(_channel(value) for value in color)


def _channel(value: int) -> int:
    return max(0, min(255, int(value)))


def _rect(x1: float, y1: float, x2: float, y2: float, scale_x: float, scale_y: float) -> tuple[float, float, float, float]:
    return (
        min(_sx(x1, scale_x), _sx(x2, scale_x)),
        min(_sy(y1, scale_y), _sy(y2, scale_y)),
        max(_sx(x1, scale_x), _sx(x2, scale_x)),
        max(_sy(y1, scale_y), _sy(y2, scale_y)),
    )


def _sx(value: float, scale: float) -> float:
    return float(value) * scale


def _sy(value: float, scale: float) -> float:
    return float(value) * scale
