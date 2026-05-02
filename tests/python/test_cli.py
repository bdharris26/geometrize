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

    def test_run_without_native_core_fails_clearly(self):
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
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("native core", stderr.lower())


if __name__ == "__main__":
    unittest.main()
