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


if __name__ == "__main__":
    unittest.main()
