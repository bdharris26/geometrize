import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from geometrize_py.batch import BatchManifest, load_batch_manifest
from geometrize_py.jobs import ExportFormat, ShapeType


class BatchManifestTests(unittest.TestCase):
    def test_load_batch_manifest_merges_defaults_and_resolves_relative_paths(self):
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

            manifest = load_batch_manifest(manifest_path)

        self.assertIsInstance(manifest, BatchManifest)
        self.assertEqual(len(manifest.jobs), 1)
        self.assertEqual(manifest.jobs[0].input_path, root_path / "input.png")
        self.assertEqual(manifest.jobs[0].output_path, root_path / "out.svg")
        self.assertEqual(manifest.jobs[0].shape, ShapeType.TRIANGLE)
        self.assertEqual(manifest.jobs[0].count, 5)
        self.assertEqual(manifest.jobs[0].export_format, ExportFormat.SVG)

    def test_load_batch_manifest_rejects_empty_jobs(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = Path(root) / "batch.json"
            manifest_path.write_text(json.dumps({"version": 1, "jobs": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "jobs"):
                load_batch_manifest(manifest_path)

    def test_load_batch_manifest_rejects_non_object_job(self):
        with tempfile.TemporaryDirectory() as root:
            manifest_path = Path(root) / "batch.json"
            manifest_path.write_text(json.dumps({"version": 1, "jobs": ["bad"]}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "job 0"):
                load_batch_manifest(manifest_path)


def write_tiny_image(path: Path) -> None:
    image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
    for x in range(2, 4):
        for y in range(4):
            image.putpixel((x, y), (255, 255, 255, 255))
    image.save(path)


if __name__ == "__main__":
    unittest.main()
