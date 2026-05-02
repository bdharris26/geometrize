import tempfile
import unittest
from pathlib import Path

from geometrize_py.screenshots import find_latest_screenshot


class ScreenshotDiscoveryTests(unittest.TestCase):
    def test_finds_newest_screenshot_image_across_roots(self):
        with tempfile.TemporaryDirectory() as first_root, tempfile.TemporaryDirectory() as second_root:
            older = Path(first_root) / "Screenshot 2026-05-01 112113.png"
            newer = Path(second_root) / "Screenshot 2026-05-01 112445.png"
            ignored = Path(second_root) / "notes.png"

            older.write_bytes(b"older")
            newer.write_bytes(b"newer")
            ignored.write_bytes(b"ignored")

            older_mtime = 1_778_000_000
            newer_mtime = older_mtime + 60
            ignored_mtime = newer_mtime + 60
            older.touch()
            newer.touch()
            ignored.touch()
            import os

            os.utime(older, (older_mtime, older_mtime))
            os.utime(newer, (newer_mtime, newer_mtime))
            os.utime(ignored, (ignored_mtime, ignored_mtime))

            self.assertEqual(find_latest_screenshot([Path(first_root), Path(second_root)]), newer)

    def test_returns_none_when_no_screenshot_exists(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(find_latest_screenshot([Path(root)]))


if __name__ == "__main__":
    unittest.main()
