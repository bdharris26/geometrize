import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from geometrize_py.cli import main
from helpers import write_tiny_split_image


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
        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "source.png"
            output_path = Path(root) / "out.png"
            write_tiny_split_image(input_path)

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
        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "source.png"
            output_path = Path(root) / "out.json"
            write_tiny_split_image(input_path)

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

    def test_run_writes_svg_output(self):
        with tempfile.TemporaryDirectory() as root:
            input_path = Path(root) / "source.png"
            output_path = Path(root) / "out.svg"
            write_tiny_split_image(input_path)

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
                    "svg",
                    "--candidate-shape-count",
                    "10",
                    "--max-shape-mutations",
                    "25",
                    "--max-threads",
                    "1",
                ]
            )

            svg = output_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["output_path"], str(output_path))
        self.assertIn("<svg", svg)

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

    def test_run_job_manifest_rejects_cli_overrides(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = Path(root) / "job.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "input_path": "source.png",
                        "output_path": "out.png",
                        "shape": "triangle",
                        "count": 4000,
                    }
                ),
                encoding="utf-8",
            )

            exit_code, stdout, stderr = self.run_cli(
                [
                    "run",
                    "--job",
                    str(manifest_path),
                    "--shape",
                    "rectangle",
                    "--output",
                    str(Path(root) / "ignored.png"),
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--job cannot be combined", stderr)
        self.assertIn("--shape", stderr)
        self.assertIn("--output", stderr)

    def test_batch_dry_run_prints_resolved_plans(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            manifest_path = root_path / "batch.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaults": {"shape": "triangle", "count": 5, "export_format": "svg"},
                        "jobs": [{"input_path": "input.png", "output_path": "out.svg"}],
                    }
                ),
                encoding="utf-8",
            )

            exit_code, stdout, stderr = self.run_cli(["batch", "--manifest", str(manifest_path), "--dry-run"])

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["jobs_total"], 1)
        self.assertEqual(payload["plans"][0]["input_path"], str(root_path / "input.png"))
        self.assertEqual(payload["plans"][0]["shape"], "triangle")
        self.assertEqual(payload["plans"][0]["export_format"], "svg")

    def test_batch_writes_png_and_svg_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            input_a = root_path / "a.png"
            input_b = root_path / "b.png"
            output_a = root_path / "a_out.png"
            output_b = root_path / "b_out.svg"
            write_tiny_split_image(input_a)
            write_tiny_split_image(input_b)
            manifest_path = root_path / "batch.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaults": {
                            "shape": "rectangle",
                            "count": 1,
                            "candidate_shape_count": 10,
                            "max_shape_mutations": 25,
                            "max_threads": 1,
                        },
                        "jobs": [
                            {"input_path": "a.png", "output_path": "a_out.png"},
                            {"input_path": "b.png", "output_path": "b_out.svg", "export_format": "svg"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            exit_code, stdout, stderr = self.run_cli(["batch", "--manifest", str(manifest_path)])

            self.assertTrue(output_a.exists())
            self.assertTrue(output_b.exists())

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["jobs_succeeded"], 2)
        self.assertEqual(payload["jobs_failed"], 0)

    def test_batch_continue_on_error_returns_1(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            input_ok = root_path / "ok.png"
            output_ok = root_path / "ok_out.png"
            write_tiny_split_image(input_ok)
            manifest_path = root_path / "batch.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaults": {
                            "shape": "rectangle",
                            "count": 1,
                            "candidate_shape_count": 10,
                            "max_shape_mutations": 25,
                            "max_threads": 1,
                        },
                        "jobs": [
                            {"input_path": "missing.png", "output_path": "missing_out.png"},
                            {"input_path": "ok.png", "output_path": "ok_out.png"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            exit_code, stdout, stderr = self.run_cli(
                ["batch", "--manifest", str(manifest_path), "--continue-on-error"]
            )

            self.assertTrue(output_ok.exists())

        self.assertEqual(exit_code, 1, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["jobs_succeeded"], 1)
        self.assertEqual(payload["jobs_failed"], 1)

    def test_batch_stops_on_first_error_without_continue(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            input_ok = root_path / "ok.png"
            output_ok = root_path / "ok_out.png"
            write_tiny_split_image(input_ok)
            manifest_path = root_path / "batch.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaults": {
                            "shape": "rectangle",
                            "count": 1,
                            "candidate_shape_count": 10,
                            "max_shape_mutations": 25,
                            "max_threads": 1,
                        },
                        "jobs": [
                            {"input_path": "missing.png", "output_path": "missing_out.png"},
                            {"input_path": "ok.png", "output_path": "ok_out.png"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            exit_code, stdout, stderr = self.run_cli(["batch", "--manifest", str(manifest_path)])

            self.assertFalse(output_ok.exists())

        self.assertEqual(exit_code, 2, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["jobs_succeeded"], 0)
        self.assertEqual(payload["jobs_failed"], 1)

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
