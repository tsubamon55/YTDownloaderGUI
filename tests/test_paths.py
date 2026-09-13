"""paths.py の単体テスト(Windows API に依存する get_downloads_folder はモックで検証)"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import paths


class GetBaseDirTest(unittest.TestCase):
    def test_frozen_returns_executable_dir(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", r"C:\Apps\YTDownloaderGUI\app.exe"):
            self.assertEqual(paths.get_base_dir(), r"C:\Apps\YTDownloaderGUI")

    def test_dev_mode_returns_project_root(self):
        with patch.object(sys, "frozen", False, create=True):
            base_dir = paths.get_base_dir()
        expected = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))
        self.assertEqual(base_dir, expected)


class GetLogFilePathTest(unittest.TestCase):
    def test_uses_localappdata_when_set(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}):
            path = paths.get_log_file_path()
        self.assertEqual(path, r"C:\Users\test\AppData\Local\YTDownloaderGUI\crash.log")

    def test_falls_back_to_home_when_localappdata_missing(self):
        env = dict(os.environ)
        env.pop("LOCALAPPDATA", None)
        with patch.dict(os.environ, env, clear=True):
            path = paths.get_log_file_path()
        expected = os.path.join(os.path.expanduser("~"), "YTDownloaderGUI", "crash.log")
        self.assertEqual(path, expected)


class GetFfmpegLocationTest(unittest.TestCase):
    def test_finds_ffmpeg_bundled_next_to_base_dir(self):
        with patch.object(paths, "get_base_dir", return_value=r"C:\App"), \
             patch("sys._MEIPASS", r"C:\Meipass", create=True), \
             patch("os.path.isfile", side_effect=lambda p: p == os.path.join(r"C:\App", "ffmpeg", "ffmpeg.exe")):
            result = paths.get_ffmpeg_location()
        self.assertEqual(result, os.path.join(r"C:\App", "ffmpeg"))

    def test_falls_back_to_meipass_when_base_dir_has_no_ffmpeg(self):
        with patch.object(paths, "get_base_dir", return_value=r"C:\App"), \
             patch("sys._MEIPASS", r"C:\Meipass", create=True), \
             patch("os.path.isfile", side_effect=lambda p: p == os.path.join(r"C:\Meipass", "ffmpeg", "ffmpeg.exe")):
            result = paths.get_ffmpeg_location()
        self.assertEqual(result, os.path.join(r"C:\Meipass", "ffmpeg"))

    def test_falls_back_to_system_path_ffmpeg(self):
        with patch.object(paths, "get_base_dir", return_value=r"C:\App"), \
             patch("os.path.isfile", return_value=False), \
             patch("shutil.which", return_value=r"C:\Windows\ffmpeg.exe"):
            result = paths.get_ffmpeg_location()
        self.assertEqual(result, r"C:\Windows")

    def test_returns_none_when_nothing_found(self):
        with patch.object(paths, "get_base_dir", return_value=r"C:\App"), \
             patch("os.path.isfile", return_value=False), \
             patch("shutil.which", return_value=None):
            result = paths.get_ffmpeg_location()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
