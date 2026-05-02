import unittest
from pathlib import Path

from geometrize_py.jobs import ExportFormat, GeometrizeJob, ShapeType


class GeometrizeJobTests(unittest.TestCase):
    def test_creates_triangle_job_with_expected_defaults(self):
        job = GeometrizeJob(
            input_path=Path("input.png"),
            output_path=Path("output.png"),
            shape=ShapeType.TRIANGLE,
            count=4000,
        )

        self.assertEqual(job.shape, ShapeType.TRIANGLE)
        self.assertEqual(job.count, 4000)
        self.assertEqual(job.export_format, ExportFormat.PNG)
        self.assertEqual(job.alpha, 128)
        self.assertEqual(job.candidate_shape_count, 50)
        self.assertEqual(job.max_shape_mutations, 100)

    def test_rejects_non_positive_shape_count(self):
        with self.assertRaisesRegex(ValueError, "count"):
            GeometrizeJob(
                input_path=Path("input.png"),
                output_path=Path("output.png"),
                shape=ShapeType.TRIANGLE,
                count=0,
            )

    def test_shape_lookup_uses_cli_names(self):
        self.assertEqual(ShapeType.from_cli("triangle"), ShapeType.TRIANGLE)
        self.assertEqual(ShapeType.from_cli("rotated-rectangle"), ShapeType.ROTATED_RECTANGLE)
        self.assertEqual(ShapeType.from_cli("rotated_rectangle"), ShapeType.ROTATED_RECTANGLE)


if __name__ == "__main__":
    unittest.main()
