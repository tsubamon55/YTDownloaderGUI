"""main_window.py の MainWindow に対する単体テスト。

実際のQtウィジェットを使うため QApplication のインスタンスが必要だが、
表示・イベントループは不要なので QT_QPA_PLATFORM=offscreen で実行する
(このテストファイル自身が環境変数を設定する)。
ネットワークやダイアログ表示を伴う箇所はモックして検証する。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtWidgets import QApplication, QMessageBox

import main_window as main_window_module
from main_window import IDLE_STATUS_TEXT, MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def make_video(format_id="137", ext="mp4", vcodec="avc1.640028", height=1080, width=None,
                fps=30, filesize=None, protocol="https"):
    return {
        "format_id": format_id,
        "ext": ext,
        "vcodec": vcodec,
        "acodec": "none",
        "height": height,
        "width": width or int(height * 16 / 9),
        "fps": fps,
        "filesize": filesize,
        "protocol": protocol,
    }


def make_audio(format_id="140", ext="m4a", acodec="mp4a.40.2", abr=128, filesize=None,
                protocol="https"):
    return {
        "format_id": format_id,
        "ext": ext,
        "vcodec": "none",
        "acodec": acodec,
        "abr": abr,
        "filesize": filesize,
        "protocol": protocol,
    }


class MainWindowTestCase(unittest.TestCase):
    """auto_paste_from_clipboard がクリップボードの内容次第でURLを書き換えてしまうため、
    共通のセットアップでクリップボードを空にしてからウィンドウを生成する。"""

    def setUp(self):
        clipboard_patch = patch.object(QApplication, "clipboard")
        mock_clipboard_getter = clipboard_patch.start()
        mock_clipboard = MagicMock()
        mock_clipboard.text.return_value = ""
        mock_clipboard_getter.return_value = mock_clipboard
        self.addCleanup(clipboard_patch.stop)

        with patch.object(main_window_module, "get_downloads_folder", return_value="C:/Downloads"):
            self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        # isVisible()は祖先を含めた実際の表示状態を返すため、offscreenプラットフォームでも
        # トップレベルウィンドウ自体をshowしておく必要がある
        self.window.show()


class OnUrlChangedTest(MainWindowTestCase):
    def test_non_url_text_resets_status_and_disables_download(self):
        self.window.download_btn.setEnabled(True)
        self.window.on_url_changed("not a url")
        self.assertFalse(self.window.download_btn.isEnabled())
        self.assertEqual(self.window.status_label.text(), IDLE_STATUS_TEXT)
        self.assertFalse(self.window.info_ready)

    def test_url_text_starts_fetch_timer(self):
        self.window.on_url_changed("https://example.com/watch?v=abc")
        self.assertTrue(self.window._info_fetch_timer.isActive())


class OnDetailToggledTest(MainWindowTestCase):
    def test_checked_shows_detail_container_and_hides_simple(self):
        self.window.detail_toggle_btn.setChecked(True)
        self.assertTrue(self.window.detail_container.isVisible())
        self.assertFalse(self.window.simple_format_container.isVisible())
        self.assertEqual(self.window.detail_toggle_btn.text(), "簡易設定 ▴")

    def test_unchecked_shows_simple_and_hides_detail(self):
        self.window.detail_toggle_btn.setChecked(True)
        self.window.detail_toggle_btn.setChecked(False)
        self.assertFalse(self.window.detail_container.isVisible())
        self.assertTrue(self.window.simple_format_container.isVisible())
        self.assertEqual(self.window.detail_toggle_btn.text(), "詳細設定 ▾")

    def test_combos_disabled_when_no_formats_fetched_yet(self):
        self.window.detail_toggle_btn.setChecked(True)
        self.assertFalse(self.window.video_format_combo.isEnabled())
        self.assertFalse(self.window.audio_format_combo.isEnabled())


class OnFormatsFetchedTest(MainWindowTestCase):
    def test_populates_combos_and_counts(self):
        formats = [make_video(format_id="137"), make_audio(format_id="140")]
        worker = MagicMock()
        self.window.format_worker = worker

        self.window.on_formats_fetched(formats, "Sample Title", b"", worker, auto=False)

        # 各コンボには「なし」+実フォーマットの2件
        self.assertEqual(self.window.video_format_combo.count(), 2)
        self.assertEqual(self.window.audio_format_combo.count(), 2)
        self.assertEqual(self.window.title_label.text(), "Sample Title")
        self.assertEqual(self.window.status_label.text(), "動画1件・音声1件を検出しました")
        self.assertTrue(self.window.info_ready)
        self.assertTrue(self.window.download_btn.isEnabled())

    def test_stale_worker_result_is_ignored(self):
        stale_worker = MagicMock()
        current_worker = MagicMock()
        self.window.format_worker = current_worker

        self.window.on_formats_fetched([make_video()], "Should Not Apply", b"", stale_worker, auto=False)

        self.assertEqual(self.window.title_label.text(), "")
        self.assertFalse(self.window.info_ready)

    def test_mismatched_formats_sorted_after_matched_ones(self):
        matched = make_video(format_id="137", ext="mp4", vcodec="avc1.640028")
        mismatched = make_video(format_id="399", ext="mp4", vcodec="vp9")
        worker = MagicMock()
        self.window.format_worker = worker

        self.window.on_formats_fetched([mismatched, matched], "T", b"", worker, auto=False)

        # index 0 は「なし」、1件目の実データが先着(=互換フォーマット)であるべき
        self.assertEqual(self.window.video_format_combo.itemData(1)["format_id"], "137")
        self.assertEqual(self.window.video_format_combo.itemData(2)["format_id"], "399")


class OnFormatsErrorTest(MainWindowTestCase):
    def test_auto_fetch_error_sets_silent_status(self):
        self.window.on_formats_error("network error", worker=None, auto=True)
        self.assertEqual(self.window.status_label.text(), "動画情報を取得できませんでした")

    def test_manual_fetch_error_shows_dialog(self):
        with patch.object(QMessageBox, "critical") as critical_mock:
            self.window.on_formats_error("network error", worker=None, auto=False)
        critical_mock.assert_called_once()
        self.assertEqual(self.window.status_label.text(), "フォーマット取得に失敗しました")

    def test_stale_worker_error_is_ignored(self):
        current_worker = MagicMock()
        self.window.format_worker = current_worker
        self.window.status_label.setText("初期値")
        self.window.on_formats_error("network error", worker=MagicMock(), auto=True)
        self.assertEqual(self.window.status_label.text(), "初期値")


class OnDetailSelectionChangedTest(MainWindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.detail_toggle_btn.setChecked(True)
        self.window.video_format_combo.addItem("なし", userData=None)
        self.window.video_format_combo.addItem("video", userData=make_video())
        self.window.audio_format_combo.addItem("なし", userData=None)
        self.window.audio_format_combo.addItem("audio", userData=make_audio())

    def test_audio_only_enables_mp3_checkbox(self):
        self.window.video_format_combo.setCurrentIndex(0)  # なし
        self.window.audio_format_combo.setCurrentIndex(1)
        self.assertTrue(self.window.mp3_checkbox.isEnabled())
        self.assertEqual(self.window.merge_note_label.text(), "音声のみダウンロードします")

    def test_video_and_audio_selected_shows_merge_note_and_disables_mp3(self):
        self.window.video_format_combo.setCurrentIndex(1)
        self.window.audio_format_combo.setCurrentIndex(1)
        self.assertFalse(self.window.mp3_checkbox.isEnabled())
        self.assertEqual(self.window.merge_note_label.text(), "動画と音声を合成してダウンロードします")

    def test_video_only_selected(self):
        self.window.video_format_combo.setCurrentIndex(1)
        self.window.audio_format_combo.setCurrentIndex(0)
        self.assertFalse(self.window.mp3_checkbox.isEnabled())
        self.assertEqual(self.window.merge_note_label.text(), "動画のみダウンロードします(音声なし)")

    def test_nothing_selected_clears_note(self):
        self.window.video_format_combo.setCurrentIndex(0)
        self.window.audio_format_combo.setCurrentIndex(0)
        self.assertEqual(self.window.merge_note_label.text(), "")

    def test_simple_mode_disables_mp3_and_clears_note(self):
        self.window.detail_toggle_btn.setChecked(False)
        self.assertFalse(self.window.mp3_checkbox.isEnabled())
        self.assertEqual(self.window.merge_note_label.text(), "")


class ResolveFormatSpecTest(MainWindowTestCase):
    def test_simple_mode_uses_combo_label(self):
        self.window.detail_toggle_btn.setChecked(False)
        self.window.format_combo.setCurrentText("動画 (最高画質 mp4)")
        spec, postprocessors, _ = self.window.resolve_format_spec()
        self.assertIn("avc1", spec)

    def test_detail_mode_without_selection_raises(self):
        self.window.detail_toggle_btn.setChecked(True)
        with self.assertRaises(ValueError):
            self.window.resolve_format_spec()

    def test_detail_mode_with_video_and_audio_merges_ids(self):
        self.window.detail_toggle_btn.setChecked(True)
        self.window.video_format_combo.addItem("v", userData=make_video(format_id="137"))
        self.window.audio_format_combo.addItem("a", userData=make_audio(format_id="140"))
        self.window.video_format_combo.setCurrentIndex(self.window.video_format_combo.count() - 1)
        self.window.audio_format_combo.setCurrentIndex(self.window.audio_format_combo.count() - 1)
        spec, _, _ = self.window.resolve_format_spec()
        self.assertEqual(spec, "137+140")


class StartDownloadValidationTest(MainWindowTestCase):
    def test_empty_url_shows_warning_and_stops(self):
        self.window.url_edit.setText("")
        self.window.out_edit.setText("C:/out")
        with patch.object(QMessageBox, "warning") as warning_mock:
            self.window.start_download()
        warning_mock.assert_called_once()
        self.assertIsNone(self.window.worker)

    def test_empty_out_dir_shows_warning_and_stops(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("")
        with patch.object(QMessageBox, "warning") as warning_mock:
            self.window.start_download()
        warning_mock.assert_called_once()
        self.assertIsNone(self.window.worker)

    def test_missing_ffmpeg_shows_critical_and_stops(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        with patch.object(main_window_module, "get_ffmpeg_location", return_value=None), \
             patch.object(QMessageBox, "critical") as critical_mock:
            self.window.start_download()
        critical_mock.assert_called_once()
        self.assertIsNone(self.window.worker)

    def test_detail_mode_value_error_shows_warning(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.detail_toggle_btn.setChecked(True)  # 動画・音声とも未選択のためValueErrorになる
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(QMessageBox, "warning") as warning_mock:
            self.window.start_download()
        warning_mock.assert_called_once()
        self.assertIsNone(self.window.worker)

    def test_valid_input_starts_worker(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(main_window_module.os, "makedirs") as makedirs_mock, \
             patch.object(main_window_module, "DownloadWorker") as worker_cls:
            worker_instance = MagicMock()
            worker_cls.return_value = worker_instance
            self.window.start_download()

        makedirs_mock.assert_called_once_with("C:/out", exist_ok=True)
        worker_cls.assert_called_once()
        worker_instance.start.assert_called_once()
        self.assertFalse(self.window.download_btn.isEnabled())
        self.assertTrue(self.window.cancel_btn.isEnabled())

    def test_mismatched_detail_selection_cancelled_by_user_stops(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.detail_toggle_btn.setChecked(True)
        mismatched = make_video(format_id="399", ext="mp4", vcodec="vp9")
        self.window.video_format_combo.addItem("v", userData=mismatched)
        self.window.video_format_combo.setCurrentIndex(self.window.video_format_combo.count() - 1)

        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question_mock, \
             patch.object(main_window_module, "DownloadWorker") as worker_cls:
            self.window.start_download()

        question_mock.assert_called_once()
        worker_cls.assert_not_called()


class ConfirmHighResolutionDownloadTest(MainWindowTestCase):
    def test_no_confirmation_needed_returns_best(self):
        self.window.available_formats = [make_video(height=1080)]
        choice, fallback = self.window.confirm_high_resolution_download(
            "動画 (最高画質 mp4)", "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b", None
        )
        self.assertEqual(choice, "best")
        self.assertIsNone(fallback)

    def test_confirmation_needed_best_button_clicked(self):
        formats = [
            make_video(format_id="399", height=1440, width=2560, filesize=80_000_000),
            make_video(format_id="137", height=1080, filesize=50_000_000),
        ]
        choice, fallback = self._run_with_clicked_button_index(formats, 0)
        self.assertEqual(choice, "best")
        self.assertIsNone(fallback)

    def _run_with_clicked_button_index(self, formats, button_index):
        """QMessageBox.exec/clickedButtonをモックし、button_index番目に追加された
        ボタンがクリックされたことにして confirm_high_resolution_download を実行する"""
        self.window.available_formats = formats
        captured = {}

        original_add_button = main_window_module.QMessageBox.addButton

        def capturing_add_button(box_self, *args, **kwargs):
            btn = original_add_button(box_self, *args, **kwargs)
            captured.setdefault("buttons", []).append(btn)
            return btn

        with patch.object(main_window_module.QMessageBox, "addButton", capturing_add_button), \
             patch.object(main_window_module.QMessageBox, "exec", return_value=0), \
             patch.object(
                 main_window_module.QMessageBox,
                 "clickedButton",
                 lambda box_self: captured["buttons"][button_index],
             ):
            return self.window.confirm_high_resolution_download(
                "動画 (最高画質 mp4)", "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b", None
            )

    def test_1080p_button_clicked_returns_fallback_spec(self):
        formats = [
            make_video(format_id="399", height=1440, width=2560, filesize=80_000_000),
            make_video(format_id="137", height=1080, filesize=50_000_000),
        ]
        choice, fallback = self._run_with_clicked_button_index(formats, 1)
        self.assertEqual(choice, "1080p")
        self.assertIsNotNone(fallback)

    def test_cancel_button_clicked_returns_none(self):
        formats = [
            make_video(format_id="399", height=1440, width=2560, filesize=80_000_000),
            make_video(format_id="137", height=1080, filesize=50_000_000),
        ]
        choice, fallback = self._run_with_clicked_button_index(formats, 2)
        self.assertIsNone(choice)
        self.assertIsNone(fallback)


class ProgressAndFinishHandlersTest(MainWindowTestCase):
    def test_on_progress_updates_bar_and_status(self):
        self.window.on_progress(42.5, "42.5% 速度:1MiB/s 残り:01:00")
        self.assertEqual(self.window.progress_bar.value(), 42)
        self.assertEqual(self.window.status_label.text(), "42.5% 速度:1MiB/s 残り:01:00")

    def test_on_finished_ok_resets_state(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.download_btn.setEnabled(True)
        self.window.cancel_btn.setEnabled(True)
        with patch.object(self.window, "open_output_folder") as open_folder_mock:
            self.window.on_finished_ok()
        self.assertEqual(self.window.url_edit.text(), "")
        self.assertFalse(self.window.download_btn.isEnabled())
        self.assertFalse(self.window.cancel_btn.isEnabled())
        self.assertTrue(self.window.open_folder_btn.isEnabled())
        self.assertEqual(self.window.status_label.text(), "完了")
        open_folder_mock.assert_called_once()

    def test_on_finished_error_shows_dialog_and_resets_state(self):
        self.window.info_ready = True
        self.window.cancel_btn.setEnabled(True)
        with patch.object(QMessageBox, "critical") as critical_mock:
            self.window.on_finished_error("failed")
        critical_mock.assert_called_once()
        self.assertFalse(self.window.cancel_btn.isEnabled())
        self.assertTrue(self.window.download_btn.isEnabled())
        self.assertEqual(self.window.status_label.text(), "エラーまたはキャンセル")

    def test_cancel_download_calls_worker_cancel(self):
        worker = MagicMock()
        self.window.worker = worker
        self.window.cancel_download()
        worker.cancel.assert_called_once()
        self.assertEqual(self.window.status_label.text(), "キャンセル中...")

    def test_cancel_download_noop_without_worker(self):
        self.window.worker = None
        self.window.cancel_download()  # 例外にならないことを確認


class SignalWiringTest(MainWindowTestCase):
    """MainWindow._connect_signalsがウィジェットのシグナルを正しいハンドラへ接続していることを、
    ハンドラを直接呼ぶのではなく実際のウィジェット操作(click/setChecked/setText等)を経由して検証する。
    (main_window_ui.Ui_MainWindowとmain_window.MainWindowの分割により、ウィジェット生成と
    シグナル接続が別ファイルに分かれたため、配線の取り違えを検知できるテストを別途用意する)"""

    def test_paste_button_click_pastes_clipboard_url(self):
        with patch.object(QApplication, "clipboard") as clipboard_getter:
            mock_clipboard = MagicMock()
            mock_clipboard.text.return_value = "https://example.com/watch?v=abc"
            clipboard_getter.return_value = mock_clipboard
            self.window.paste_btn.click()
        self.assertEqual(self.window.url_edit.text(), "https://example.com/watch?v=abc")

    def test_url_edit_text_changed_triggers_handler(self):
        self.window.title_label.setText("Existing Title")
        self.window.url_edit.setText("not a url")
        self.assertEqual(self.window.title_label.text(), "")
        self.assertEqual(self.window.status_label.text(), IDLE_STATUS_TEXT)

    def test_format_combo_change_triggers_note_update(self):
        self.window.detail_toggle_btn.setChecked(False)
        with patch.object(main_window_module, "compute_simple_format_note", return_value="") as note_mock:
            self.window.format_combo.setCurrentIndex(1)
        note_mock.assert_called()

    def test_browse_button_click_updates_out_dir(self):
        with patch.object(main_window_module, "QFileDialog") as file_dialog_mock:
            file_dialog_mock.getExistingDirectory.return_value = "C:/chosen"
            self.window.browse_btn.click()
        self.assertEqual(self.window.out_edit.text(), "C:/chosen")

    def test_download_button_click_triggers_start_download(self):
        self.window.url_edit.setText("")
        self.window.out_edit.setText("C:/out")
        self.window.download_btn.setEnabled(True)
        with patch.object(QMessageBox, "warning") as warning_mock:
            self.window.download_btn.click()
        warning_mock.assert_called_once()

    def test_cancel_button_click_cancels_worker(self):
        worker = MagicMock()
        self.window.worker = worker
        self.window.cancel_btn.setEnabled(True)
        self.window.cancel_btn.click()
        worker.cancel.assert_called_once()
        self.assertEqual(self.window.status_label.text(), "キャンセル中...")

    def test_open_folder_button_click_triggers_open(self):
        self.window.last_output_dir = "C:/out"
        self.window.open_folder_btn.setEnabled(True)
        with patch.object(main_window_module.os.path, "isdir", return_value=True), \
             patch.object(self.window, "find_open_explorer_window", return_value=None), \
             patch.object(main_window_module.os, "startfile") as startfile_mock:
            self.window.open_folder_btn.click()
        startfile_mock.assert_called_once_with("C:/out")

    def test_log_toggle_button_shows_log_view(self):
        self.window.log_toggle_btn.setChecked(True)
        self.assertTrue(self.window.log_view.isVisible())
        self.assertEqual(self.window.log_toggle_btn.text(), "ログ ▴")


if __name__ == "__main__":
    unittest.main()
