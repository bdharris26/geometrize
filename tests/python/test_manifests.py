import json
import tempfile
import unittest
from pathlib import Path

from geometrize_py.jobs import ExportFormat, GeometrizeJob, ShapeType
from geometrize_py.manifests import load_job, save_job


class ManifestTests(unittest.TestCase):
    def test_save_and_load_job_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = Path(root) / "job.json"
            job = GeometrizeJob(
                input_path=Path(root) / "input.png",
                output_path=Path(root) / "output.svg",
                shape=ShapeType.ROTATED_RECTANGLE,
                count=250,
                export_format=ExportFormat.SVG,
                alpha=180,
            )

            save_job(job, manifest_path)
            loaded = load_job(manifest_path)

        self.assertEqual(loaded.input_path, job.input_path)
        self.assertEqual(loaded.output_path, job.output_path)
        self.assertEqual(loaded.shape, ShapeType.ROTATED_RECTANGLE)
        self.assertEqual(loaded.count, 250)
        self.assertEqual(loaded.export_format, ExportFormat.SVG)
        self.assertEqual(loaded.alpha, 180)

    def test_load_job_accepts_cli_shape_names(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = Path(root) / "job.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "input_path": str(Path(root) / "in.png"),
                        "output_path": str(Path(root) / "out.png"),
                        "shape": "rotated-rectangle",
                        "count": 50,
                    }
                ),
                encoding="utf-8",
            )

            job = load_job(manifest_path)

        self.assertEqual(job.shape, ShapeType.ROTATED_RECTANGLE)
        self.assertEqual(job.export_format, ExportFormat.PNG)

    def test_load_job_rejects_missing_required_fields(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = Path(root) / "job.json"
            manifest_path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "input_path"):
                load_job(manifest_path)

    def test_load_job_accepts_utf8_bom_from_windows_tools(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = Path(root) / "job.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "input_path": str(Path(root) / "in.png"),
                        "output_path": str(Path(root) / "out.png"),
                        "shape": "triangle",
                        "count": 10,
                    }
                ),
                encoding="utf-8-sig",
            )

            job = load_job(manifest_path)

        self.assertEqual(job.shape, ShapeType.TRIANGLE)


if __name__ == "__main__":
    unittest.main()
