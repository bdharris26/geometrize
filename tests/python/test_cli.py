import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from geometrize_py.cli import main


class CliTests(unittest.TestCase):
    def run_cli(self, args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(args)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_run_dry_run_prints_job_plan(self):
        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "source.png"
            output_path = Path(root) / "out.png"
            input_path.write_bytes(b"not a real image yet")

            exit_code, stdout, stderr = self.run_cli(
                [
                    "run",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--shape",
                    "triangle",
                    "--count",
                    "4000",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0, stderr)
        plan = json.loads(stdout)
        self.assertEqual(plan["shape"], "triangle")
        self.assertEqual(plan["count"], 4000)
        self.assertEqual(plan["export_format"], "png")
        self.assertEqual(plan["runner"], "dry-run")

    def test_run_writes_png_output(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "source.png"
            output_path = Path(root) / "out.png"
            image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
            for x in range(2, 4):
                for y in range(4):
                    image.putpixel((x, y), (255, 255, 255, 255))
            image.save(input_path)

            exit_code, stdout, stderr = self.run_cli(
                [
                    "run",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--shape",
                    "rectangle",
                    "--count",
                    "1",
                    "--candidate-shape-count",
                    "10",
                    "--max-shape-mutations",
                    "25",
                    "--max-threads",
                    "1",
                ]
            )

            self.assertTrue(output_path.exists())

        self.assertEqual(exit_code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["output_path"], str(output_path))
        self.assertLessEqual(result["shapes_written"], 1)

    def test_run_writes_json_output(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "source.png"
            output_path = Path(root) / "out.json"
            image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
            for x in range(2, 4):
                for y in range(4):
                    image.putpixel((x, y), (255, 255, 255, 255))
            image.save(input_path)

            exit_code, stdout, stderr = self.run_cli(
                [
                    "run",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--shape",
                    "rectangle",
                    "--count",
                    "1",
                    "--export-format",
                    "json",
                    "--candidate-shape-count",
                    "10",
                    "--max-shape-mutations",
                    "25",
                    "--max-threads",
                    "1",
                ]
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["output_path"], str(output_path))
        self.assertEqual(payload["width"], 4)
        self.assertEqual(payload["height"], 4)
        self.assertLessEqual(len(payload["shapes"]), 1)

    def test_run_dry_run_accepts_job_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "source.png"
            output_path = Path(root) / "out.png"
            manifest_path = Path(root) / "job.json"
            input_path.write_bytes(b"not a real image yet")
            manifest_path.write_text(
                json.dumps(
                    {
                        "input_path": str(input_path),
                        "output_path": str(output_path),
                        "shape": "triangle",
                        "count": 4000,
                    }
                ),
                encoding="utf-8",
            )

            exit_code, stdout, stderr = self.run_cli(["run", "--job", str(manifest_path), "--dry-run"])

        self.assertEqual(exit_code, 0, stderr)
        plan = json.loads(stdout)
        self.assertEqual(plan["shape"], "triangle")
        self.assertEqual(plan["count"], 4000)

    def test_inspect_prints_image_metadata(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "source.png"
            Image.new("RGBA", (3, 4), (1, 2, 3, 4)).save(input_path)

            exit_code, stdout, stderr = self.run_cli(["inspect", str(input_path)])

        self.assertEqual(exit_code, 0, stderr)
        info = json.loads(stdout)
        self.assertEqual(info["width"], 3)
        self.assertEqual(info["height"], 4)
        self.assertEqual(info["mode"], "RGBA")


if __name__ == "__main__":
    unittest.main()
