"""Application limits and metadata shared by Python and the browser.

Keep wire-format definitions here; GET /api/config serves the same values to
the UI so validators, controls, and saved projects use one contract.
"""

from __future__ import annotations

from typing import Any

SHAPE_TYPES = {
    "rectangle": 1,
    "rotated_rectangle": 2,
    "triangle": 4,
    "ellipse": 8,
    "rotated_ellipse": 16,
    "circle": 32,
    "line": 64,
    "quadratic_bezier": 128,
    "polyline": 256,
}
SHAPE_LABELS = {
    "rectangle": "Rectangle",
    "rotated_rectangle": "Rotated rectangle",
    "triangle": "Triangle",
    "ellipse": "Ellipse",
    "rotated_ellipse": "Rotated ellipse",
    "circle": "Circle",
    "line": "Line",
    "quadratic_bezier": "Bezier",
    "polyline": "Polyline",
}
SHAPE_DATA_FIELDS = {
    "rectangle": ("x1", "y1", "x2", "y2"),
    "rotated_rectangle": ("x1", "y1", "x2", "y2", "angle"),
    "triangle": ("x1", "y1", "x2", "y2", "x3", "y3"),
    "ellipse": ("x", "y", "rx", "ry"),
    "rotated_ellipse": ("x", "y", "rx", "ry", "angle"),
    "circle": ("x", "y", "r"),
    "line": ("x1", "y1", "x2", "y2"),
    "quadratic_bezier": ("x1", "y1", "cx", "cy", "x2", "y2"),
    "polyline": ("points",),
}
SHAPE_RADIUS_FIELDS = {"circle": ("r",), "ellipse": ("rx", "ry"), "rotated_ellipse": ("rx", "ry")}
STROKE_SHAPES = frozenset(("line", "quadratic_bezier", "polyline"))
DEFAULT_SHAPES = ("ellipse", "rotated_rectangle", "triangle")

OPTION_LIMITS = {
    "steps": (1, 4096),
    "alpha": (1, 255),
    "shape_count": (1, 512),
    "mutations": (1, 2048),
    "seed": (0, 2**31 - 1),
    "max_threads": (0, 128),
    "max_size": (32, 2048),
    "export_size": (32, 4096),
    "stagnation_limit": (0, 4096),
}
MAX_WORKING_IMAGE_SIZE = OPTION_LIMITS["max_size"][1]
MAX_IMAGE_SIZE = OPTION_LIMITS["export_size"][1]
OPTION_DEFAULTS = {
    "steps": 128,
    "shape_types": list(DEFAULT_SHAPES),
    "alpha": 128,
    "shape_count": 64,
    "mutations": 128,
    "seed": 9001,
    "max_threads": 0,
    "max_size": 1024,
    "export_size": 1024,
    "stagnation_limit": 128,
}

MAX_SOURCE_DIMENSION = 16384
MAX_SOURCE_PIXELS = 8192 * 8192
MAX_REQUEST_BYTES = 64 * 1024 * 1024
PROJECT_FORMAT = "geometrize-project"
PROJECT_VERSION = 1
PROJECT_MAX_BYTES = MAX_REQUEST_BYTES
PROJECT_MAX_SHAPES = 100000
PROJECT_MAX_BATCHES = 10000
RASTER_MIME_TYPES = ("image/png", "image/jpeg", "image/webp", "image/bmp", "image/gif")
RASTER_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")

PRESETS = {
    "quick": {"label": "Quick sketch", "options": {"steps": 64, "shape_count": 16, "mutations": 32, "max_size": 256}},
    "balanced": {"label": "Balanced", "options": {"steps": 128, "shape_count": 64, "mutations": 128, "max_size": 1024}},
    "fine": {"label": "Fine detail", "options": {"steps": 256, "shape_count": 128, "mutations": 256, "max_size": 2048}},
}


def app_contract() -> dict[str, Any]:
    return {
        "api_version": 1,
        "shapes": [
            {
                "type": name,
                "id": type_id,
                "label": SHAPE_LABELS[name],
                "data_fields": list(SHAPE_DATA_FIELDS[name]),
                "radius_fields": list(SHAPE_RADIUS_FIELDS.get(name, ())),
                "stroke": name in STROKE_SHAPES,
            }
            for name, type_id in SHAPE_TYPES.items()
        ],
        "defaults": OPTION_DEFAULTS.copy(),
        "limits": {name: {"min": lower, "max": upper} for name, (lower, upper) in OPTION_LIMITS.items()},
        "images": {
            "mime_types": list(RASTER_MIME_TYPES),
            "extensions": list(RASTER_EXTENSIONS),
            "max_dimension": MAX_SOURCE_DIMENSION,
            "max_pixels": MAX_SOURCE_PIXELS,
        },
        "project": {
            "format": PROJECT_FORMAT,
            "version": PROJECT_VERSION,
            "max_bytes": PROJECT_MAX_BYTES,
            "max_shapes": PROJECT_MAX_SHAPES,
            "max_batches": PROJECT_MAX_BATCHES,
        },
        "presets": PRESETS,
    }
