import tempfile
import unittest
import json
from pathlib import Path

from PIL import Image

from geometrize_py.jobs import ExportFormat, GeometrizeJob, ShapeType
from geometrize_py.native import NativeRunner


class NativeRunnerTests(unittest.TestCase):
    def test_runner_writes_png_output(self):
        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "input.png"
            output_path = Path(root) / "output.png"
            image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
            for x in range(2, 4):
                for y in range(4):
                    image.putpixel((x, y), (255, 255, 255, 255))
            image.save(input_path)

            result = NativeRunner().run(
                GeometrizeJob(
                    input_path=input_path,
                    output_path=output_path,
                    shape=ShapeType.RECTANGLE,
                    count=1,
                    candidate_shape_count=10,
                    max_shape_mutations=25,
                    max_threads=1,
                )
            )

            self.assertEqual(result.output_path, output_path)
            self.assertTrue(output_path.exists())
            self.assertLessEqual(result.shapes_written, 1)

    def test_runner_writes_json_shape_output(self):
        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "input.png"
            output_path = Path(root) / "output.json"
            image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
            for x in range(2, 4):
                for y in range(4):
                    image.putpixel((x, y), (255, 255, 255, 255))
            image.save(input_path)

            result = NativeRunner().run(
                GeometrizeJob(
                    input_path=input_path,
                    output_path=output_path,
                    shape=ShapeType.RECTANGLE,
                    count=1,
                    export_format=ExportFormat.JSON,
                    candidate_shape_count=10,
                    max_shape_mutations=25,
                    max_threads=1,
                )
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result.output_path, output_path)
        self.assertEqual(payload["width"], 4)
        self.assertEqual(payload["height"], 4)
        self.assertLessEqual(len(payload["shapes"]), 1)

    def test_runner_writes_svg_output(self):
        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "input.png"
            output_path = Path(root) / "output.svg"
            image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
            for x in range(2, 4):
                for y in range(4):
                    image.putpixel((x, y), (255, 255, 255, 255))
            image.save(input_path)

            result = NativeRunner().run(
                GeometrizeJob(
                    input_path=input_path,
                    output_path=output_path,
                    shape=ShapeType.RECTANGLE,
                    count=1,
                    export_format=ExportFormat.SVG,
                    candidate_shape_count=10,
                    max_shape_mutations=25,
                    max_threads=1,
                )
            )

            svg = output_path.read_text(encoding="utf-8")

        self.assertEqual(result.output_path, output_path)
        self.assertIn("<svg", svg)
        self.assertIn("width=\"4\"", svg)
        self.assertIn("height=\"4\"", svg)


if __name__ == "__main__":
    unittest.main()
