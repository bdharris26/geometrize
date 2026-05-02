import tempfile
import unittest
import json
from pathlib import Path

from geometrize_py.jobs import ExportFormat, GeometrizeJob, ShapeType
from geometrize_py.native import NativeRunner
from helpers import write_tiny_split_image


class NativeRunnerTests(unittest.TestCase):
    def test_runner_writes_png_output(self):
        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "input.png"
            output_path = Path(root) / "output.png"
            write_tiny_split_image(input_path)

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
            write_tiny_split_image(input_path)

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
            write_tiny_split_image(input_path)

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
