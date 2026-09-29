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

from PyQt6.QtCore import QBuffer, QEvent, QIODevice, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QLineEdit, QMessageBox

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


def make_storyboard(format_id="sb3", width=48, height=27, rows=10, columns=10, fps=100 / 635,
                     fragment_urls=("https://example.com/sb3/M0.jpg",)):
    return {
        "format_id": format_id,
        "format_note": "storyboard",
        "vcodec": "none",
        "acodec": "none",
        "width": width,
        "height": height,
        "rows": rows,
        "columns": columns,
        "fps": fps,
        "fragments": [{"url": url} for url in fragment_urls],
    }


def encode_pixmap_png(pixmap: QPixmap) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


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
        # url_edit にURLらしき文字列を設定するテストはon_url_changed経由で
        # _info_fetch_timer(700msのsingleShot)を起動する。テスト自身はイベントループを
        # 回さないため通常は発火しないが、後から(別のテストや本体コードで)何かが
        # イベントループを一度でも回すと、対象が破棄済みの本テストのwindowであっても
        # タイマーは生きたままFormatListWorkerを起動しようとし、実ネットワーク通信や
        # クラッシュを招く。テスト終了時に必ず止めて、次のイベントループ処理へ
        # 持ち越さないようにする
        self.addCleanup(self.window._info_fetch_timer.stop)
        # isVisible()は祖先を含めた実際の表示状態を返すため、offscreenプラットフォームでも
        # トップレベルウィンドウ自体をshowしておく必要がある
        self.window.show()


class EventFilterTest(MainWindowTestCase):
    """入力欄をクリックした後、フォーカスを持たない場所(ラベル・背景等)を
    クリックしてもカーソル/フォーカス枠が残り続けてしまう問題への対応を検証する"""

    @staticmethod
    def _mouse_press_event():
        return QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(0, 0), QPointF(0, 0),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        )

    def test_click_outside_focused_line_edit_clears_focus(self):
        # offscreenプラットフォームでは実際のsetFocus()/hasFocus()が信頼できないため、
        # QApplication.focusWidget()の戻り値をモックし、clearFocus()の呼び出し有無で検証する
        with patch.object(QApplication, "focusWidget", return_value=self.window.url_edit), \
             patch.object(QApplication, "widgetAt", return_value=self.window.out_edit), \
             patch.object(type(self.window.url_edit), "clearFocus") as clear_focus_mock:
            self.window.eventFilter(self.window, self._mouse_press_event())

        clear_focus_mock.assert_called_once()

    def test_click_on_focused_line_edit_itself_keeps_focus(self):
        # クリックした先がまさにフォーカス中のウィジェット自身なら解除しない
        with patch.object(QApplication, "focusWidget", return_value=self.window.url_edit), \
             patch.object(QApplication, "widgetAt", return_value=self.window.url_edit), \
             patch.object(type(self.window.url_edit), "clearFocus") as clear_focus_mock:
            self.window.eventFilter(self.window, self._mouse_press_event())

        clear_focus_mock.assert_not_called()

    def test_non_mouse_press_event_is_ignored(self):
        move_event = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(0, 0), QPointF(0, 0),
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        )

        with patch.object(QApplication, "focusWidget", return_value=self.window.url_edit), \
             patch.object(QApplication, "widgetAt", return_value=self.window.out_edit), \
             patch.object(type(self.window.url_edit), "clearFocus") as clear_focus_mock:
            self.window.eventFilter(self.window, move_event)

        clear_focus_mock.assert_not_called()

    def test_focus_widget_from_another_window_is_left_alone(self):
        # 親を持たないQLineEditはwindow()が自分自身を返すため、このウィンドウには属さない
        # (例: 別ダイアログの入力欄)ケースを再現できる。その場合は関与せず何もしない
        unrelated_edit = QLineEdit()
        self.addCleanup(unrelated_edit.deleteLater)

        with patch.object(QApplication, "focusWidget", return_value=unrelated_edit), \
             patch.object(QApplication, "widgetAt", return_value=self.window.out_edit), \
             patch.object(type(unrelated_edit), "clearFocus") as clear_focus_mock:
            self.window.eventFilter(self.window, self._mouse_press_event())

        clear_focus_mock.assert_not_called()


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

    def test_changing_to_new_valid_url_resets_previous_format_and_progress_state(self):
        # 動画Aを読み込み済みの状態を再現する(フォーマット選択・mp3変換・進捗バー等)
        self.window.video_format_combo.addItem("dummy")
        self.window.video_format_combo.setEnabled(True)
        self.window.available_formats = [make_video()]
        self.window.mp3_checkbox.setEnabled(True)
        self.window.mp3_checkbox.setChecked(True)
        self.window.mp3_label.setEnabled(True)
        self.window.merge_note_label.setText("動画と音声を合成してダウンロードします")
        self.window.progress_bar.setValue(55)
        self.window.video_duration = 300.0

        # URLを空にせず、別の有効なURLに貼り替えた場合でも即座に(fetch_formats実行前に)
        # 前の動画に対する選択内容がリセットされることを確認する
        self.window.on_url_changed("https://example.com/watch?v=different")

        self.assertEqual(self.window.video_format_combo.count(), 0)
        self.assertFalse(self.window.video_format_combo.isEnabled())
        self.assertEqual(self.window.available_formats, [])
        self.assertFalse(self.window.mp3_checkbox.isChecked())
        self.assertFalse(self.window.mp3_checkbox.isEnabled())
        self.assertFalse(self.window.mp3_label.isEnabled())
        self.assertEqual(self.window.merge_note_label.text(), "")
        self.assertLess(self.window.progress_bar.value(), 0)  # reset()後は無効値
        self.assertIsNone(self.window.video_duration)
        # リセット後も有効なURLなので取得タイマーは開始される
        self.assertTrue(self.window._info_fetch_timer.isActive())

    def test_clearing_url_after_video_loaded_resets_and_disables_clip_slider(self):
        # 動画読み込み済みの状態を再現する
        self.window.video_duration = 635.0
        self.window.storyboard_format = make_storyboard()
        self.window._storyboard_cache["https://example.com/x.jpg"] = QPixmap(10, 10)
        self.window.detail_container.setEnabled(True)
        self.window.clip_range_slider.setRange(0, 635)
        self.window.clip_range_slider.setValues(60, 600)
        self.window.clip_duration_label.setText("動画の長さ: 10:35")
        self.window.clip_start_edit.setText("1:00")
        self.window.clip_end_edit.setText("2:00")

        self.window.on_url_changed("")

        self.assertFalse(self.window.clip_range_slider.isEnabled())
        self.assertIsNone(self.window.video_duration)
        self.assertIsNone(self.window.storyboard_format)
        self.assertEqual(self.window._storyboard_cache, {})
        self.assertEqual(self.window.clip_duration_label.text(), "")
        # 古い動画のクリップ範囲が新しい動画に持ち込まれないよう、テキストもクリアする
        self.assertEqual(self.window.clip_start_edit.text(), "")
        self.assertEqual(self.window.clip_end_edit.text(), "")
        # スライダーのハンドル位置・範囲も古い動画の長さのまま残らないようリセットされる
        self.assertEqual(self.window.clip_range_slider.values(), (0, 100))


class FetchFormatsTest(MainWindowTestCase):
    def test_switching_to_new_url_clears_stale_clip_range_text(self):
        # 前の動画に対して入力したクリップ範囲が、別の動画を取得し直した際に
        # そのまま(長さの整合性を確認されずに)残ってしまわないことを確認する
        self.window.video_duration = 635.0
        self.window.clip_start_edit.setText("1:00")
        self.window.clip_end_edit.setText("2:00")

        with patch.object(main_window_module, "FormatListWorker") as worker_cls:
            worker_cls.return_value = MagicMock()
            self.window.url_edit.setText("https://example.com/watch?v=new")
            self.window.fetch_formats()

        self.assertEqual(self.window.clip_start_edit.text(), "")
        self.assertEqual(self.window.clip_end_edit.text(), "")
        self.assertIsNone(self.window.video_duration)


class OnManualToggledTest(MainWindowTestCase):
    def test_checked_shows_manual_container_and_hides_auto(self):
        self.window.manual_toggle_btn.setChecked(True)
        self.assertTrue(self.window.manual_container.isVisible())
        self.assertFalse(self.window.auto_format_container.isVisible())
        self.assertEqual(self.window.manual_toggle_btn.text(), "自動設定 ▴")

    def test_unchecked_shows_auto_and_hides_manual(self):
        self.window.manual_toggle_btn.setChecked(True)
        self.window.manual_toggle_btn.setChecked(False)
        self.assertFalse(self.window.manual_container.isVisible())
        self.assertTrue(self.window.auto_format_container.isVisible())
        self.assertEqual(self.window.manual_toggle_btn.text(), "手動設定 ▾")

    def test_combos_disabled_when_no_formats_fetched_yet(self):
        self.window.manual_toggle_btn.setChecked(True)
        self.assertFalse(self.window.video_format_combo.isEnabled())
        self.assertFalse(self.window.audio_format_combo.isEnabled())


class OnFormatsFetchedTest(MainWindowTestCase):
    def test_populates_combos_and_counts(self):
        formats = [make_video(format_id="137"), make_audio(format_id="140")]
        worker = MagicMock()
        self.window.format_worker = worker

        self.window.on_formats_fetched(formats, "Sample Title", b"", 125.0, worker, auto=False)

        # 各コンボには「なし」+実フォーマットの2件
        self.assertEqual(self.window.video_format_combo.count(), 2)
        self.assertEqual(self.window.audio_format_combo.count(), 2)
        self.assertEqual(self.window.title_label.text(), "Sample Title")
        self.assertEqual(self.window.status_label.text(), "動画1件・音声1件を検出しました")
        self.assertTrue(self.window.info_ready)
        self.assertTrue(self.window.download_btn.isEnabled())
        self.assertTrue(self.window.clip_range_slider.isEnabled())
        self.assertEqual(self.window.clip_range_slider.values(), (0, 125))

    def test_stale_worker_result_is_ignored(self):
        stale_worker = MagicMock()
        current_worker = MagicMock()
        self.window.format_worker = current_worker

        self.window.on_formats_fetched([make_video()], "Should Not Apply", b"", 60.0, stale_worker, auto=False)

        self.assertEqual(self.window.title_label.text(), "")
        self.assertFalse(self.window.info_ready)

    def test_mismatched_formats_sorted_after_matched_ones(self):
        matched = make_video(format_id="137", ext="mp4", vcodec="avc1.640028")
        mismatched = make_video(format_id="399", ext="mp4", vcodec="vp9")
        worker = MagicMock()
        self.window.format_worker = worker

        self.window.on_formats_fetched([mismatched, matched], "T", b"", 60.0, worker, auto=False)

        # index 0 は「なし」、1件目の実データが先着(=互換フォーマット)であるべき
        self.assertEqual(self.window.video_format_combo.itemData(1)["format_id"], "137")
        self.assertEqual(self.window.video_format_combo.itemData(2)["format_id"], "399")

    def test_thumbnail_is_scaled_to_label_size_without_distortion(self):
        # 元画像はラベルとアスペクト比が異なる(1280x720)ものを使い、
        # 単純な引き伸ばしではなく中央切り出しでラベルの実寸に収まることを確認する
        source = QPixmap(1280, 720)
        source.fill(Qt.GlobalColor.red)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        source.save(buffer, "PNG")
        thumbnail_bytes = bytes(buffer.data())

        worker = MagicMock()
        self.window.format_worker = worker

        self.window.on_formats_fetched([make_video()], "T", thumbnail_bytes, 60.0, worker, auto=False)

        result = self.window.thumbnail_label.pixmap()
        self.assertIsNotNone(result)
        self.assertEqual(result.size(), self.window.thumbnail_label.size())


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


class OnDetailToggledTest(MainWindowTestCase):
    def test_checked_shows_detail_container(self):
        self.window.detail_toggle_btn.setChecked(True)
        self.assertTrue(self.window.detail_container.isVisible())
        self.assertEqual(self.window.detail_toggle_btn.text(), "詳細設定 ▴")

    def test_unchecked_hides_detail_container(self):
        self.window.detail_toggle_btn.setChecked(True)
        self.window.detail_toggle_btn.setChecked(False)
        self.assertFalse(self.window.detail_container.isVisible())
        self.assertEqual(self.window.detail_toggle_btn.text(), "詳細設定 ▾")

    def test_starts_collapsed(self):
        self.assertFalse(self.window.detail_container.isVisible())
        self.assertFalse(self.window.detail_toggle_btn.isChecked())


class ClipSliderSyncTest(MainWindowTestCase):
    def test_update_clip_slider_range_enables_and_sets_full_range(self):
        self.window.video_duration = 125.0
        self.window._update_clip_slider_range()
        self.assertTrue(self.window.clip_range_slider.isEnabled())
        self.assertEqual(self.window.clip_range_slider.values(), (0, 125))
        self.assertIn("2:05", self.window.clip_duration_label.text())

    def test_update_clip_slider_range_disables_when_duration_unknown(self):
        self.window.video_duration = None
        self.window._update_clip_slider_range()
        self.assertFalse(self.window.clip_range_slider.isEnabled())
        self.assertEqual(self.window.clip_duration_label.text(), "")

    def test_slider_drag_updates_text_fields(self):
        self.window.video_duration = 125.0
        self.window._update_clip_slider_range()
        self.window.on_clip_slider_changed(60, 100)
        self.assertEqual(self.window.clip_start_edit.text(), "1:00")
        self.assertEqual(self.window.clip_end_edit.text(), "1:40")

    def test_slider_drag_to_full_range_clears_text_fields(self):
        self.window.video_duration = 125.0
        self.window._update_clip_slider_range()
        self.window.on_clip_slider_changed(0, 125)
        self.assertEqual(self.window.clip_start_edit.text(), "")
        self.assertEqual(self.window.clip_end_edit.text(), "")

    def test_text_edit_updates_slider(self):
        self.window.video_duration = 125.0
        self.window._update_clip_slider_range()
        self.window.clip_start_edit.setText("1:00")
        self.window.clip_end_edit.setText("2:00")
        self.assertEqual(self.window.clip_range_slider.values(), (60, 120))

    def test_invalid_text_does_not_move_slider(self):
        self.window.video_duration = 125.0
        self.window._update_clip_slider_range()
        self.window.clip_start_edit.setText("1:00")
        self.window.clip_start_edit.setText("not a time")
        # 解析できない間は開始側を直前の値(60)のまま保つ
        self.assertEqual(self.window.clip_range_slider.values(), (60, 125))


class OnClipPreviewRequestedTest(MainWindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.video_duration = 635.0

    def test_does_nothing_without_storyboard_format(self):
        self.window.storyboard_format = None
        with patch.object(main_window_module, "StoryboardFragmentWorker") as worker_cls:
            self.window.on_clip_preview_requested("low", 10)
        worker_cls.assert_not_called()

    def test_cache_hit_applies_immediately_without_network(self):
        storyboard = make_storyboard()
        self.window.storyboard_format = storyboard
        sprite = QPixmap(480, 270)
        sprite.fill()
        self.window._storyboard_cache[storyboard["fragments"][0]["url"]] = sprite

        with patch.object(main_window_module, "StoryboardFragmentWorker") as worker_cls:
            self.window.on_clip_preview_requested("low", 10)

        worker_cls.assert_not_called()

    def test_cache_miss_starts_worker_for_fragment_url(self):
        storyboard = make_storyboard()
        self.window.storyboard_format = storyboard

        with patch.object(main_window_module, "StoryboardFragmentWorker") as worker_cls:
            worker_instance = MagicMock()
            worker_cls.return_value = worker_instance
            self.window.on_clip_preview_requested("low", 10)

        worker_cls.assert_called_once_with(storyboard["fragments"][0]["url"])
        worker_instance.start.assert_called_once()
        self.assertIn(storyboard["fragments"][0]["url"], self.window._pending_storyboard_urls)

    def test_duplicate_request_for_pending_fragment_is_skipped(self):
        storyboard = make_storyboard()
        self.window.storyboard_format = storyboard
        self.window._pending_storyboard_urls.add(storyboard["fragments"][0]["url"])

        with patch.object(main_window_module, "StoryboardFragmentWorker") as worker_cls:
            self.window.on_clip_preview_requested("low", 10)

        worker_cls.assert_not_called()

    def test_fetch_failure_clears_pending_state(self):
        storyboard = make_storyboard()
        self.window.storyboard_format = storyboard
        url = storyboard["fragments"][0]["url"]
        worker = MagicMock()
        worker.url = url
        self.window._pending_storyboard_urls.add(url)
        self.window._storyboard_workers.add(worker)

        self.window._on_storyboard_fragment_failed(worker)

        self.assertNotIn(url, self.window._pending_storyboard_urls)
        self.assertNotIn(worker, self.window._storyboard_workers)

    def test_fetch_success_caches_and_applies_when_handle_still_active(self):
        storyboard = make_storyboard()
        self.window.storyboard_format = storyboard
        url = storyboard["fragments"][0]["url"]
        worker = MagicMock()
        worker.url = url
        self.window._pending_storyboard_urls.add(url)
        self.window._storyboard_workers.add(worker)
        self.window.clip_range_slider.setRange(0, 635)
        self.window.clip_range_slider.setValues(10, 600)
        self.window.clip_range_slider._active_handle = "low"

        sprite = QPixmap(480, 270)
        sprite.fill()
        data = encode_pixmap_png(sprite)

        with patch.object(self.window.clip_range_slider, "set_preview_pixmap") as set_pixmap_mock:
            self.window._on_storyboard_fragment_fetched(worker, data, "low")

        self.assertNotIn(url, self.window._pending_storyboard_urls)
        self.assertIn(url, self.window._storyboard_cache)
        set_pixmap_mock.assert_called_once()
        applied_which, applied_pixmap = set_pixmap_mock.call_args[0]
        self.assertEqual(applied_which, "low")
        self.assertEqual((applied_pixmap.width(), applied_pixmap.height()), (48, 27))

    def test_fetch_success_ignored_when_handle_no_longer_active(self):
        storyboard = make_storyboard()
        self.window.storyboard_format = storyboard
        url = storyboard["fragments"][0]["url"]
        worker = MagicMock()
        worker.url = url
        self.window.clip_range_slider._active_handle = "high"  # 既に別のハンドルに切り替わっている

        sprite = QPixmap(480, 270)
        sprite.fill()
        data = encode_pixmap_png(sprite)

        with patch.object(self.window.clip_range_slider, "set_preview_pixmap") as set_pixmap_mock:
            self.window._on_storyboard_fragment_fetched(worker, data, "low")

        set_pixmap_mock.assert_not_called()
        # 取得結果自体は次回以降のために引き続きキャッシュされる
        self.assertIn(url, self.window._storyboard_cache)


class OnManualSelectionChangedTest(MainWindowTestCase):
    def setUp(self):
        super().setUp()
        self.window.manual_toggle_btn.setChecked(True)
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

    def test_auto_mode_disables_mp3_and_clears_note(self):
        self.window.manual_toggle_btn.setChecked(False)
        self.assertFalse(self.window.mp3_checkbox.isEnabled())
        self.assertEqual(self.window.merge_note_label.text(), "")


class ResolveFormatSpecTest(MainWindowTestCase):
    def test_auto_mode_uses_combo_label(self):
        self.window.manual_toggle_btn.setChecked(False)
        self.window.format_combo.setCurrentText("動画 (最高画質 mp4)")
        spec, postprocessors, _ = self.window.resolve_format_spec()
        self.assertIn("avc1", spec)

    def test_manual_mode_without_selection_raises(self):
        self.window.manual_toggle_btn.setChecked(True)
        with self.assertRaises(ValueError):
            self.window.resolve_format_spec()

    def test_manual_mode_with_video_and_audio_merges_ids(self):
        self.window.manual_toggle_btn.setChecked(True)
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

    def test_manual_mode_value_error_shows_warning(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.manual_toggle_btn.setChecked(True)  # 動画・音声とも未選択のためValueErrorになる
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

    def test_makedirs_failure_shows_input_error_and_stops(self):
        """予約デバイス名(CON等)や同名ファイルが存在するパスを手入力した場合、
        他の入力ミスと同じ「入力エラー」で案内する。try/exceptがないと
        グローバルのexcepthookに捕まり「予期しないエラー」という技術的な文言になる"""
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out/CON")
        with (
            patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"),
            patch.object(main_window_module.os, "makedirs", side_effect=OSError("Invalid argument")),
            patch.object(QMessageBox, "warning") as warning_mock,
            patch.object(main_window_module, "DownloadWorker") as worker_cls,
        ):
            self.window.start_download()

        warning_mock.assert_called_once()
        self.assertEqual(warning_mock.call_args[0][1], "入力エラー")
        worker_cls.assert_not_called()
        self.assertIsNone(self.window.worker)

    def test_clip_range_is_passed_to_worker(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.clip_start_edit.setText("1:00")
        self.window.clip_end_edit.setText("2:00")
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(main_window_module.os, "makedirs"), \
             patch.object(main_window_module, "DownloadWorker") as worker_cls:
            worker_cls.return_value = MagicMock()
            self.window.start_download()

        request = worker_cls.call_args.args[0]
        self.assertEqual(request.clip_start, 60.0)
        self.assertEqual(request.clip_end, 120.0)

    def test_invalid_clip_range_shows_warning_and_stops(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.clip_start_edit.setText("2:00")
        self.window.clip_end_edit.setText("1:00")
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(QMessageBox, "warning") as warning_mock, \
             patch.object(main_window_module, "DownloadWorker") as worker_cls:
            self.window.start_download()

        warning_mock.assert_called_once()
        worker_cls.assert_not_called()

    def test_clip_start_beyond_video_duration_shows_warning_and_stops(self):
        # 前の動画(長さ不明時や別動画)の入力が残っていた等、動画より長い開始時刻を
        # 指定した場合はダウンロード開始前に弾く
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.video_duration = 90.0
        self.window.clip_start_edit.setText("2:00")  # 120秒 > 90秒
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(QMessageBox, "warning") as warning_mock, \
             patch.object(main_window_module, "DownloadWorker") as worker_cls:
            self.window.start_download()

        warning_mock.assert_called_once()
        worker_cls.assert_not_called()

    def test_clip_end_beyond_video_duration_shows_warning_and_stops(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.video_duration = 90.0
        self.window.clip_end_edit.setText("2:00")  # 120秒 > 90秒
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(QMessageBox, "warning") as warning_mock, \
             patch.object(main_window_module, "DownloadWorker") as worker_cls:
            self.window.start_download()

        warning_mock.assert_called_once()
        worker_cls.assert_not_called()

    def test_clip_end_equal_to_video_duration_is_allowed(self):
        # 末尾(動画の長さそのもの)までを終了時刻に指定するのは正当な範囲
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.video_duration = 90.0
        self.window.clip_end_edit.setText("1:30")  # 90秒 == 90秒
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(main_window_module.os, "makedirs"), \
             patch.object(main_window_module, "DownloadWorker") as worker_cls:
            worker_cls.return_value = MagicMock()
            self.window.start_download()

        worker_cls.assert_called_once()

    def test_clip_range_beyond_duration_skipped_when_duration_unknown(self):
        # ライブ配信等、長さが不明な場合は範囲チェックできないためスキップされる
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.video_duration = None
        self.window.clip_start_edit.setText("100:00:00")
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(main_window_module.os, "makedirs"), \
             patch.object(main_window_module, "DownloadWorker") as worker_cls:
            worker_cls.return_value = MagicMock()
            self.window.start_download()

        worker_cls.assert_called_once()

    def test_mismatched_manual_selection_cancelled_by_user_stops(self):
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.window.out_edit.setText("C:/out")
        self.window.manual_toggle_btn.setChecked(True)
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
        self.window.manual_toggle_btn.setChecked(False)
        with patch.object(main_window_module, "compute_auto_format_note", return_value="") as note_mock:
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

    def test_open_folder_button_click_triggers_open_on_windows(self):
        self.window.last_output_dir = "C:/out"
        self.window.open_folder_btn.setEnabled(True)
        with patch.object(main_window_module.sys, "platform", "win32"), \
             patch.object(main_window_module.os.path, "isdir", return_value=True), \
             patch.object(self.window, "find_open_explorer_window", return_value=None), \
             patch.object(main_window_module.QDesktopServices, "openUrl") as open_url_mock:
            self.window.open_folder_btn.click()
        open_url_mock.assert_called_once()

    def test_open_folder_button_click_triggers_open_on_macos(self):
        self.window.last_output_dir = "/tmp/out"
        self.window.open_folder_btn.setEnabled(True)
        with patch.object(main_window_module.sys, "platform", "darwin"), \
             patch.object(main_window_module.os.path, "isdir", return_value=True), \
             patch.object(main_window_module.QDesktopServices, "openUrl") as open_url_mock:
            self.window.open_folder_btn.click()
        open_url_mock.assert_called_once()
        opened_url = open_url_mock.call_args[0][0]
        self.assertEqual(opened_url.toLocalFile(), "/tmp/out")

    def test_log_toggle_button_shows_log_view(self):
        self.window.log_toggle_btn.setChecked(True)
        self.assertTrue(self.window.log_view.isVisible())
        self.assertEqual(self.window.log_toggle_btn.text(), "ログ ▴")


class CloseEventTest(MainWindowTestCase):
    """終了時にワーカースレッドを止めて終了を待つこと。待たずに閉じると
    QThreadがrun()のままインタプリタ終了処理に入り、yt-dlp/ffmpegが中途半端に
    打ち切られて未完成ファイルが残る"""

    @staticmethod
    def _running_worker():
        worker = MagicMock()
        worker.isRunning.return_value = True
        worker.wait.return_value = True
        return worker

    @staticmethod
    def _event():
        event = MagicMock()
        event.accepted = True
        return event

    def test_no_worker_closes_immediately(self):
        event = self._event()
        self.window.closeEvent(event)
        event.accept.assert_called_once()
        event.ignore.assert_not_called()

    def test_running_download_asks_and_cancels_then_waits(self):
        worker = self._running_worker()
        self.window.worker = worker
        event = self._event()

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.closeEvent(event)

        worker.cancel.assert_called_once()
        worker.wait.assert_called_once()
        event.accept.assert_called_once()
        event.ignore.assert_not_called()

    def test_declining_the_prompt_keeps_the_window_open(self):
        worker = self._running_worker()
        self.window.worker = worker
        event = self._event()

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window.closeEvent(event)

        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        worker.cancel.assert_not_called()
        worker.wait.assert_not_called()

    def test_waits_for_background_fetch_workers_without_prompting(self):
        """フォーマット取得・ストーリーボード取得はユーザー操作を伴わない短い処理なので、
        確認ダイアログは出さずに終了だけ待つ"""
        format_worker = self._running_worker()
        storyboard_worker = self._running_worker()
        self.window.format_worker = format_worker
        self.window._storyboard_workers.add(storyboard_worker)
        event = self._event()

        with patch.object(QMessageBox, "question") as question_mock:
            self.window.closeEvent(event)

        question_mock.assert_not_called()
        format_worker.wait.assert_called_once()
        storyboard_worker.wait.assert_called_once()
        event.accept.assert_called_once()

    def test_timed_out_worker_is_logged_and_close_proceeds(self):
        """cancel()は進捗フックの区切りでしか効かないため、後処理中は待ち切れない
        ことがある。その場合もUIスレッドからできることはないので記録だけ残して閉じる"""
        worker = self._running_worker()
        worker.wait.return_value = False
        self.window.worker = worker
        event = self._event()

        with (
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
            patch.object(main_window_module, "log_debug") as log_debug_mock,
        ):
            self.window.closeEvent(event)

        log_debug_mock.assert_called_once()
        event.accept.assert_called_once()

    def test_finished_worker_is_not_waited_on(self):
        worker = MagicMock()
        worker.isRunning.return_value = False
        self.window.worker = worker
        event = self._event()

        self.window.closeEvent(event)

        worker.cancel.assert_not_called()
        worker.wait.assert_not_called()
        event.accept.assert_called_once()


class UpdateAvailablePromptTest(MainWindowTestCase):
    """新バージョン検出時、動画ダウンロード中でなければ確認ダイアログを出し、
    承諾された場合のみダウンロードを開始すること"""

    def test_offers_update_and_starts_download_on_yes(self):
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as question_mock, \
             patch.object(MainWindow, "_start_update_download") as start_mock:
            self.window.on_update_available("9.9.9", "https://example.com/Setup.exe", "Setup.exe")

        question_mock.assert_called_once()
        start_mock.assert_called_once_with("https://example.com/Setup.exe", "Setup.exe")

    def test_declining_does_not_start_download(self):
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
             patch.object(MainWindow, "_start_update_download") as start_mock:
            self.window.on_update_available("9.9.9", "https://example.com/Setup.exe", "Setup.exe")

        start_mock.assert_not_called()

    def test_no_prompt_while_video_download_is_running(self):
        worker = MagicMock()
        worker.isRunning.return_value = True
        self.window.worker = worker

        with patch.object(QMessageBox, "question") as question_mock, \
             patch.object(MainWindow, "_start_update_download") as start_mock:
            self.window.on_update_available("9.9.9", "https://example.com/Setup.exe", "Setup.exe")

        question_mock.assert_not_called()
        start_mock.assert_not_called()

    def test_missing_asset_info_is_ignored(self):
        with patch.object(QMessageBox, "question") as question_mock:
            self.window.on_update_available("9.9.9", "", "")

        question_mock.assert_not_called()


class UpdateDownloadCallbackTest(MainWindowTestCase):
    """アップデート本体のダウンロード完了/失敗時のコールバックを、実際のQThread・
    ネットワーク通信を使わずMagicMockのworker/progressダイアログで検証する"""

    def test_finished_switches_progress_to_applying_state_without_closing_it(self):
        worker = MagicMock()
        progress = MagicMock()
        self.window.update_download_worker = worker

        with patch.object(MainWindow, "close") as close_mock:
            self.window.on_update_download_finished("C:/tmp/Setup.exe", worker, progress)

        # ダウンロード完了後もダイアログを閉じず、「適用中」の不確定進捗表示へ
        # 切り替えるだけにする(closeEvent側で実際にウィンドウごと破棄されるまで見せ続ける)
        progress.close.assert_not_called()
        progress.setLabelText.assert_called_once()
        progress.setRange.assert_called_once_with(0, 0)
        progress.setCancelButton.assert_called_once_with(None)
        worker.wait.assert_called_once()
        self.assertEqual(self.window._pending_update_path, "C:/tmp/Setup.exe")
        self.assertIsNone(self.window.update_download_worker)
        close_mock.assert_called_once()

    def test_stale_worker_callback_is_ignored(self):
        stale_worker = MagicMock()
        current_worker = MagicMock()
        self.window.update_download_worker = current_worker
        progress = MagicMock()

        with patch.object(MainWindow, "close") as close_mock:
            self.window.on_update_download_finished("C:/tmp/Setup.exe", stale_worker, progress)

        progress.setLabelText.assert_not_called()
        close_mock.assert_not_called()
        self.assertIsNone(self.window._pending_update_path)
        # 現在進行中のworker参照は、無関係な古いコールバックによって消されない
        self.assertIs(self.window.update_download_worker, current_worker)

    def test_error_closes_progress_and_shows_message(self):
        worker = MagicMock()
        progress = MagicMock()
        self.window.update_download_worker = worker

        with patch.object(QMessageBox, "critical") as critical_mock:
            self.window.on_update_download_error("network down", worker, progress)

        progress.close.assert_called_once()
        critical_mock.assert_called_once()
        self.assertIsNone(self.window.update_download_worker)

    def test_cancellation_closes_progress_without_error_message(self):
        worker = MagicMock()
        progress = MagicMock()
        self.window.update_download_worker = worker

        with patch.object(QMessageBox, "critical") as critical_mock:
            self.window.on_update_download_cancelled(worker, progress)

        progress.close.assert_called_once()
        critical_mock.assert_not_called()
        self.assertIsNone(self.window.update_download_worker)

    def test_stale_cancellation_is_ignored(self):
        self.window.update_download_worker = MagicMock()
        progress = MagicMock()
        self.window.on_update_download_cancelled(MagicMock(), progress)
        progress.close.assert_not_called()


class CloseEventUpdateApplyTest(MainWindowTestCase):
    """closeEventでウィンドウの終了が確定した後にのみ、ダウンロード済みの
    アップデートを適用すること"""

    def test_pending_update_is_applied_before_closing(self):
        self.window._pending_update_path = "C:/tmp/Setup.exe"
        event = CloseEventTest._event()

        with patch.object(main_window_module, "apply_downloaded_update") as apply_mock:
            self.window.closeEvent(event)

        apply_mock.assert_called_once_with("C:/tmp/Setup.exe")
        self.assertIsNone(self.window._pending_update_path)
        event.accept.assert_called_once()

    def test_apply_failure_shows_error_but_still_closes(self):
        self.window._pending_update_path = "C:/tmp/Setup.exe"
        event = CloseEventTest._event()

        with patch.object(
                main_window_module, "apply_downloaded_update", side_effect=RuntimeError("boom")
             ), \
             patch.object(QMessageBox, "critical") as critical_mock:
            self.window.closeEvent(event)

        critical_mock.assert_called_once()
        event.accept.assert_called_once()

    def test_declining_download_cancellation_prompt_also_cancels_pending_update(self):
        """動画ダウンロード中に終了確認で「いいえ」を選んだ場合、適用予定だった
        アップデートも一緒に取り消す(強制終了でダウンロードが壊れるのを防ぐため)"""
        worker = MagicMock()
        worker.isRunning.return_value = True
        self.window.worker = worker
        self.window._pending_update_path = "C:/tmp/Setup.exe"
        event = CloseEventTest._event()

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
             patch.object(QMessageBox, "information") as information_mock, \
             patch.object(main_window_module, "apply_downloaded_update") as apply_mock:
            self.window.closeEvent(event)

        apply_mock.assert_not_called()
        information_mock.assert_called_once()
        self.assertIsNone(self.window._pending_update_path)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()


if __name__ == "__main__":
    unittest.main()
