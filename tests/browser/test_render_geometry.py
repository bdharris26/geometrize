"""Compare SVG geometry with PNG while allowing different edge antialiasing."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from geometrize_py.render import render_shapes_to_image
from geometrize_py.svg import shapes_to_svg

SOURCE_SIZE = (60, 60)
OUTPUT_SIZE = (120, 80)  # Deliberately nonuniform, to expose viewBox errors.
SHAPES = {
    "circle": {"x": 26.375, "y": 24.125, "r": 10.75},
    "ellipse": {"x": 26.375, "y": 24.125, "rx": 17.25, "ry": 8.875},
    "rotated_ellipse": {"x": 26.375, "y": 24.125, "rx": 17.25, "ry": 8.875, "angle": 32.5},
    "rectangle": {"x1": 44.5, "y1": 38.125, "x2": 10.25, "y2": 12.875},
    "rotated_rectangle": {"x1": 44.5, "y1": 38.125, "x2": 10.25, "y2": 12.875, "angle": -28.75},
    "triangle": {"x1": 8.375, "y1": 11.25, "x2": 49.5, "y2": 22.875, "x3": 25.25, "y3": 46.375},
    "line": {"x1": 7.25, "y1": 10.125, "x2": 49.875, "y2": 45.5},
    "quadratic_bezier": {"x1": 6.375, "y1": 42.125, "cx": 27.75, "cy": -7.375, "x2": 51.25, "y2": 42.625},
    "polyline": {"points": [[7.25, 39.125], [18.875, 11.625], [33.25, 44.375], [50.5, 17.25]]},
}


def test_all_nine_svg_primitives_follow_png_geometry_away_from_antialiased_edges() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        for shape_type, data in SHAPES.items():
            shape = {"type": shape_type, "data": data, "color": {"r": 0, "g": 0, "b": 0, "a": 255}}
            png = render_shapes_to_image([shape], *SOURCE_SIZE, (255, 255, 255, 255), *OUTPUT_SIZE)
            svg = shapes_to_svg([shape], *SOURCE_SIZE, (255, 255, 255, 255), *OUTPUT_SIZE)
            svg_red = page.evaluate(
                """async ({ svg, width, height }) => {
                  const image = new Image();
                  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
                  try {
                    image.src = url;
                    await image.decode();
                    const canvas = document.createElement('canvas');
                    canvas.width = width;
                    canvas.height = height;
                    const context = canvas.getContext('2d');
                    context.drawImage(image, 0, 0);
                    const pixels = context.getImageData(0, 0, width, height).data;
                    return Array.from({ length: width * height }, (_, index) => pixels[index * 4]);
                  } finally {
                    URL.revokeObjectURL(url);
                  }
                }""",
                {"svg": svg, "width": OUTPUT_SIZE[0], "height": OUTPUT_SIZE[1]},
            )
            png_red = png.getchannel("R").tobytes()
            png_mask = _mask(png_red)
            svg_mask = _mask(svg_red)
            assert png_mask and svg_mask, shape_type
            assert _within_edge_band(png_mask, svg_mask, 2), shape_type
            assert _within_edge_band(svg_mask, png_mask, 2), shape_type
            assert _bbox_distance(png_mask, svg_mask) <= 2, shape_type
        browser.close()


def _mask(red: bytes | list[int]) -> set[tuple[int, int]]:
    return {
        (index % OUTPUT_SIZE[0], index // OUTPUT_SIZE[0])
        for index, value in enumerate(red)
        if value < 224
    }


def _within_edge_band(source: set[tuple[int, int]], target: set[tuple[int, int]], radius: int) -> bool:
    return all(
        any((x + dx, y + dy) in target for dx in range(-radius, radius + 1) for dy in range(-radius, radius + 1))
        for x, y in source
    )


def _bbox_distance(left: set[tuple[int, int]], right: set[tuple[int, int]]) -> int:
    def bounds(points: set[tuple[int, int]]) -> tuple[int, int, int, int]:
        xs, ys = zip(*points, strict=True)
        return min(xs), min(ys), max(xs), max(ys)

    return max(abs(a - b) for a, b in zip(bounds(left), bounds(right), strict=True))
