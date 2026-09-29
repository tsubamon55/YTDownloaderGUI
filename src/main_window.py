"""メインウィンドウ(状態管理とイベントハンドリング)

ウィジェットの生成・レイアウトはmain_window_ui.Ui_MainWindowに委譲し、
このファイルは「どのイベントで何をするか」に専念する。
"""

import os
import sys
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QEvent, QObject, QSettings, Qt, QThread, QTimer
from PyQt6.QtGui import QCloseEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QFileDialog, QLineEdit, QMessageBox, QProgressDialog

from clip_range import (
    auto_format_clip_input,
    format_clip_time,
    parse_clip_time,
    resolve_clip_range,
)
from config import CONFIG
from folder_opener import open_folder
from format_engine import (
    FormatSelection,
    compute_auto_format_note,
    mismatched_selected_formats,
    plan_high_resolution_confirmation,
    resolve_format_spec,
)
from formats import (
    FORMAT_COLUMN_ROLE,
    FORMAT_MISMATCH_ROLE,
    Format,
    describe_format_plain,
    find_format_option,
    format_columns,
    has_audio,
    has_video,
    is_codec_container_mismatch,
)
from main_window_ui import IDLE_STATUS_TEXT, Ui_MainWindow, toggle_button_text
from paths import FFMPEG_EXECUTABLE_NAME, get_downloads_folder, get_ffmpeg_location, log_debug
from storyboard import StoryboardTile, select_storyboard_format, storyboard_tile_for_time
from updater import UpdateCheckWorker, UpdateDownloadWorker, apply_downloaded_update, download_dir
from widgets import HandleName, ScrubPreviewPopup
from workers import DownloadRequest, DownloadWorker, FormatListWorker, StoryboardFragmentWorker


def _looks_like_url(text: str) -> bool:
    """自動で動画情報を取得しにいく対象か(http/httpsで始まるか)"""
    return text.strip().startswith(("http://", "https://"))


class MainWindow(Ui_MainWindow):
    # 終了時にワーカースレッドの終了を待つ上限(ミリ秒)。cancel()が効くのは進捗フックの
    # 区切りごとなので、ffmpegの結合・切り抜きの最中は即座には止まらない
    _WORKER_SHUTDOWN_WAIT_MS = 10000
    # ウィンドウの初回表示より前にアップデートのダイアログが割り込まないよう、表示後まで遅らせる時間
    _UPDATE_CHECK_DELAY_MS = 1000

    def __init__(self) -> None:
        super().__init__()

        self.download_worker: DownloadWorker | None = None
        self.format_worker: FormatListWorker | None = None
        self.update_check_worker: UpdateCheckWorker | None = None
        self.update_download_worker: UpdateDownloadWorker | None = None
        # ダウンロード済みで、アプリ終了時(closeEvent)に適用するアップデートのパス
        self._pending_update_path: str | None = None
        self.last_output_dir: str | None = None
        self.info_ready = False
        self.available_formats: list[Format] = []
        self.video_duration: float | None = None
        self.storyboard_format: Format | None = None
        self._storyboard_cache: dict[str, QPixmap] = {}
        self._storyboard_workers: set[StoryboardFragmentWorker] = set()
        self._pending_storyboard_urls: set[str] = set()

        self._info_fetch_timer = QTimer(self)
        self._info_fetch_timer.setSingleShot(True)
        self._info_fetch_timer.setInterval(CONFIG.info_fetch_debounce_ms)
        self._info_fetch_timer.timeout.connect(lambda: self.fetch_formats(auto=True))

        self.settings = QSettings("ytdlp-gui", "YTDownloaderGUI")
        default_out_dir = get_downloads_folder()
        saved_out_dir = self.settings.value("last_output_dir", default_out_dir, type=str)

        self.setup_ui(saved_out_dir)
        self._connect_signals()
        # 入力欄をクリックした後、ラベルや背景などフォーカスを持たない場所をクリックしても
        # カーソル/フォーカス枠が残り続けるため、アプリ全体のクリックを監視して解除する
        QApplication.instance().installEventFilter(self)

        self.auto_paste_from_clipboard()
        self._sync_window_height()

        # ソースから直接実行している開発中は、置き換えるインストーラー/appが存在せず
        # アップデートを適用できないため、ビルド済み実行ファイルの場合のみ確認する
        if getattr(sys, "frozen", False) and CONFIG.auto_update_enabled:
            QTimer.singleShot(self._UPDATE_CHECK_DELAY_MS, self._check_for_updates)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            focus_widget = QApplication.focusWidget()
            if isinstance(focus_widget, QLineEdit) and focus_widget.window() is self:
                clicked_widget = QApplication.widgetAt(event.globalPosition().toPoint())
                if clicked_widget is not focus_widget:
                    focus_widget.clearFocus()
        return super().eventFilter(obj, event)

    def _connect_signals(self) -> None:
        """setup_uiが生成したウィジェットのシグナルを、このクラスが持つハンドラへ接続する"""
        self.url_edit.textChanged.connect(self.on_url_changed)
        self.paste_btn.clicked.connect(self.paste_from_clipboard)
        self.format_combo.currentIndexChanged.connect(self.update_auto_format_note)
        self.manual_toggle_btn.toggled.connect(self.on_manual_toggled)
        self.video_format_combo.currentIndexChanged.connect(self.on_manual_selection_changed)
        self.audio_format_combo.currentIndexChanged.connect(self.on_manual_selection_changed)
        self.browse_btn.clicked.connect(self.browse_folder)
        self.download_btn.clicked.connect(self.start_download)
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.open_folder_btn.clicked.connect(self.open_output_folder)
        self.log_toggle_btn.toggled.connect(self.on_log_toggle)
        self.detail_toggle_btn.toggled.connect(self.on_detail_toggled)
        self.clip_range_slider.rangeChanged.connect(self.on_clip_slider_changed)
        self.clip_range_slider.previewRequested.connect(self.on_clip_preview_requested)
        self.clip_start_edit.textChanged.connect(self.on_clip_text_changed)
        self.clip_end_edit.textChanged.connect(self.on_clip_text_changed)
        self.clip_start_edit.textEdited.connect(
            lambda text: self.on_clip_text_edited(self.clip_start_edit, text)
        )
        self.clip_end_edit.textEdited.connect(
            lambda text: self.on_clip_text_edited(self.clip_end_edit, text)
        )

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
        self.log_toggle_btn.setText(toggle_button_text("ログ", checked))
        self._sync_window_height()

    def on_detail_toggled(self, checked: bool) -> None:
        self.detail_toggle_btn.setText(toggle_button_text("詳細設定", checked))
        self.detail_container.setVisible(checked)
        self._sync_window_height()

    def on_url_changed(self, text: str) -> None:
        self._info_fetch_timer.stop()
        # URLが削除・変更・別のものに貼り替えられた場合、直前の動画に対する選択
        # (フォーマット・mp3変換・クリップ範囲・進捗バー等)が次の動画にそのまま
        # 引き継がれてしまわないよう、都度すべての入力内容をリセットする
        self._clear_video_info()
        self.progress_bar.reset()
        self.status_label.setText(IDLE_STATUS_TEXT)

        if _looks_like_url(text):
            self._info_fetch_timer.start()

    def _clear_video_info(self) -> None:
        """表示中の動画の情報(タイトル・サムネイル・フォーマット・クリップ範囲)を破棄し、
        ダウンロードできない状態に戻す"""
        self.info_ready = False
        self.download_btn.setEnabled(False)
        self.title_label.setText("")
        self.thumbnail_label.clear()
        self._reset_format_state()
        self._reset_video_state()

    def _reset_format_state(self) -> None:
        """動画/音声フォーマットの選択・mp3変換・関連の注記表示を初期化する。
        古い動画で選んだフォーマットやmp3変換の要否が次の動画に引き継がれるのを
        防ぐため、URLが変わった時・再取得を始める前に必ず呼ぶ"""
        self.video_format_combo.clear()
        self.audio_format_combo.clear()
        self.video_format_combo.setEnabled(False)
        self.audio_format_combo.setEnabled(False)
        self.available_formats = []
        self.mp3_checkbox.setChecked(False)
        self.mp3_checkbox.setEnabled(False)
        self.mp3_label.setEnabled(False)
        self.merge_note_label.setText("")
        self.auto_format_note_label.setText("")
        self.auto_format_note_label.setVisible(False)

    def _reset_video_state(self) -> None:
        """動画の長さ・ストーリーボード関連の状態を初期化し、クリップ範囲の入力を
        無効化・クリアする。URLが空/不正になった時、および新たな取得を始める前に呼ぶ。
        古い動画のクリップ範囲(秒数)が新しい動画にそのまま持ち込まれてしまう
        (長さを超えた範囲を指定してしまう)のを防ぐため、テキストも必ずクリアする"""
        self.video_duration = None
        self.storyboard_format = None
        self._storyboard_cache = {}
        self.detail_container.setEnabled(False)
        # 古い動画の長さに基づいた範囲(0〜635等)がハンドル位置に残ったままにならないよう、
        # スライダー自体もコンストラクタ相当の初期状態(全区間選択)に戻す
        self.clip_range_slider.setRange(0, 100)
        self.clip_range_slider.setValues(0, 100)
        self.clip_duration_label.setText("")
        self.clip_end_edit.setPlaceholderText("")
        self.clip_start_edit.clear()
        self.clip_end_edit.clear()

    def on_manual_toggled(self, checked: bool) -> None:
        self.manual_toggle_btn.setText("自動設定 ▴" if checked else "手動設定 ▾")
        self.auto_format_container.setVisible(not checked)
        # auto_format_container が非表示の間は代わりにスペーサーへ伸縮を持たせる
        self.format_row.setStretch(1, 1 if checked else 0)
        self.manual_container.setVisible(checked)
        self._sync_window_height()
        has_items = self.video_format_combo.count() > 0
        self.video_format_combo.setEnabled(checked and has_items)
        self.audio_format_combo.setEnabled(checked and has_items)
        self.on_manual_selection_changed()
        self.update_auto_format_note()

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
        if _looks_like_url(text):
            self.url_edit.setText(text)

    def fetch_formats(self, auto: bool = False) -> None:
        url = self.url_edit.text().strip()
        if not url:
            return

        self._clear_video_info()
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

        self.available_formats = formats
        self.video_duration = duration
        self.storyboard_format = select_storyboard_format(
            formats, ScrubPreviewPopup.PREVIEW_SIZE.width(), ScrubPreviewPopup.PREVIEW_SIZE.height()
        )
        self._update_clip_slider_range()
        video_count, audio_count = self._populate_format_combos(formats)

        self.video_format_combo.setEnabled(self.manual_toggle_btn.isChecked())
        self.audio_format_combo.setEnabled(self.manual_toggle_btn.isChecked())
        self.on_manual_selection_changed()
        self.update_auto_format_note()

        self.title_label.setText(title)
        if thumbnail_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(thumbnail_bytes):
                self.thumbnail_label.setPixmap(self._fit_thumbnail_pixmap(pixmap))

        self.status_label.setText(f"動画{video_count}件・音声{audio_count}件を検出しました")
        self.spinner.stop()
        self.info_ready = True
        self.download_btn.setEnabled(True)

    def _populate_format_combos(self, formats: list[Format]) -> tuple[int, int]:
        """手動設定の動画/音声コンボにフォーマット一覧を流し込み、(動画件数, 音声件数)を返す"""
        self.video_format_combo.clear()
        self.audio_format_combo.clear()
        self.video_format_combo.addItem("なし", userData=None)
        self.audio_format_combo.addItem("なし", userData=None)

        video_count = 0
        audio_count = 0
        # コンテナ/コーデックが一致しない非推奨フォーマットを一覧の下の方に追いやる(安定ソートなので
        # 元々の解像度順は各グループ内で保たれる)
        for fmt in sorted(formats, key=is_codec_container_mismatch):
            if has_video(fmt):
                combo = self.video_format_combo
                video_count += 1
            elif has_audio(fmt):
                combo = self.audio_format_combo
                audio_count += 1
            else:
                continue

            combo.addItem(describe_format_plain(fmt), userData=fmt)
            row = combo.count() - 1
            combo.setItemData(row, format_columns(fmt), FORMAT_COLUMN_ROLE)
            combo.setItemData(row, is_codec_container_mismatch(fmt), FORMAT_MISMATCH_ROLE)
        return video_count, audio_count

    def _fit_thumbnail_pixmap(self, pixmap: QPixmap) -> QPixmap:
        """thumbnail_labelの表示枠に合わせて、アスペクト比を保ったまま
        スムーズに縮小し、はみ出た部分を中央基準で切り出す(単純な引き伸ばし
        によるぼやけ・歪みを避けるため)"""
        target_size = self.thumbnail_label.size()
        scaled = pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - target_size.width()) // 2)
        y = max(0, (scaled.height() - target_size.height()) // 2)
        return scaled.copy(x, y, target_size.width(), target_size.height())

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
        長さが不明(ライブ配信等)な場合はクリップ範囲の入力全体を無効化する。"""
        duration = self.video_duration
        if not duration or duration <= 0:
            self.detail_container.setEnabled(False)
            self.clip_duration_label.setText("")
            self.clip_end_edit.setPlaceholderText("")
            return

        total = int(duration)
        self.detail_container.setEnabled(True)
        self.clip_range_slider.setRange(0, total)
        self.clip_range_slider.setValues(0, total)
        self.clip_duration_label.setText(f"動画の長さ: {format_clip_time(duration)}")
        # 空欄時は「末尾(動画の長さ)まで」が実際のデフォルト動作なので、それをそのまま表示する
        self.clip_end_edit.setPlaceholderText(format_clip_time(duration))

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

    def on_clip_text_edited(self, edit: QLineEdit, text: str) -> None:
        """ユーザーが切り抜き範囲欄に数字を入力した際、右詰め(ストップウォッチ入力)方式で
        コロンを自動的に振り直す。(プログラム側からのsetText、例えばスライダー操作の反映では
        発火しないシグナルなので、ユーザーの手入力のみを対象にできる)"""
        formatted = auto_format_clip_input(text)
        if formatted != text:
            edit.setText(formatted)
            edit.setCursorPosition(len(formatted))

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

    def on_clip_preview_requested(self, which: HandleName, value: int) -> None:
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

    def _apply_storyboard_tile(self, which: HandleName, sprite: QPixmap, tile: StoryboardTile) -> None:
        cropped = sprite.copy(tile.x, tile.y, tile.width, tile.height)
        self.clip_range_slider.set_preview_pixmap(which, cropped)

    def _on_storyboard_fragment_fetched(
        self, worker: StoryboardFragmentWorker, data: bytes, which: HandleName
    ) -> None:
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

    def on_manual_selection_changed(self, *_: Any) -> None:
        if not self.manual_toggle_btn.isChecked():
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

    def update_auto_format_note(self, *_: Any) -> None:
        """自動設定の「動画 (最高画質 mp4)」がH.264限定のため本来の最高画質より
        解像度が落ちる場合のみ、非モーダルな注記で分かるようにする。
        注記がない間はラベル自体を隠し、空欄による不自然な余白が残らないようにする"""
        note = self._compute_auto_format_note()
        self.auto_format_note_label.setText(note)
        self.auto_format_note_label.setVisible(bool(note))
        self._sync_window_height()

    def _compute_auto_format_note(self) -> str:
        if self.manual_toggle_btn.isChecked():
            return ""
        return compute_auto_format_note(self.available_formats, self.format_combo.currentText())

    def browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダを選択", self.out_edit.text())
        if folder:
            self.out_edit.setText(folder)
            self.settings.setValue("last_output_dir", folder)

    def open_output_folder(self) -> None:
        if self.last_output_dir and os.path.isdir(self.last_output_dir):
            open_folder(self.last_output_dir)

    def _check_for_updates(self) -> None:
        worker = UpdateCheckWorker()
        self.update_check_worker = worker
        worker.update_available.connect(self.on_update_available)
        worker.up_to_date.connect(lambda w=worker: self._on_update_check_settled(w))
        worker.check_failed.connect(lambda message, w=worker: self._on_update_check_failed(message, w))
        worker.start()

    def _on_update_check_settled(self, worker: UpdateCheckWorker) -> None:
        if worker is self.update_check_worker:
            self.update_check_worker = None

    def _on_update_check_failed(self, message: str, worker: UpdateCheckWorker) -> None:
        # バックグラウンドの自動確認なので、ネットワーク不調等で失敗してもユーザーには
        # 見せない(対処法を提示できない不安を与えるだけのため)。ログにのみ残す
        log_debug(f"_check_for_updates: アップデート確認に失敗しました ({message})")
        self._on_update_check_settled(worker)

    def on_update_available(self, version: str, download_url: str, asset_name: str) -> None:
        if not download_url or not asset_name:
            return
        if self.download_worker is not None and self.download_worker.isRunning():
            # 更新の適用にはアプリの終了が必要なため、動画のダウンロード中には提案しない
            # (次回起動時に改めて確認される)
            log_debug(f"on_update_available: ダウンロード中のため {version} への更新提案を見送りました")
            return

        reply = QMessageBox.question(
            self,
            "アップデートがあります",
            f"新しいバージョン {version} が利用可能です。今すぐダウンロードしてインストールしますか?\n"
            "(インストール後、アプリは自動的に再起動します)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_update_download(download_url, asset_name)

    def _start_update_download(self, download_url: str, asset_name: str) -> None:
        dest_path = os.path.join(download_dir(), asset_name)
        worker = UpdateDownloadWorker(download_url, dest_path)
        self.update_download_worker = worker

        progress = QProgressDialog("アップデートをダウンロード中...", "キャンセル", 0, 100, self)
        progress.setWindowTitle("アップデート")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)

        worker.progress.connect(lambda percent, p=progress: p.setValue(int(percent)))
        progress.canceled.connect(worker.cancel)
        worker.finished_ok.connect(
            lambda path, w=worker, p=progress: self.on_update_download_finished(path, w, p)
        )
        worker.finished_error.connect(
            lambda message, w=worker, p=progress: self.on_update_download_error(message, w, p)
        )
        worker.cancelled.connect(
            lambda w=worker, p=progress: self.on_update_download_cancelled(w, p)
        )
        worker.start()
        progress.show()

    def on_update_download_finished(
        self, local_path: str, worker: UpdateDownloadWorker, progress: QProgressDialog
    ) -> None:
        if worker is not self.update_download_worker:
            return
        self.update_download_worker = None
        # finished_okの送出直後はまだrun()から戻る途中のため、参照を手放す前に終了を待つ
        worker.wait()

        # インストーラーの展開・ファイルコピーには数十秒かかることがあり、その間
        # ウィンドウは閉じたまま何も表示されない。ここでダイアログを閉じずに
        # 「適用中」表示へ切り替えておくことで、ウィンドウが消える直前まで
        # 進行中であることが伝わるようにする(そのままcloseEvent完了時に一緒に破棄される)
        progress.setLabelText("アップデートを適用しています。しばらくすると自動的に再起動します...")
        progress.setRange(0, 0)
        progress.setCancelButton(None)
        # 直後のcloseEvent側の処理まで描画が持ち越されると、上の表示切り替えが一度も
        # 画面に出ないまま次の処理に埋もれてしまうことがあるため、ここで明示的に
        # 再描画させておく。QApplication.processEvents()は無関係な保留中イベント
        # (他のタイマー・別スレッドからのシグナル等)まで処理してしまい、この
        # 終了シーケンスの最中に意図しない処理が割り込む余地を作ってしまうため、
        # このダイアログ1つだけを対象にする repaint() を使う
        progress.repaint()

        # 適用はcloseEventで終了が確定してから行う。先にインストーラーを起動すると、
        # 終了確認で「いいえ」を選ばれた場合でも実行中のアプリがインストーラーに
        # 強制終了され、進行中の動画ダウンロードが壊れてしまうため
        self._pending_update_path = local_path
        self.close()

    def on_update_download_error(
        self, message: str, worker: UpdateDownloadWorker, progress: QProgressDialog
    ) -> None:
        if worker is not self.update_download_worker:
            return
        self.update_download_worker = None
        progress.close()
        QMessageBox.critical(self, "アップデートのダウンロードに失敗しました", message)

    def on_update_download_cancelled(self, worker: UpdateDownloadWorker, progress: QProgressDialog) -> None:
        if worker is not self.update_download_worker:
            return
        self.update_download_worker = None
        progress.close()

    def set_inputs_enabled(self, enabled: bool) -> None:
        for widget in self.input_widgets:
            widget.setEnabled(enabled)
        if enabled:
            self.on_manual_toggled(self.manual_toggle_btn.isChecked())
            # video_format_combo等と同様、動画の長さが判明していない間は無効のままにしたいため、
            # 一括enable後に補正する(値は変更せず有効/無効のみ再判定する)
            self.detail_container.setEnabled(bool(self.video_duration and self.video_duration > 0))

    def append_log(self, msg: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {msg}")

    def _current_format_selection(self) -> FormatSelection:
        manual_mode = self.manual_toggle_btn.isChecked()
        return resolve_format_spec(
            manual_mode,
            self.video_format_combo.currentData() if manual_mode else None,
            self.audio_format_combo.currentData() if manual_mode else None,
            self.mp3_checkbox.isChecked(),
            self.format_combo.currentText(),
        )

    def confirm_high_resolution_download(
        self, format_label: str, format_spec: str, format_sort: list[str] | None
    ) -> str | None:
        """自動設定の最高画質が1920x1080を超える場合に確認する。
        戻り値はダウンロードに使うformat_spec(1080pを選んだ場合はその代替)。キャンセル時はNone"""
        option = find_format_option(format_label)
        if option is None or not option.confirm_high_resolution:
            return format_spec
        plan = plan_high_resolution_confirmation(self.available_formats, format_label, format_spec, format_sort)
        if not plan.needs_confirmation:
            return format_spec

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
            return format_spec
        if p1080_btn is not None and clicked is p1080_btn:
            return plan.fallback_spec
        return None

    def start_download(self) -> None:
        request = self._build_download_request()
        if request is None or not self._prepare_output_dir(request.out_dir):
            return

        self._set_busy(True)
        self.download_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("ダウンロード中...")

        self.download_worker = DownloadWorker(request)
        self.download_worker.progress.connect(self.on_progress)
        self.download_worker.log.connect(self.append_log)
        self.download_worker.finished_ok.connect(self.on_finished_ok)
        self.download_worker.finished_error.connect(self.on_finished_error)
        self.download_worker.start()

    def _build_download_request(self) -> DownloadRequest | None:
        """入力内容を検証・確認してダウンロード要求を組み立てる。入力エラーや
        ユーザーのキャンセルの場合は、ダイアログを出した上でNoneを返す"""
        url = self.url_edit.text().strip()
        out_dir = self.out_edit.text().strip()
        if not self._check_required_inputs(url, out_dir):
            return None

        try:
            selection = self._current_format_selection()
            clip_start, clip_end = resolve_clip_range(self.clip_start_edit.text(), self.clip_end_edit.text())
            self._check_clip_within_duration(clip_start, clip_end)
        except ValueError as e:
            QMessageBox.warning(self, "入力エラー", str(e))
            return None

        format_spec = self._confirm_format_choice(selection)
        if format_spec is None:
            return None

        return DownloadRequest(
            url=url,
            out_dir=out_dir,
            format_spec=format_spec,
            postprocessors=selection.postprocessors,
            format_sort=selection.sort,
            exclude_mismatched=not self.manual_toggle_btn.isChecked(),
            clip_start=clip_start,
            clip_end=clip_end,
        )

    def _check_required_inputs(self, url: str, out_dir: str) -> bool:
        if not url:
            QMessageBox.warning(self, "入力エラー", "URLを入力してください")
            return False
        if not out_dir:
            QMessageBox.warning(self, "入力エラー", "保存先フォルダを指定してください")
            return False
        if get_ffmpeg_location() is None:
            QMessageBox.critical(
                self,
                "ffmpegが見つかりません",
                f"ffmpegが見つかりません。アプリの ffmpeg{os.sep}{FFMPEG_EXECUTABLE_NAME} を配置するか、"
                "システムにffmpegをインストールしてPATHを通してください。",
            )
            return False
        return True

    def _check_clip_within_duration(self, clip_start: float | None, clip_end: float | None) -> None:
        """resolve_clip_rangeは開始・終了の前後関係のみを見るため、動画の長さとの整合性は
        ここで確認する(範囲外ならValueError)。長さが不明(ライブ配信等)な場合はチェックできないためスキップする"""
        if not self.video_duration:
            return
        if clip_start is not None and clip_start >= self.video_duration:
            raise ValueError("開始時刻が動画の長さを超えています")
        if clip_end is not None and clip_end > self.video_duration:
            raise ValueError("終了時刻が動画の長さを超えています")

    def _confirm_format_choice(self, selection: FormatSelection) -> str | None:
        """選んだフォーマットに注意が必要な場合にユーザーへ確認し、使うformat_specを返す(中止ならNone)"""
        if self.manual_toggle_btn.isChecked():
            return selection.spec if self._confirm_mismatched_formats() else None
        return self.confirm_high_resolution_download(
            self.format_combo.currentText(), selection.spec, selection.sort
        )

    def _confirm_mismatched_formats(self) -> bool:
        """手動設定で非推奨フォーマットを選んでいる場合に続行してよいか確認する"""
        mismatched_fmts = mismatched_selected_formats(
            self.video_format_combo.currentData(), self.audio_format_combo.currentData()
        )
        if not mismatched_fmts:
            return True
        ids = ", ".join(f"[{fmt.get('format_id')}]" for fmt in mismatched_fmts)
        reply = QMessageBox.question(
            self,
            "非推奨フォーマットの選択",
            f"選択中のフォーマット({ids})はコンテナとコーデックが一致しない非推奨のものです。"
            "再生環境によっては正しく再生できない場合があります。\n\nこのままダウンロードしますか?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _prepare_output_dir(self, out_dir: str) -> bool:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            # 予約デバイス名(CON等)・禁止文字を含むパス・同名のファイルが既に存在する
            # パスなどを手入力した場合にここへ来る。他の入力ミスと同じ「入力エラー」で
            # 案内し、グローバルのexcepthookによる「予期しないエラー」に落とさない
            QMessageBox.warning(
                self, "入力エラー", f"保存先フォルダを作成できませんでした:\n{out_dir}\n\n{e}"
            )
            return False
        self.last_output_dir = out_dir
        self.settings.setValue("last_output_dir", out_dir)
        return True

    def _set_busy(self, busy: bool) -> None:
        """ダウンロード中/待機中で切り替わる入力欄・キャンセルボタン・スピナーをまとめて設定する"""
        self.set_inputs_enabled(not busy)
        self.cancel_btn.setEnabled(busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def cancel_download(self) -> None:
        if self.download_worker is not None:
            self.download_worker.cancel()
            self.status_label.setText("キャンセル中...")

    def closeEvent(self, event: QCloseEvent) -> None:
        """終了時に、走っているワーカースレッドを止めて終了を待つ。

        待たずに閉じるとQThreadがrun()(ネットワークダウンロード中やffmpegの切り抜き処理中)
        のままインタプリタ終了処理に入り、"QThread: Destroyed while thread is still running"
        を招く。yt-dlp/ffmpegが中途半端に打ち切られ、DownloadWorker側の後片付けも走らないため
        .part等の未完成ファイルが保存先に残ってしまう。
        """
        if self.download_worker is not None and self.download_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "ダウンロード中",
                "ダウンロードが進行中です。中止して終了しますか?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                if self._pending_update_path is not None:
                    self._pending_update_path = None
                    QMessageBox.information(
                        self, "アップデート", "アップデートを中止しました。次回起動時に改めて確認します。"
                    )
                event.ignore()
                return
            self.download_worker.cancel()
            self.status_label.setText("キャンセル中...")

        if self.update_download_worker is not None:
            self.update_download_worker.cancel()

        for worker in self._running_workers():
            # cancel()は進捗フック経由でしか効かず、後処理(ffmpegの結合・切り抜き)の最中は
            # 区切りが来るまで止まらないため、待ち時間には上限を設ける。時間切れの場合は
            # これ以上UIスレッドからできることがないので、記録だけ残して終了する
            if not worker.wait(self._WORKER_SHUTDOWN_WAIT_MS):
                log_debug(
                    f"closeEvent: {type(worker).__name__} が"
                    f"{self._WORKER_SHUTDOWN_WAIT_MS}ms以内に終了しませんでした"
                )

        if self._pending_update_path is not None:
            update_path = self._pending_update_path
            self._pending_update_path = None
            try:
                apply_downloaded_update(update_path)
            except Exception as e:
                # 元のアプリはそのまま残っているため、終了自体は続行して手動更新を案内する
                log_debug(f"closeEvent: アップデートの適用に失敗しました ({e!r})")
                QMessageBox.critical(
                    self,
                    "アップデートの適用に失敗しました",
                    f"アップデートを適用できませんでした。手動でダウンロード・インストールしてください。\n\n{e}",
                )
        event.accept()

    def _running_workers(self) -> list[QThread]:
        """終了待ちの対象となる、現在走っているワーカースレッドを列挙する"""
        candidates = [
            self.download_worker,
            self.format_worker,
            self.update_check_worker,
            self.update_download_worker,
            *self._storyboard_workers,
        ]
        return [w for w in candidates if w is not None and w.isRunning()]

    def on_progress(self, percent: float, text: str) -> None:
        self.progress_bar.setValue(int(percent))
        self.status_label.setText(text)

    def on_finished_ok(self) -> None:
        # url_edit.clear()がtextChangedを発火させ、on_url_changed内のリセット処理で
        # フォーマット選択・mp3変換・クリップ範囲・進捗バー等の入力内容が一括で初期化される
        self.url_edit.clear()
        self._set_busy(False)
        self.download_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(True)
        self.progress_bar.reset()
        self.status_label.setText("完了")
        self.open_output_folder()

    def on_finished_error(self, message: str) -> None:
        self.status_label.setText("エラーまたはキャンセル")
        self._set_busy(False)
        self.download_btn.setEnabled(self.info_ready)
        self.progress_bar.reset()
        QMessageBox.critical(self, "ダウンロード失敗", message)
