from pathlib import Path

from PIL import Image


def write_tiny_split_image(path: Path) -> None:
    image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
    for x in range(2, 4):
        for y in range(4):
            image.putpixel((x, y), (255, 255, 255, 255))
    image.save(path)
