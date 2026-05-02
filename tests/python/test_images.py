import tempfile
import unittest
from pathlib import Path

from PIL import Image

from geometrize_py.images import RgbaImage, inspect_image, load_rgba_image, save_rgba_image


class ImageIoTests(unittest.TestCase):
    def test_load_rgba_image_returns_dimensions_and_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            image_path = Path(root) / "source.png"
            Image.new("RGB", (2, 1), (10, 20, 30)).save(image_path)

            image = load_rgba_image(image_path)

        self.assertEqual(image.width, 2)
        self.assertEqual(image.height, 1)
        self.assertEqual(image.rgba, bytes([10, 20, 30, 255, 10, 20, 30, 255]))

    def test_inspect_image_does_not_materialize_rgba_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            image_path = Path(root) / "source.png"
            Image.new("RGBA", (3, 4), (1, 2, 3, 4)).save(image_path)

            info = inspect_image(image_path)

        self.assertEqual(info.width, 3)
        self.assertEqual(info.height, 4)
        self.assertEqual(info.mode, "RGBA")
        self.assertEqual(info.format, "PNG")

    def test_save_rgba_image_writes_png(self):
        with tempfile.TemporaryDirectory() as root:
            output_path = Path(root) / "out.png"
            image = RgbaImage(width=1, height=1, rgba=bytes([5, 6, 7, 255]))

            save_rgba_image(image, output_path)

            with Image.open(output_path) as saved:
                self.assertEqual(saved.size, (1, 1))
                self.assertEqual(saved.convert("RGBA").getpixel((0, 0)), (5, 6, 7, 255))


if __name__ == "__main__":
    unittest.main()
