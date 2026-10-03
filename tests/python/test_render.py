from __future__ import annotations

import random

import pytest
from PIL import Image, ImageDraw

from geometrize_py.render import _draw_shape, _scaled_color, render_shapes_to_image

_COLOR = {"r": 231, "g": 37, "b": 119, "a": 147}
_SHAPES = {
    "circle": {"x": 12.75, "y": 8.625, "r": 5.25},
    "ellipse": {"x": 13.75, "y": 9.25, "rx": 8.125, "ry": 4.75},
    "rotated_ellipse": {"x": 13.75, "y": 9.25, "rx": 8.125, "ry": 4.75, "angle": 31.75},
    "rectangle": {"x1": 19.625, "y1": 15.125, "x2": 4.375, "y2": 2.875},
    "rotated_rectangle": {"x1": 19.625, "y1": 15.125, "x2": 4.375, "y2": 2.875, "angle": -24.5},
    "triangle": {"x1": 3.75, "y1": 3.125, "x2": 22.5, "y2": 9.875, "x3": 9.25, "y3": 18.5},
    "line": {"x1": 21.375, "y1": 2.75, "x2": 1.5, "y2": 17.625},
    "quadratic_bezier": {"x1": 2.125, "y1": 17.75, "cx": 11.625, "cy": -8.25, "x2": 23.75, "y2": 17.125},
    "polyline": {"points": [[1.5, 12.25], [6.75, 2.875], [11.125, 16.5], [23.25, 5.375]]},
}


def _reference_render(
    shapes: list[dict], output_size: tuple[int, int], background: tuple[int, int, int, int]
) -> Image.Image:
    """The original full-frame-overlay compositor, retained as a pixel oracle."""
    output_width, output_height = output_size
    image = Image.new("RGBA", output_size, _scaled_color(background))
    for shape in shapes:
        overlay = Image.new("RGBA", output_size, (0, 0, 0, 0))
        _draw_shape(ImageDraw.Draw(overlay, "RGBA"), shape, output_width / 25, output_height / 20)
        image.alpha_composite(overlay)
    return image


def _shape(shape_type: str, offset: tuple[float, float] = (0, 0), alpha: int = 147) -> dict:
    dx, dy = offset
    data = _SHAPES[shape_type]
    translated = {
        key: value + (dx if key.startswith("x") or key == "cx" else dy)
        if isinstance(value, (int, float)) and key not in {"r", "rx", "ry", "angle"}
        else value
        for key, value in data.items()
    }
    if "points" in translated:
        translated["points"] = [[x + dx, y + dy] for x, y in data["points"]]
    return {"type": shape_type, "color": {**_COLOR, "a": alpha}, "data": translated}


@pytest.mark.parametrize("shape_type", _SHAPES)
@pytest.mark.parametrize(
    ("offset", "output_size", "background"),
    [
        ((0, 0), (25, 20), (12, 34, 56, 255)),
        ((-11.5, -5.25), (137, 54), (12, 34, 56, 87)),
        ((19.125, 13.75), (41, 113), (211, 47, 29, 0)),
    ],
    ids=("native-scale", "wide-offscreen", "tall-offscreen-transparent"),
)
def test_render_matches_full_frame_reference_for_every_primitive(
    shape_type: str,
    offset: tuple[float, float],
    output_size: tuple[int, int],
    background: tuple[int, int, int, int],
) -> None:
    shape = _shape(shape_type, offset)
    actual = render_shapes_to_image([shape], 25, 20, background, *output_size)
    reference = _reference_render([shape], output_size, background)

    assert actual.tobytes() == reference.tobytes()


def test_render_matches_full_frame_reference_for_overlapping_translucent_shapes() -> None:
    shapes = [_shape(shape_type, (-2.125, 1.75)) for shape_type in _SHAPES]
    shapes += [_shape("rectangle", alpha=0), _shape("circle", alpha=255)]
    background = (221, 78, 14, 0)
    output_size = (111, 73)

    actual = render_shapes_to_image(shapes, 25, 20, background, *output_size)
    reference = _reference_render(shapes, output_size, background)

    assert actual.tobytes() == reference.tobytes()


def test_render_matches_full_frame_reference_for_mixed_randomized_sequences() -> None:
    randomizer = random.Random(20261003)
    for _ in range(20):
        types = list(_SHAPES)
        randomizer.shuffle(types)
        shapes = []
        for shape_type in types:
            shape = _shape(
                shape_type,
                (randomizer.uniform(-25, 25), randomizer.uniform(-20, 20)),
                alpha=randomizer.choice((0, 1, 77, 128, 254, 255)),
            )
            shape["color"].update({channel: randomizer.randrange(256) for channel in ("r", "g", "b")})
            shapes.append(shape)
        output_size = (randomizer.randrange(10, 100), randomizer.randrange(10, 100))
        background = tuple(randomizer.randrange(256) for _ in range(4))

        actual = render_shapes_to_image(shapes, 25, 20, background, *output_size)
        reference = _reference_render(shapes, output_size, background)

        assert actual.tobytes() == reference.tobytes()


def test_render_shapes_rejects_unknown_shape_type() -> None:
    with pytest.raises(ValueError, match="unknown shape type"):
        render_shapes_to_image(
            [{"type": "mystery", "color": {"r": 0, "g": 0, "b": 0, "a": 255}, "data": {}}],
            10,
            10,
            (255, 255, 255, 255),
            10,
            10,
        )


def test_render_shapes_rejects_non_positive_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions must be positive"):
        render_shapes_to_image([], 0, 10, (255, 255, 255, 255), 10, 10)
