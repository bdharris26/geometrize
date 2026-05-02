import unittest

from geometrize_py import native


class NativeBackendTests(unittest.TestCase):
    def test_native_backend_reports_available(self):
        self.assertTrue(native.is_native_core_available())


if __name__ == "__main__":
    unittest.main()
