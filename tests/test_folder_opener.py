"""folder_opener.py(保存先フォルダをOSのファイルマネージャで開く処理)の単体テスト"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import folder_opener


class OpenFolderTest(unittest.TestCase):
    def test_windows_without_existing_window_opens_new(self):
        with patch.object(folder_opener.sys, "platform", "win32"), \
             patch.object(folder_opener, "find_open_explorer_window", return_value=None), \
             patch.object(folder_opener.QDesktopServices, "openUrl") as open_url_mock:
            folder_opener.open_folder("C:/out")
        open_url_mock.assert_called_once()

    def test_windows_reuses_existing_window(self):
        window = MagicMock()
        with patch.object(folder_opener.sys, "platform", "win32"), \
             patch.object(folder_opener, "find_open_explorer_window", return_value=window), \
             patch.object(folder_opener, "_bring_to_front") as front_mock, \
             patch.object(folder_opener.QDesktopServices, "openUrl") as open_url_mock:
            folder_opener.open_folder("C:/out")
        front_mock.assert_called_once_with(window)
        open_url_mock.assert_not_called()

    def test_macos_opens_via_qt(self):
        with patch.object(folder_opener.sys, "platform", "darwin"), \
             patch.object(folder_opener.QDesktopServices, "openUrl") as open_url_mock:
            folder_opener.open_folder("/tmp/out")
        self.assertEqual(open_url_mock.call_args[0][0].toLocalFile(), "/tmp/out")


class RevealFileTest(unittest.TestCase):
    def test_windows_selects_file_in_explorer(self):
        with patch.object(folder_opener.sys, "platform", "win32"), \
             patch.object(folder_opener.subprocess, "Popen") as popen_mock:
            folder_opener.reveal_file("C:/out/My Video.mp4")
        command = popen_mock.call_args[0][0]
        self.assertEqual(command, f'explorer /select,"{os.path.normpath("C:/out/My Video.mp4")}"')

    def test_macos_reveals_in_finder(self):
        with patch.object(folder_opener.sys, "platform", "darwin"), \
             patch.object(folder_opener.subprocess, "Popen") as popen_mock:
            folder_opener.reveal_file("/tmp/out/My Video.mp4")
        # reveal_fileはパスをOSの表記へ正規化する(Windowsでは区切りが\\になる)
        popen_mock.assert_called_once_with(["open", "-R", os.path.normpath("/tmp/out/My Video.mp4")])

    def test_other_os_opens_containing_folder(self):
        with patch.object(folder_opener.sys, "platform", "linux"), \
             patch.object(folder_opener, "open_folder") as open_folder_mock:
            folder_opener.reveal_file("/tmp/out/My Video.mp4")
        open_folder_mock.assert_called_once_with(os.path.normpath("/tmp/out"))

    def test_falls_back_to_folder_when_launch_fails(self):
        with patch.object(folder_opener.sys, "platform", "darwin"), \
             patch.object(folder_opener.subprocess, "Popen", side_effect=OSError("not found")), \
             patch.object(folder_opener, "open_folder") as open_folder_mock, \
             patch.object(folder_opener, "log_debug"):
            folder_opener.reveal_file("/tmp/out/My Video.mp4")
        open_folder_mock.assert_called_once_with(os.path.normpath("/tmp/out"))


if __name__ == "__main__":
    unittest.main()
