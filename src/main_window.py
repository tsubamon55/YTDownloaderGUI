"""メインウィンドウ(状態管理とイベントハンドリング)

ウィジェットの生成・レイアウトはmain_window_ui.Ui_MainWindowに委譲し、
このファイルは「どのイベントで何をするか」に専念する。
"""

import os
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from clip_range import format_clip_time, parse_clip_time, resolve_clip_range
from format_engine import (
    compute_simple_format_note,
    mismatched_selected_formats,
    plan_high_resolution_confirmation,
    resolve_format_spec as resolve_format_spec_logic,
)
from formats import (
    FORMAT_COLUMN_ROLE,
    FORMAT_MISMATCH_ROLE,
    HIGH_RESOLUTION_CHECK_LABELS,
    describe_format_plain,
    format_columns,
    is_codec_container_mismatch,
)
from main_window_ui import IDLE_STATUS_TEXT, Ui_MainWindow
from paths import get_downloads_folder, get_ffmpeg_location, log_debug
from storyboard import StoryboardTile, select_storyboard_format, storyboard_tile_for_time
from widgets import ScrubPreviewPopup
from workers import DownloadWorker, FormatListWorker, StoryboardFragmentWorker


class MainWindow(Ui_MainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.worker: DownloadWorker | None = None
        self.format_worker: FormatListWorker | None = None
        self.last_output_dir: str | None = None
        self.info_ready = False
        self.available_formats: list = []
        self.video_duration: float | None = None
        self.storyboard_format: dict | None = None
        self._storyboard_cache: dict[str, QPixmap] = {}
        self._storyboard_workers: set[StoryboardFragmentWorker] = set()
        self._pending_storyboard_urls: set[str] = set()

        self._info_fetch_timer = QTimer(self)
        self._info_fetch_timer.setSingleShot(True)
        self._info_fetch_timer.setInterval(700)
        self._info_fetch_timer.timeout.connect(lambda: self.fetch_formats(auto=True))

        self.settings = QSettings("ytdlp-gui", "YTDownloaderGUI")
        default_out_dir = get_downloads_folder()
        saved_out_dir = self.settings.value("last_output_dir", default_out_dir, type=str)

        self.setup_ui(saved_out_dir)
        self._connect_signals()

        self.auto_paste_from_clipboard()
        self._sync_window_height()

    def _connect_signals(self) -> None:
        """setup_uiが生成したウィジェットのシグナルを、このクラスが持つハンドラへ接続する"""
        self.url_edit.textChanged.connect(self.on_url_changed)
        self.paste_btn.clicked.connect(self.paste_from_clipboard)
        self.format_combo.currentIndexChanged.connect(self.update_simple_format_note)
        self.detail_toggle_btn.toggled.connect(self.on_detail_toggled)
        self.video_format_combo.currentIndexChanged.connect(self.on_detail_selection_changed)
        self.audio_format_combo.currentIndexChanged.connect(self.on_detail_selection_changed)
        self.browse_btn.clicked.connect(self.browse_folder)
        self.download_btn.clicked.connect(self.start_download)
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.open_folder_btn.clicked.connect(self.open_output_folder)
        self.log_toggle_btn.toggled.connect(self.on_log_toggle)
        self.clip_toggle_btn.toggled.connect(self.on_clip_toggled)
        self.clip_range_slider.rangeChanged.connect(self.on_clip_slider_changed)
        self.clip_range_slider.previewRequested.connect(self.on_clip_preview_requested)
        self.clip_start_edit.textChanged.connect(self.on_clip_text_changed)
        self.clip_end_edit.textChanged.connect(self.on_clip_text_changed)

    def _sync_window_height(self) -> None:
        """現在表示中のウィジェットに合わせてウィンドウの高さだけを追従させる"""
        central = self.centralWidget()
        # setVisible直後はレイアウトの最小サイズキャッシュが古いままのことがあるため、
        # sizeHintを読む前に明示的に再計算させる
        central.layout().invalidate()
        central.layout().activate()
        chrome_height = self.height() - central.height()
        target_height = central.layout().sizeHint().height() + chrome_height
        # QMainWindowは一度大きくなった最小サイズを記憶したままになることがあるため、
        # 縮める前にリセットしてから目的の高さへ合わせる
        self.setMinimumSize(0, 0)
        self.resize(self.width(), target_height)

    def on_log_toggle(self, checked: bool) -> None:
        self.log_view.setVisible(checked)
        self.log_toggle_btn.setText("ログ ▴" if checked else "ログ ▾")
        self._sync_window_height()

    def on_clip_toggled(self, checked: bool) -> None:
        self.clip_toggle_btn.setText("▴" if checked else "▾")
        self.clip_container.setVisible(checked)
        self._sync_window_height()

    def on_url_changed(self, text: str) -> None:
        self._info_fetch_timer.stop()
        self.info_ready = False
        self.download_btn.setEnabled(False)
        stripped = text.strip()
        if stripped.startswith("http://") or stripped.startswith("https://"):
            self._info_fetch_timer.start()
        else:
            self.title_label.setText("")
            self.thumbnail_label.clear()
            self.status_label.setText(IDLE_STATUS_TEXT)

    def on_detail_toggled(self, checked: bool) -> None:
        self.detail_toggle_btn.setText("簡易設定 ▴" if checked else "詳細設定 ▾")
        self.simple_format_container.setVisible(not checked)
        # simple_format_container が非表示の間は代わりにスペーサーへ伸縮を持たせる
        self.format_row.setStretch(1, 1 if checked else 0)
        self.detail_container.setVisible(checked)
        self._sync_window_height()
        has_items = self.video_format_combo.count() > 0
        self.video_format_combo.setEnabled(checked and has_items)
        self.audio_format_combo.setEnabled(checked and has_items)
        self.on_detail_selection_changed()
        self.update_simple_format_note()

        url = self.url_edit.text().strip()
        is_fetching = self.format_worker is not None and self.format_worker.isRunning()
        if checked and url and not has_items and not is_fetching:
            self.fetch_formats(auto=False)

    def paste_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)

    def auto_paste_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text.startswith("http://") or text.startswith("https://"):
            self.url_edit.setText(text)

    def fetch_formats(self, auto: bool = False) -> None:
        url = self.url_edit.text().strip()
        if not url:
            return

        self.video_format_combo.clear()
        self.audio_format_combo.clear()
        self.video_format_combo.setEnabled(False)
        self.audio_format_combo.setEnabled(False)
        self.title_label.setText("")
        self.thumbnail_label.clear()
        self.info_ready = False
        self.available_formats = []
        self.video_duration = None
        self.storyboard_format = None
        self._storyboard_cache = {}
        self.clip_range_slider.setEnabled(False)
        self.clip_duration_label.setText("")
        self.simple_format_note_label.setText("")
        self.simple_format_note_label.setVisible(False)
        self.download_btn.setEnabled(False)
        self.status_label.setText("動画情報を取得中...")
        self.spinner.start()

        worker = FormatListWorker(url)
        self.format_worker = worker
        worker.finished_ok.connect(
            lambda formats, title, thumb, duration, w=worker: self.on_formats_fetched(
                formats, title, thumb, duration, w, auto
            )
        )
        worker.finished_error.connect(
            lambda message, w=worker: self.on_formats_error(message, w, auto)
        )
        worker.start()

    def on_formats_fetched(
        self,
        formats: list[dict],
        title: str,
        thumbnail_bytes: bytes,
        duration: float | None,
        worker: FormatListWorker,
        auto: bool = False,
    ) -> None:
        if worker is not self.format_worker:
            return

        self.video_format_combo.clear()
        self.audio_format_combo.clear()
        self.available_formats = formats
        self.video_duration = duration
        self.storyboard_format = select_storyboard_format(
            formats, ScrubPreviewPopup.PREVIEW_SIZE.width(), ScrubPreviewPopup.PREVIEW_SIZE.height()
        )
        self._update_clip_slider_range()

        self.video_format_combo.addItem("なし", userData=None)
        self.audio_format_combo.addItem("なし", userData=None)

        video_count = 0
        audio_count = 0
        # コンテナ/コーデックが一致しない非推奨フォーマットを一覧の下の方に追いやる(安定ソートなので
        # 元々の解像度順は各グループ内で保たれる)
        sorted_formats = sorted(formats, key=is_codec_container_mismatch)
        for fmt in sorted_formats:
            vcodec = fmt.get("vcodec", "none")
            acodec = fmt.get("acodec", "none")
            has_video = bool(vcodec and vcodec != "none")
            has_audio = bool(acodec and acodec != "none")

            if has_video:
                combo = self.video_format_combo
                video_count += 1
            elif has_audio:
                combo = self.audio_format_combo
                audio_count += 1
            else:
                continue

            combo.addItem(describe_format_plain(fmt), userData=fmt)
            row = combo.count() - 1
            combo.setItemData(row, format_columns(fmt), FORMAT_COLUMN_ROLE)
            combo.setItemData(row, is_codec_container_mismatch(fmt), FORMAT_MISMATCH_ROLE)

        self.video_format_combo.setEnabled(self.detail_toggle_btn.isChecked())
        self.audio_format_combo.setEnabled(self.detail_toggle_btn.isChecked())
        self.on_detail_selection_changed()
        self.update_simple_format_note()

        self.title_label.setText(title)
        if thumbnail_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(thumbnail_bytes):
                self.thumbnail_label.setPixmap(pixmap)

        self.status_label.setText(f"動画{video_count}件・音声{audio_count}件を検出しました")
        self.spinner.stop()
        self.info_ready = True
        self.download_btn.setEnabled(True)

    def on_formats_error(self, message: str, worker: FormatListWorker | None = None, auto: bool = False) -> None:
        if worker is not None and worker is not self.format_worker:
            return

        self.spinner.stop()
        if auto:
            self.status_label.setText("動画情報を取得できませんでした")
        else:
            self.status_label.setText("フォーマット取得に失敗しました")
            QMessageBox.critical(self, "フォーマット取得エラー", message)

    def _update_clip_slider_range(self) -> None:
        """動画の長さが判明した時点で、スライダーの範囲を0〜動画の長さに合わせる。
        長さが不明(ライブ配信等)な場合はスライダー自体を無効化する。"""
        duration = self.video_duration
        if not duration or duration <= 0:
            self.clip_range_slider.setEnabled(False)
            self.clip_duration_label.setText("")
            return

        total = int(duration)
        self.clip_range_slider.setEnabled(True)
        self.clip_range_slider.setRange(0, total)
        self.clip_range_slider.setValues(0, total)
        self.clip_duration_label.setText(f"動画の長さ: {format_clip_time(duration)}")

    def on_clip_slider_changed(self, low: int, high: int) -> None:
        """スライダー操作の結果をテキスト入力欄へ反映する。端まで動かした場合は
        「指定なし(先頭から/末尾まで)」を表す空文字列にする"""
        total = int(self.video_duration) if self.video_duration else 0
        self.clip_start_edit.blockSignals(True)
        self.clip_end_edit.blockSignals(True)
        self.clip_start_edit.setText("" if low <= 0 else format_clip_time(low))
        self.clip_end_edit.setText("" if high >= total else format_clip_time(high))
        self.clip_start_edit.blockSignals(False)
        self.clip_end_edit.blockSignals(False)

    def on_clip_text_changed(self, *_: Any) -> None:
        """テキスト入力欄の内容をスライダーへ反映する。解析できない入力(入力途中を含む)は
        無視し、スライダーの表示は直前の値のまま保つ"""
        if not self.video_duration or self.video_duration <= 0:
            return

        total = int(self.video_duration)
        current_low, current_high = self.clip_range_slider.values()

        try:
            start = parse_clip_time(self.clip_start_edit.text())
            low = 0 if start is None else int(start)
        except ValueError:
            low = current_low  # 入力途中など解析できない間はスライダーを動かさない

        try:
            end = parse_clip_time(self.clip_end_edit.text())
            high = total if end is None else int(end)
        except ValueError:
            high = current_high

        self.clip_range_slider.setValues(low, high)

    def on_clip_preview_requested(self, which: str, value: int) -> None:
        """スライダードラッグ中、そのハンドルが指す時刻のサムネイル(ストーリーボード)を
        取得してポップアップへ反映する。取得できるまで/できない場合は数字のみ表示される"""
        if not self.storyboard_format or not self.video_duration:
            return

        tile = storyboard_tile_for_time(self.storyboard_format, self.video_duration, value)
        if tile is None:
            return

        cached = self._storyboard_cache.get(tile.fragment_url)
        if cached is not None:
            self._apply_storyboard_tile(which, cached, tile)
            return

        if tile.fragment_url in self._pending_storyboard_urls:
            return

        self._pending_storyboard_urls.add(tile.fragment_url)
        worker = StoryboardFragmentWorker(tile.fragment_url)
        self._storyboard_workers.add(worker)
        worker.finished_ok.connect(
            lambda data, w=worker: self._on_storyboard_fragment_fetched(w, data, which)
        )
        worker.finished_error.connect(lambda message, w=worker: self._on_storyboard_fragment_failed(w))
        worker.start()

    def _apply_storyboard_tile(self, which: str, sprite: QPixmap, tile: StoryboardTile) -> None:
        cropped = sprite.copy(tile.x, tile.y, tile.width, tile.height)
        self.clip_range_slider.set_preview_pixmap(which, cropped)

    def _on_storyboard_fragment_fetched(self, worker: StoryboardFragmentWorker, data: bytes, which: str) -> None:
        self._storyboard_workers.discard(worker)
        self._pending_storyboard_urls.discard(worker.url)

        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self._storyboard_cache[worker.url] = pixmap

        # 取得完了までの間にドラッグが進んでいる可能性があるため、リクエスト時点の値ではなく
        # 現在のスライダー値で切り出し位置を求め直す。既にドラッグが終わっている/別のハンドルに
        # 移っている、あるいは別のフラグメントが必要になっている場合は反映しない
        if self.storyboard_format is None or not self.video_duration:
            return
        if self.clip_range_slider.active_handle != which:
            return
        low, high = self.clip_range_slider.values()
        current_value = low if which == "low" else high
        tile = storyboard_tile_for_time(self.storyboard_format, self.video_duration, current_value)
        if tile is None or tile.fragment_url != worker.url:
            return
        self._apply_storyboard_tile(which, pixmap, tile)

    def _on_storyboard_fragment_failed(self, worker: StoryboardFragmentWorker) -> None:
        self._storyboard_workers.discard(worker)
        self._pending_storyboard_urls.discard(worker.url)

    def on_detail_selection_changed(self, *_: Any) -> None:
        if not self.detail_toggle_btn.isChecked():
            self.mp3_checkbox.setEnabled(False)
            self.mp3_label.setEnabled(False)
            self.merge_note_label.setText("")
            return

        video_fmt = self.video_format_combo.currentData()
        audio_fmt = self.audio_format_combo.currentData()

        mp3_enabled = video_fmt is None and audio_fmt is not None
        self.mp3_checkbox.setEnabled(mp3_enabled)
        self.mp3_label.setEnabled(mp3_enabled)

        if video_fmt is not None and audio_fmt is not None:
            self.merge_note_label.setText("動画と音声を合成してダウンロードします")
        elif video_fmt is not None:
            self.merge_note_label.setText("動画のみダウンロードします(音声なし)")
        elif audio_fmt is not None:
            self.merge_note_label.setText("音声のみダウンロードします")
        else:
            self.merge_note_label.setText("")

    def update_simple_format_note(self, *_: Any) -> None:
        """簡易設定の「動画 (最高画質 mp4)」がH.264限定のため本来の最高画質より
        解像度が落ちる場合のみ、非モーダルな注記で分かるようにする。
        注記がない間はラベル自体を隠し、空欄による不自然な余白が残らないようにする"""
        note = self._compute_simple_format_note()
        self.simple_format_note_label.setText(note)
        self.simple_format_note_label.setVisible(bool(note))
        self._sync_window_height()

    def _compute_simple_format_note(self) -> str:
        if self.detail_toggle_btn.isChecked():
            return ""
        return compute_simple_format_note(self.available_formats, self.format_combo.currentText())

    def browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダを選択", self.out_edit.text())
        if folder:
            self.out_edit.setText(folder)
            self.settings.setValue("last_output_dir", folder)

    def find_open_explorer_window(self, path: str) -> Any | None:
        """指定フォルダを既に開いているエクスプローラーウィンドウがあれば返す"""
        normalized = os.path.normcase(os.path.normpath(path))
        try:
            import win32com.client

            shell = win32com.client.Dispatch("Shell.Application")
            for window in shell.Windows():
                try:
                    folder_path = window.Document.Folder.Self.Path
                except Exception:
                    # 制御パネル等、フォルダを持たないシェルウィンドウもあるため無視して次へ
                    continue
                if os.path.normcase(os.path.normpath(folder_path)) == normalized:
                    return window
        except Exception as e:
            log_debug(f"find_open_explorer_window: シェルウィンドウの列挙に失敗 ({e!r})")
            return None
        return None

    def open_output_folder(self) -> None:
        if not (self.last_output_dir and os.path.isdir(self.last_output_dir)):
            return

        window = self.find_open_explorer_window(self.last_output_dir)
        if window is not None:
            try:
                import win32gui

                window.Visible = True
                win32gui.SetForegroundWindow(window.HWND)
            except Exception as e:
                log_debug(f"open_output_folder: 既存ウィンドウの前面化に失敗 ({e!r})")
            return

        os.startfile(self.last_output_dir)

    def set_inputs_enabled(self, enabled: bool) -> None:
        for widget in self.input_widgets:
            widget.setEnabled(enabled)
        if enabled:
            self.on_detail_toggled(self.detail_toggle_btn.isChecked())
            # video_format_combo等と同様、動画の長さが判明していない間は無効のままにしたいため、
            # 一括enable後に補正する(値は変更せず有効/無効のみ再判定する)
            self.clip_range_slider.setEnabled(bool(self.video_duration and self.video_duration > 0))

    def append_log(self, msg: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {msg}")

    def resolve_format_spec(self) -> tuple[str, list, list | None]:
        detail_mode = self.detail_toggle_btn.isChecked()
        return resolve_format_spec_logic(
            detail_mode,
            self.video_format_combo.currentData() if detail_mode else None,
            self.audio_format_combo.currentData() if detail_mode else None,
            self.mp3_checkbox.isChecked(),
            self.format_combo.currentText(),
        )

    def confirm_high_resolution_download(
        self, format_label: str, format_spec: str, format_sort: list | None
    ) -> tuple[str | None, str | None]:
        """簡易設定の最高画質が1920x1080を超える場合に確認する。
        戻り値: (選択, 1080p選択時の代替format_spec)
        選択は "best"(最高画質のまま) / "1080p"(1080pに制限) / None(キャンセル)"""
        plan = plan_high_resolution_confirmation(self.available_formats, format_label, format_spec, format_sort)
        if not plan.needs_confirmation:
            return "best", None

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("高解像度の動画です")
        box.setText(plan.message)
        best_btn = box.addButton("最高画質でダウンロード", QMessageBox.ButtonRole.AcceptRole)
        p1080_btn = (
            box.addButton("1080pでダウンロード", QMessageBox.ButtonRole.ActionRole)
            if plan.has_fallback
            else None
        )
        box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(p1080_btn if p1080_btn is not None else best_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is best_btn:
            return "best", None
        if clicked is p1080_btn:
            return "1080p", plan.fallback_spec
        return None, None

    def start_download(self) -> None:
        url = self.url_edit.text().strip()
        out_dir = self.out_edit.text().strip()

        if not url:
            QMessageBox.warning(self, "入力エラー", "URLを入力してください")
            return
        if not out_dir:
            QMessageBox.warning(self, "入力エラー", "保存先フォルダを指定してください")
            return
        if get_ffmpeg_location() is None:
            QMessageBox.critical(
                self,
                "ffmpegが見つかりません",
                "ffmpegが見つかりません。アプリの ffmpeg\\ffmpeg.exe を配置するか、"
                "システムにffmpegをインストールしてPATHを通してください。",
            )
            return

        try:
            format_spec, postprocessors, format_sort = self.resolve_format_spec()
        except ValueError as e:
            QMessageBox.warning(self, "入力エラー", str(e))
            return

        try:
            clip_start, clip_end = resolve_clip_range(self.clip_start_edit.text(), self.clip_end_edit.text())
        except ValueError as e:
            QMessageBox.warning(self, "入力エラー", str(e))
            return

        if self.detail_toggle_btn.isChecked():
            mismatched_fmts = mismatched_selected_formats(
                self.video_format_combo.currentData(), self.audio_format_combo.currentData()
            )
            if mismatched_fmts:
                ids = ", ".join(f"[{fmt.get('format_id')}]" for fmt in mismatched_fmts)
                reply = QMessageBox.question(
                    self,
                    "非推奨フォーマットの選択",
                    f"選択中のフォーマット({ids})はコンテナとコーデックが一致しない非推奨のものです。"
                    "再生環境によっては正しく再生できない場合があります。\n\nこのままダウンロードしますか?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        if not self.detail_toggle_btn.isChecked():
            format_label = self.format_combo.currentText()
            if format_label in HIGH_RESOLUTION_CHECK_LABELS:
                choice, fallback_spec = self.confirm_high_resolution_download(format_label, format_spec, format_sort)
                if choice is None:
                    return
                if choice == "1080p":
                    format_spec = fallback_spec

        os.makedirs(out_dir, exist_ok=True)
        self.last_output_dir = out_dir
        self.settings.setValue("last_output_dir", out_dir)

        self.set_inputs_enabled(False)
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.open_folder_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("ダウンロード中...")
        self.spinner.start()

        self.worker = DownloadWorker(
            url, out_dir, format_spec, postprocessors, format_sort,
            exclude_mismatched=not self.detail_toggle_btn.isChecked(),
            start_time=clip_start, end_time=clip_end,
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.log.connect(self.append_log)
        self.worker.finished_ok.connect(self.on_finished_ok)
        self.worker.finished_error.connect(self.on_finished_error)
        self.worker.start()

    def cancel_download(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.status_label.setText("キャンセル中...")

    def on_progress(self, percent: float, text: str) -> None:
        self.progress_bar.setValue(int(percent))
        self.status_label.setText(text)

    def on_finished_ok(self) -> None:
        self.spinner.stop()
        self.url_edit.clear()
        self.clip_start_edit.clear()
        self.clip_end_edit.clear()
        self.video_format_combo.clear()
        self.audio_format_combo.clear()
        self.available_formats = []
        self.video_duration = None
        self.storyboard_format = None
        self._storyboard_cache = {}
        self.clip_duration_label.setText("")
        self.simple_format_note_label.setText("")
        self.simple_format_note_label.setVisible(False)
        self.set_inputs_enabled(True)
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(True)
        self.progress_bar.reset()
        self.status_label.setText("完了")
        self.open_output_folder()

    def on_finished_error(self, message: str) -> None:
        self.spinner.stop()
        self.status_label.setText("エラーまたはキャンセル")
        self.set_inputs_enabled(True)
        self.download_btn.setEnabled(self.info_ready)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.reset()
        QMessageBox.critical(self, "ダウンロード失敗", message)
