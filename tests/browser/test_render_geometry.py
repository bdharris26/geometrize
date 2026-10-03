"""Compare SVG geometry with PNG while allowing different edge antialiasing."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from geometrize_py.render import render_shapes_to_image
from geometrize_py.svg import shapes_to_svg

SOURCE_SIZE = (60, 60)
OUTPUT_SIZE = (120, 80)  # Deliberately nonuniform, to expose viewBox errors.
PREVIEW_SOURCE = (Path(__file__).resolve().parents[2] / "python/geometrize_py/static/preview.js").read_text(
    encoding="utf-8"
)
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
        _load_preview(page)
        for shape_type, data in SHAPES.items():
            shape = {"type": shape_type, "data": data, "color": {"r": 0, "g": 0, "b": 0, "a": 255}}
            png = render_shapes_to_image([shape], *SOURCE_SIZE, (255, 255, 255, 255), *OUTPUT_SIZE)
            svg = shapes_to_svg([shape], *SOURCE_SIZE, (255, 255, 255, 255), *OUTPUT_SIZE)
            svg_red = _svg_red(page, svg, OUTPUT_SIZE)
            png_red = png.getchannel("R").tobytes()
            png_mask = _mask(png_red, OUTPUT_SIZE[0])
            svg_mask = _mask(svg_red, OUTPUT_SIZE[0])
            assert png_mask and svg_mask, shape_type
            assert _within_edge_band(png_mask, svg_mask, 2), shape_type
            assert _within_edge_band(svg_mask, png_mask, 2), shape_type
            assert _bbox_distance(png_mask, svg_mask) <= 2, shape_type

            # The live preview uses continuous Canvas paths at source size.
            # Compare it with the same-size SVG before the export-scale rules.
            source_svg = shapes_to_svg([shape], *SOURCE_SIZE, (255, 255, 255, 255))
            canvas_mask = _mask(_canvas_red(page, [shape], SOURCE_SIZE), SOURCE_SIZE[0])
            source_svg_mask = _mask(_svg_red(page, source_svg, SOURCE_SIZE), SOURCE_SIZE[0])
            assert canvas_mask and source_svg_mask, shape_type
            assert _within_edge_band(canvas_mask, source_svg_mask, 2), shape_type
            assert _within_edge_band(source_svg_mask, canvas_mask, 2), shape_type
            assert _bbox_distance(canvas_mask, source_svg_mask) <= 2, shape_type
        browser.close()


def _load_preview(page: Page) -> None:
    page.evaluate(
        """async (source) => {
          const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
          try { window.PreviewForTest = (await import(url)).Preview; }
          finally { URL.revokeObjectURL(url); }
        }""",
        PREVIEW_SOURCE,
    )


def test_translucent_overlaps_have_matching_canvas_and_svg_interior_colors() -> None:
    shapes = [
        {
            "type": "rectangle",
            "data": {"x1": 10, "y1": 10, "x2": 35, "y2": 35},
            "color": {"r": 0, "g": 0, "b": 0, "a": 128},
        },
        {
            "type": "circle",
            "data": {"x": 30, "y": 30, "r": 16},
            "color": {"r": 0, "g": 0, "b": 0, "a": 128},
        },
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _load_preview(page)
        canvas_red = _canvas_red(page, shapes, SOURCE_SIZE)
        svg_red = _svg_red(page, shapes_to_svg(shapes, *SOURCE_SIZE, (255, 255, 255, 255)), SOURCE_SIZE)
        png_red = render_shapes_to_image(shapes, *SOURCE_SIZE, (255, 255, 255, 255), *SOURCE_SIZE).getchannel(
            "R"
        ).tobytes()
        samples = ((2, 2, (255, 255)), (12, 12, (125, 130)), (42, 30, (125, 130)), (30, 30, (62, 67)))
        for x, y, expected_range in samples:
            index = y * SOURCE_SIZE[0] + x
            assert expected_range[0] <= canvas_red[index] <= expected_range[1]
            assert abs(canvas_red[index] - svg_red[index]) <= 1
            assert abs(canvas_red[index] - png_red[index]) <= 1
        browser.close()


def _svg_red(page: Page, svg: str, size: tuple[int, int]) -> list[int]:
    return page.evaluate(
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
        {"svg": svg, "width": size[0], "height": size[1]},
    )


def _canvas_red(page: Page, shapes: list[dict], size: tuple[int, int]) -> list[int]:
    return page.evaluate(
        """({ shapes, width, height }) => {
          const sourceStage = document.createElement('div');
          const resultStage = document.createElement('div');
          for (const stage of [sourceStage, resultStage]) {
            stage.style.width = '200px';
            stage.style.height = '200px';
            document.body.append(stage);
          }
          const sourceImage = document.createElement('img');
          const resultImage = document.createElement('img');
          const resultCanvas = document.createElement('canvas');
          const zoomOutput = document.createElement('span');
          sourceStage.append(sourceImage);
          resultStage.append(resultImage, resultCanvas);
          try {
            const preview = new window.PreviewForTest({
              sourceStage, resultStage, sourceImage, resultImage, resultCanvas, zoomOutput,
            });
            preview.rebuild(width, height, [255, 255, 255, 255], shapes);
            const pixels = resultCanvas.getContext('2d').getImageData(0, 0, width, height).data;
            return Array.from({ length: width * height }, (_, index) => pixels[index * 4]);
          } finally {
            sourceStage.remove();
            resultStage.remove();
          }
        }""",
        {"shapes": shapes, "width": size[0], "height": size[1]},
    )


def _mask(red: bytes | list[int], width: int) -> set[tuple[int, int]]:
    return {
        (index % width, index // width)
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
