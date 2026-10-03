from __future__ import annotations

import re
from xml.etree import ElementTree

import pytest

from geometrize_py.svg import shapes_to_svg

_SVG = "{http://www.w3.org/2000/svg}"
_VALUES = (5.123456789012345, 8.987654321098765, 2.345678901234567, 3.456789012345678)


@pytest.mark.parametrize(
    ("shape_type", "data", "expected_tag", "numeric_attributes"),
    [
        (
            "circle",
            {"x": _VALUES[0], "y": _VALUES[1], "r": _VALUES[2]},
            "circle",
            {"cx": _VALUES[0], "cy": _VALUES[1], "r": _VALUES[2]},
        ),
        (
            "ellipse",
            {"x": _VALUES[0], "y": _VALUES[1], "rx": _VALUES[2], "ry": _VALUES[3]},
            "ellipse",
            {"cx": _VALUES[0], "cy": _VALUES[1], "rx": _VALUES[2], "ry": _VALUES[3]},
        ),
        (
            "rectangle",
            {"x1": _VALUES[1], "y1": _VALUES[3], "x2": _VALUES[0], "y2": _VALUES[2]},
            "rect",
            {"x": _VALUES[0], "y": _VALUES[2], "width": _VALUES[1] - _VALUES[0], "height": _VALUES[3] - _VALUES[2]},
        ),
        (
            "rotated_rectangle",
            {"x1": _VALUES[1], "y1": _VALUES[3], "x2": _VALUES[0], "y2": _VALUES[2], "angle": -23.1234567890123},
            "rect",
            {"x": _VALUES[0], "y": _VALUES[2], "width": _VALUES[1] - _VALUES[0], "height": _VALUES[3] - _VALUES[2]},
        ),
        (
            "triangle",
            {
                "x1": _VALUES[0],
                "y1": _VALUES[1],
                "x2": _VALUES[2],
                "y2": _VALUES[3],
                "x3": 12.9876543210123,
                "y3": 15.0123456789012,
            },
            "polygon",
            {},
        ),
        (
            "line",
            {"x1": _VALUES[0], "y1": _VALUES[1], "x2": _VALUES[2], "y2": _VALUES[3]},
            "line",
            {"x1": _VALUES[0], "y1": _VALUES[1], "x2": _VALUES[2], "y2": _VALUES[3]},
        ),
        (
            "quadratic_bezier",
            {
                "x1": _VALUES[0],
                "y1": _VALUES[1],
                "cx": _VALUES[2],
                "cy": _VALUES[3],
                "x2": 12.9876543210123,
                "y2": 15.0123456789012,
            },
            "path",
            {},
        ),
        ("polyline", {"points": [[_VALUES[0], _VALUES[1]], [_VALUES[2], _VALUES[3]]]}, "polyline", {}),
    ],
)
def test_svg_preserves_all_primitive_coordinates(
    shape_type: str, data: dict, expected_tag: str, numeric_attributes: dict[str, float]
) -> None:
    shape = {"type": shape_type, "color": {"r": 3, "g": 50, "b": 100, "a": 137}, "data": data}
    root = ElementTree.fromstring(shapes_to_svg([shape], 20, 20))
    element = root[0]

    assert element.tag == _SVG + expected_tag
    for attribute, expected in numeric_attributes.items():
        assert float(element.attrib[attribute]) == expected
    if shape_type == "triangle":
        assert _numbers(element.attrib["points"]) == [data[key] for key in ("x1", "y1", "x2", "y2", "x3", "y3")]
    if shape_type == "polyline":
        assert _numbers(element.attrib["points"]) == [value for point in data["points"] for value in point]
    if shape_type == "quadratic_bezier":
        assert _numbers(element.attrib["d"]) == [data[key] for key in ("x1", "y1", "cx", "cy", "x2", "y2")]
    if shape_type == "rotated_rectangle":
        expected_transform = [data["angle"], (data["x1"] + data["x2"]) / 2, (data["y1"] + data["y2"]) / 2]
        assert _numbers(element.attrib["transform"]) == expected_transform
    if shape_type in {"line", "quadratic_bezier", "polyline"}:
        assert element.attrib["vector-effect"] == "non-scaling-stroke"
    assert float(element.attrib.get("stroke-opacity", element.attrib.get("fill-opacity", ""))) == 137 / 255


def test_svg_rotated_ellipse_preserves_its_transform() -> None:
    data = {"x": _VALUES[0], "y": _VALUES[1], "rx": _VALUES[2], "ry": _VALUES[3], "angle": 37.1234567890123}
    shape = {"type": "rotated_ellipse", "color": {"r": 1, "g": 2, "b": 3, "a": 255}, "data": data}
    root = ElementTree.fromstring(shapes_to_svg([shape], 20, 20))
    group = root[0]

    assert group.tag == _SVG + "g"
    assert _numbers(group.attrib["transform"]) == [data[key] for key in ("x", "y", "angle", "rx", "ry")]
    assert group[0].tag == _SVG + "ellipse"


def test_svg_nonuniform_output_preserves_shape_placement_and_circular_radius() -> None:
    circle = {"type": "circle", "color": {"r": 0, "g": 0, "b": 0, "a": 255}, "data": {"x": 5, "y": 8, "r": 3}}
    line = {"type": "line", "color": {"r": 0, "g": 0, "b": 0, "a": 255}, "data": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}}
    root = ElementTree.fromstring(shapes_to_svg([circle, line], 20, 20, output_width=60, output_height=80))

    assert root.attrib["preserveAspectRatio"] == "none"
    assert root[0].tag == _SVG + "ellipse"
    assert [float(root[0].attrib[key]) for key in ("cx", "cy", "rx", "ry")] == [5, 8, 3, 2.25]
    assert root[1].attrib["stroke-width"] == "3"
    assert root[1].attrib["vector-effect"] == "non-scaling-stroke"


def _numbers(value: str) -> list[float]:
    return [float(number) for number in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value)]


def test_shapes_to_svg_exports_basic_shapes() -> None:
    svg = shapes_to_svg(
        [
            {
                "type": "circle",
                "color": {"r": 10, "g": 20, "b": 30, "a": 128},
                "data": {"x": 5, "y": 6, "r": 7},
            },
            {
                "type": "line",
                "color": {"r": 200, "g": 10, "b": 40, "a": 255},
                "data": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
            },
        ],
        16,
        16,
        (1, 2, 3, 255),
    )

    assert '<svg xmlns="http://www.w3.org/2000/svg"' in svg
    assert '<circle cx="5" cy="6" r="7"' in svg
    assert '<line x1="0" y1="0" x2="10" y2="10"' in svg
    assert "rgb(1,2,3)" in svg


def test_shapes_to_svg_can_scale_output_dimensions_with_viewbox() -> None:
    svg = shapes_to_svg([], 2, 3, (1, 2, 3, 255), 200, 300)

    assert 'width="200" height="300" viewBox="0 0 2 3"' in svg


def test_shapes_to_svg_normalizes_reversed_rectangles() -> None:
    svg = shapes_to_svg(
        [
            {
                "type": "rectangle",
                "color": {"r": 10, "g": 20, "b": 30, "a": 255},
                "data": {"x1": 8, "y1": 7, "x2": 2, "y2": 3},
            }
        ],
        10,
        10,
    )

    assert '<rect x="2" y="3" width="6" height="4"' in svg
