import unittest

from geometrize_py import native
from geometrize_py.images import RgbaImage


class NativeBackendTests(unittest.TestCase):
    def test_native_backend_reports_available(self):
        self.assertTrue(native.is_native_core_available())

    def test_native_backend_runs_tiny_rgba_job(self):
        from geometrize_py import _native

        image = RgbaImage(
            width=4,
            height=4,
            rgba=bytes(
                [
                    0,
                    0,
                    0,
                    255,
                    0,
                    0,
                    0,
                    255,
                    255,
                    255,
                    255,
                    255,
                    255,
                    255,
                    255,
                    255,
                ]
                * 4
            ),
        )

        result = _native.run_rgba(
            image.width,
            image.height,
            image.rgba,
            {
                "shape": "rectangle",
                "count": 1,
                "alpha": 128,
                "candidate_shape_count": 10,
                "max_shape_mutations": 25,
                "seed": 1,
                "max_threads": 1,
            },
        )

        self.assertEqual(result["width"], image.width)
        self.assertEqual(result["height"], image.height)
        self.assertEqual(len(result["rgba"]), len(image.rgba))
        self.assertLessEqual(len(result["shapes"]), 1)
        self.assertIsInstance(result["shapes"], list)


if __name__ == "__main__":
    unittest.main()
