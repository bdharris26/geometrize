from __future__ import annotations

from geometrize_py.svg import shapes_to_svg


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
