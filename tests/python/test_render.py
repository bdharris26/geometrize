from __future__ import annotations

import pytest

from geometrize_py.render import render_shapes_to_image


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
