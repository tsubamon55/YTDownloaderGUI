"""main_window_ui.py の Ui_MainWindow に対する単体テスト。

MainWindowの振る舞い(イベントハンドラ)には一切依存せず、setup_uiが生成する
ウィジェットの初期状態のみを検証する。ハンドラへのシグナル接続は
main_window.MainWindow._connect_signalsの責務であり、そちらは
tests/test_main_window.py の SignalWiringTest で検証する。
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from formats import FORMAT_OPTIONS
from main_window_ui import APP_TITLE, IDLE_STATUS_TEXT, THUMBNAIL_SIZE, Ui_MainWindow, toggle_button_text, window_title

_app = QApplication.instance() or QApplication(sys.argv)


class SetupUiTest(unittest.TestCase):
    def setUp(self):
        self.window = Ui_MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.window.setup_ui("C:/Downloads")
        # isVisible()は祖先を含めた実際の表示状態を返すため、offscreenプラットフォームでも
        # トップレベルウィンドウ自体をshowしておく必要がある
        self.window.show()

    def test_out_edit_uses_saved_dir(self):
        self.assertEqual(self.window.out_edit.text(), "C:/Downloads")

    def test_format_combo_populated_with_options(self):
        self.assertEqual(self.window.format_combo.count(), len(FORMAT_OPTIONS))
        self.assertEqual(self.window.format_combo.itemText(0), FORMAT_OPTIONS[0].label)

    def test_format_combo_items_have_tooltips(self):
        for i, option in enumerate(FORMAT_OPTIONS):
            self.assertEqual(
                self.window.format_combo.itemData(i, Qt.ItemDataRole.ToolTipRole), option.tooltip
            )

    def test_manual_container_starts_hidden(self):
        self.assertFalse(self.window.manual_container.isVisible())

    def test_detail_container_starts_hidden(self):
        self.assertFalse(self.window.detail_container.isVisible())

    def test_detail_container_starts_disabled(self):
        # スライダー・開始/終了欄はdetail_container単位でまとめて無効化されている
        self.assertFalse(self.window.detail_container.isEnabled())
        self.assertFalse(self.window.clip_range_slider.isEnabled())
        self.assertFalse(self.window.clip_start_edit.isEnabled())
        self.assertFalse(self.window.clip_end_edit.isEnabled())

    def test_video_and_audio_combos_start_disabled(self):
        self.assertFalse(self.window.video_format_combo.isEnabled())
        self.assertFalse(self.window.audio_format_combo.isEnabled())

    def test_download_related_buttons_start_disabled(self):
        self.assertFalse(self.window.download_btn.isEnabled())
        self.assertFalse(self.window.cancel_btn.isEnabled())
        self.assertFalse(self.window.open_folder_btn.isEnabled())

    def test_window_title_without_version_is_app_name(self):
        self.assertEqual(self.window.windowTitle(), APP_TITLE)

    def test_window_title_shows_version(self):
        window = Ui_MainWindow()
        self.addCleanup(window.deleteLater)
        window.setup_ui("C:/Downloads", "1.2.1")
        self.assertEqual(window.windowTitle(), f"{APP_TITLE} v1.2.1")

    def test_window_title_helper_ignores_empty_version(self):
        self.assertEqual(window_title(None), APP_TITLE)
        self.assertEqual(window_title(""), APP_TITLE)

    def test_status_label_starts_idle(self):
        self.assertEqual(self.window.status_label.text(), IDLE_STATUS_TEXT)

    def test_log_view_starts_hidden(self):
        self.assertFalse(self.window.log_view.isVisible())

    def test_input_widgets_lists_expected_widgets(self):
        expected = {
            self.window.url_edit,
            self.window.paste_btn,
            self.window.out_edit,
            self.window.browse_btn,
            self.window.format_combo,
            self.window.manual_toggle_btn,
            self.window.video_format_combo,
            self.window.audio_format_combo,
            self.window.mp3_checkbox,
            self.window.mp3_label,
            self.window.detail_toggle_btn,
            self.window.detail_container,
        }
        self.assertEqual(set(self.window.input_widgets), expected)


class ToggleButtonTextTest(unittest.TestCase):
    def test_expanded_and_collapsed(self):
        self.assertEqual(toggle_button_text("ログ", True), "ログ ▴")
        self.assertEqual(toggle_button_text("詳細設定", False), "詳細設定 ▾")

    def test_thumbnail_size_is_unchanged(self):
        self.assertEqual((THUMBNAIL_SIZE.width(), THUMBNAIL_SIZE.height()), (120, 68))


if __name__ == "__main__":
    unittest.main()
