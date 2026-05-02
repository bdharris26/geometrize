from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass(frozen=True, slots=True)
class ImageInfo:
    path: Path
    width: int
    height: int
    mode: str
    format: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "mode": self.mode,
            "format": self.format,
        }


@dataclass(frozen=True, slots=True)
class RgbaImage:
    width: int
    height: int
    rgba: bytes

    def __post_init__(self) -> None:
        if self.width <= 0:
            raise ValueError("width must be greater than zero")
        if self.height <= 0:
            raise ValueError("height must be greater than zero")
        expected_length = self.width * self.height * 4
        if len(self.rgba) != expected_length:
            raise ValueError(f"rgba data must contain {expected_length} bytes")


def inspect_image(path: Path) -> ImageInfo:
    path = Path(path)
    with Image.open(path) as image:
        return ImageInfo(
            path=path,
            width=image.width,
            height=image.height,
            mode=image.mode,
            format=image.format,
        )


def load_rgba_image(path: Path) -> RgbaImage:
    path = Path(path)
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        return RgbaImage(width=rgba.width, height=rgba.height, rgba=rgba.tobytes())


def save_rgba_image(image: RgbaImage, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pil_image = Image.frombytes("RGBA", (image.width, image.height), image.rgba)
    pil_image.save(path)
