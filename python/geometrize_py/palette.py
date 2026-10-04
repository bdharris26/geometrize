"""Immutable fitting palettes and bounded, deterministic visible-color extraction."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Any

from PIL import Image

from .contracts import (
    MAX_SOURCE_DIMENSION,
    MAX_SOURCE_PIXELS,
    PALETTE_DEFAULTS,
    PALETTE_MAX_COLORS,
    PALETTE_SAMPLE_SIZE,
)

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Palette:
    """Strength 1 constrains primitive RGB; lower strengths pull fitted colors."""

    colors: tuple[RGB, ...]
    strength: float = PALETTE_DEFAULTS["strength"]

    def __post_init__(self) -> None:
        if not isinstance(self.colors, (list, tuple)) or not 1 <= len(self.colors) <= PALETTE_MAX_COLORS:
            raise ValueError(f"palette.colors must contain 1 to {PALETTE_MAX_COLORS} RGB triples")
        normalized = []
        for color in self.colors:
            if not isinstance(color, (list, tuple)) or len(color) != 3:
                raise ValueError("Each palette color must be an RGB triple")
            if any(isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= 255
                   for channel in color):
                raise ValueError("Palette channels must be integers from 0 to 255")
            rgb = tuple(color)
            if rgb not in normalized:
                normalized.append(rgb)
        try:
            valid = (not isinstance(self.strength, bool) and isinstance(self.strength, Real)
                     and isfinite(self.strength) and 0 <= self.strength <= 1)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("palette.strength must be a finite number from 0 to 1")
        object.__setattr__(self, "colors", tuple(normalized))
        object.__setattr__(self, "strength", float(self.strength))

    def to_dict(self) -> dict[str, Any]:
        return {"colors": [list(color) for color in self.colors], "strength": self.strength}


def normalize_palette(value: Any) -> Palette | None:
    if value is None or isinstance(value, Palette):
        return value
    if not isinstance(value, dict):
        raise ValueError("palette must be an object or null")
    if set(value) - {"colors", "strength"}:
        raise ValueError("palette only accepts colors and strength")
    if "colors" not in value:
        raise ValueError("palette requires colors")
    return Palette(value["colors"], value.get("strength", PALETTE_DEFAULTS["strength"]))


def normalize_max_colors(value: Any = PALETTE_DEFAULTS["max_colors"]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= PALETTE_MAX_COLORS:
        raise ValueError(f"max_colors must be an integer from 1 to {PALETTE_MAX_COLORS}")
    return value


def palette_sample_dimensions(width: int, height: int) -> tuple[int, int]:
    longest = max(width, height)
    if longest <= PALETTE_SAMPLE_SIZE:
        return width, height
    return (max(1, width * PALETTE_SAMPLE_SIZE // longest),
            max(1, height * PALETTE_SAMPLE_SIZE // longest))


def palette_memory_bytes(width: int, height: int) -> int:
    sample_width, sample_height = palette_sample_dimensions(width, height)
    # Source decoding/orientation/conversion plus histogram, sorted boxes and
    # median-cut references. At most 65,536 sampled colors enter Python lists.
    return width * height * 12 + sample_width * sample_height * 384 + 1024 * 1024


def palette_fitting_memory_bytes(width: int, height: int, workers: int) -> int:
    # Four-point polylines and sampled quadratic curves have at most eight
    # image perimeters of rasterized spans, including focus translation. Each
    # worker sorts a 12-byte scanline copy; margins cover endpoints/tiny axes.
    # An overlapping winning mask may also need a replacement runner's two
    # bitmaps. Neither allocation is repeated for each palette color.
    return workers * 96 * (width + height + 32) + width * height * 8


def extract_palette(image: Image.Image, max_colors: int = PALETTE_DEFAULTS["max_colors"]) -> dict[str, Any]:
    """Median-cut RGB samples weighted by alpha; transparent pixels contribute 0.

    Nearest-neighbor sampling keeps source colors intact, without an implicit
    matte. Tie-breaking and integer means are stable across repeated requests.
    """
    max_colors = normalize_max_colors(max_colors)
    if (image.width <= 0 or image.height <= 0 or max(image.size) > MAX_SOURCE_DIMENSION
            or image.width * image.height > MAX_SOURCE_PIXELS):
        raise ValueError("Source image exceeds palette extraction dimensions or pixel limit")
    dimensions = palette_sample_dimensions(image.width, image.height)
    sample = image.resize(dimensions, Image.Resampling.NEAREST).convert("RGBA")
    histogram: dict[RGB, int] = {}
    pixels = sample.tobytes()
    for index in range(0, len(pixels), 4):
        red, green, blue, alpha = pixels[index:index + 4]
        if alpha:
            color = (red, green, blue)
            histogram[color] = histogram.get(color, 0) + alpha
    if not histogram:
        # A nearest-neighbor grid can miss a sparse visible pixel or stripe.
        # Pillow scans alpha under the source pixel cap, then only one bounded
        # row enters Python. This also distinguishes truly transparent input.
        rgba = image.convert("RGBA")
        alpha_image = rgba.getchannel("A")
        bounds = alpha_image.getbbox()
        if bounds is None:
            raise ValueError("Source has no visible colors; fully transparent images cannot supply a palette")
        top = bounds[1]
        row = alpha_image.crop((0, top, rgba.width, top + 1))
        for x, alpha in enumerate(row.tobytes()):
            if alpha:
                color = rgba.getpixel((x, top))[:3]
                histogram[color] = alpha
                break

    boxes = [list(histogram)]
    while len(boxes) < max_colors:
        choices = []
        for index, box in enumerate(boxes):
            if len(box) > 1:
                spans = [max(color[channel] for color in box) - min(color[channel] for color in box)
                         for channel in range(3)]
                channel = max(range(3), key=lambda axis: (spans[axis], -axis))
                weight = sum(histogram[color] for color in box)
                choices.append((spans[channel] * weight, -index, channel))
        if not choices:
            break
        _priority, negative_index, channel = max(choices)
        index = -negative_index
        box = sorted(boxes[index], key=lambda color: (color[channel], color))
        weight = sum(histogram[color] for color in box)
        cumulative = 0
        split = 1
        for offset, color in enumerate(box[:-1], start=1):
            split = offset
            cumulative += histogram[color]
            if cumulative * 2 >= weight:
                break
        boxes[index:index + 1] = [box[:split], box[split:]]

    weighted_colors = []
    for index, box in enumerate(boxes):
        weight = sum(histogram[color] for color in box)
        mean = tuple((sum(color[channel] * histogram[color] for color in box) + weight // 2) // weight
                     for channel in range(3))
        weighted_colors.append((-weight, index, mean))
    colors = []
    for _weight, _index, color in sorted(weighted_colors):
        if list(color) not in colors:
            colors.append(list(color))
    return {"colors": colors, "requested_max_colors": max_colors,
            "sample_width": dimensions[0], "sample_height": dimensions[1]}
