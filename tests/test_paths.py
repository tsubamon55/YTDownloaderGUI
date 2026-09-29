"""paths.py の単体テスト(OS固有APIに依存する箇所はsys.platformをパッチして各OSの挙動を検証)"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import paths


class GetBaseDirTest(unittest.TestCase):
    def test_frozen_returns_executable_dir(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", os.path.join("Apps", "YTDownloaderGUI", "app.exe")):
            self.assertEqual(paths.get_base_dir(), os.path.join("Apps", "YTDownloaderGUI"))

    def test_dev_mode_returns_project_root(self):
        with patch.object(sys, "frozen", False, create=True):
            base_dir = paths.get_base_dir()
        expected = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))
        self.assertEqual(base_dir, expected)


class GetAppDataDirTest(unittest.TestCase):
    def test_uses_localappdata_on_windows(self):
        with patch.object(sys, "platform", "win32"), \
             patch.dict(os.environ, {"LOCALAPPDATA": os.path.join("C:", "Users", "test", "AppData", "Local")}):
            path = paths.get_app_data_dir()
        self.assertEqual(path, os.path.join("C:", "Users", "test", "AppData", "Local", "YTDownloaderGUI"))

    def test_falls_back_to_home_when_localappdata_missing_on_windows(self):
        env = dict(os.environ)
        env.pop("LOCALAPPDATA", None)
        with patch.object(sys, "platform", "win32"), patch.dict(os.environ, env, clear=True):
            path = paths.get_app_data_dir()
        expected = os.path.join(os.path.expanduser("~"), "YTDownloaderGUI")
        self.assertEqual(path, expected)

    def test_uses_application_support_on_macos(self):
        with patch.object(sys, "platform", "darwin"):
            path = paths.get_app_data_dir()
        expected = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "YTDownloaderGUI")
        self.assertEqual(path, expected)

    def test_uses_xdg_data_home_on_linux(self):
        with patch.object(sys, "platform", "linux"), \
             patch.dict(os.environ, {"XDG_DATA_HOME": os.path.join("home", "test", ".data")}):
            path = paths.get_app_data_dir()
        self.assertEqual(path, os.path.join("home", "test", ".data", "YTDownloaderGUI"))


class GetLogFilePathTest(unittest.TestCase):
    def test_appends_crash_log_to_app_data_dir(self):
        with patch.object(paths, "get_app_data_dir", return_value=os.path.join("base", "YTDownloaderGUI")):
            path = paths.get_log_file_path()
        self.assertEqual(path, os.path.join("base", "YTDownloaderGUI", "crash.log"))


class GetDownloadsFolderTest(unittest.TestCase):
    def test_returns_home_downloads_on_macos(self):
        with patch.object(sys, "platform", "darwin"):
            result = paths.get_downloads_folder()
        self.assertEqual(result, os.path.join(os.path.expanduser("~"), "Downloads"))

    def test_returns_home_downloads_on_linux(self):
        with patch.object(sys, "platform", "linux"):
            result = paths.get_downloads_folder()
        self.assertEqual(result, os.path.join(os.path.expanduser("~"), "Downloads"))

    def test_falls_back_to_home_downloads_when_windows_api_fails(self):
        with patch.object(sys, "platform", "win32"), \
             patch("ctypes.windll", create=True) as windll_mock:
            windll_mock.shell32.SHGetKnownFolderPath.side_effect = OSError("boom")
            result = paths.get_downloads_folder()
        self.assertEqual(result, os.path.join(os.path.expanduser("~"), "Downloads"))


class GetFfmpegLocationTest(unittest.TestCase):
    def test_finds_ffmpeg_bundled_next_to_base_dir(self):
        with patch.object(paths, "get_base_dir", return_value=os.path.join("C:", "App")), \
             patch.object(paths, "FFMPEG_EXECUTABLE_NAME", "ffmpeg.exe"), \
             patch("sys._MEIPASS", os.path.join("C:", "Meipass"), create=True), \
             patch("os.path.isfile", side_effect=lambda p: p == os.path.join("C:", "App", "ffmpeg", "ffmpeg.exe")):
            result = paths.get_ffmpeg_location()
        self.assertEqual(result, os.path.join("C:", "App", "ffmpeg"))

    def test_finds_ffmpeg_bundled_without_exe_suffix_on_macos(self):
        with patch.object(paths, "get_base_dir", return_value=os.path.join("/", "App")), \
             patch.object(paths, "FFMPEG_EXECUTABLE_NAME", "ffmpeg"), \
             patch("sys._MEIPASS", os.path.join("/", "Meipass"), create=True), \
             patch("os.path.isfile", side_effect=lambda p: p == os.path.join("/", "App", "ffmpeg", "ffmpeg")):
            result = paths.get_ffmpeg_location()
        self.assertEqual(result, os.path.join("/", "App", "ffmpeg"))

    def test_falls_back_to_meipass_when_base_dir_has_no_ffmpeg(self):
        with patch.object(paths, "get_base_dir", return_value=os.path.join("C:", "App")), \
             patch.object(paths, "FFMPEG_EXECUTABLE_NAME", "ffmpeg.exe"), \
             patch("sys._MEIPASS", os.path.join("C:", "Meipass"), create=True), \
             patch("os.path.isfile", side_effect=lambda p: p == os.path.join("C:", "Meipass", "ffmpeg", "ffmpeg.exe")):
            result = paths.get_ffmpeg_location()
        self.assertEqual(result, os.path.join("C:", "Meipass", "ffmpeg"))

    def test_falls_back_to_system_path_ffmpeg(self):
        with patch.object(paths, "get_base_dir", return_value=os.path.join("C:", "App")), \
             patch("os.path.isfile", return_value=False), \
             patch("shutil.which", return_value=os.path.join("C:", "Windows", "ffmpeg.exe")):
            result = paths.get_ffmpeg_location()
        self.assertEqual(result, os.path.join("C:", "Windows"))

    def test_returns_none_when_nothing_found(self):
        with patch.object(paths, "get_base_dir", return_value=os.path.join("C:", "App")), \
             patch("os.path.isfile", return_value=False), \
             patch("shutil.which", return_value=None):
            result = paths.get_ffmpeg_location()
        self.assertIsNone(result)


class AppendLogEntryTest(unittest.TestCase):
    def test_appends_timestamped_line_and_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "nested", "crash.log")
            self.assertTrue(paths.append_log_entry("hello", log_path))
            with open(log_path, encoding="utf-8") as f:
                content = f.read()
            self.assertRegex(content, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] hello\n$")

    def test_returns_false_when_directory_cannot_be_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocking_file = os.path.join(tmp, "blocker")
            open(blocking_file, "w").close()
            self.assertFalse(paths.append_log_entry("x", os.path.join(blocking_file, "crash.log")))


class RemoveFileQuietlyTest(unittest.TestCase):
    def test_removes_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.tmp")
            open(path, "w").close()
            self.assertTrue(paths.remove_file_quietly(path, "test"))
            self.assertFalse(os.path.exists(path))

    def test_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(paths.remove_file_quietly(os.path.join(tmp, "none.tmp"), "test"))

    def test_failure_is_logged_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.tmp")
            open(path, "w").close()
            with patch("paths.os.remove", side_effect=PermissionError("locked")), \
                 patch("paths.log_debug") as log_mock:
                self.assertFalse(paths.remove_file_quietly(path, "ctx"))
            self.assertIn("ctx", log_mock.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
