"""メインウィンドウ(状態管理とイベントハンドリング)

ウィジェットの生成・レイアウトはmain_window_ui.Ui_MainWindowに委譲し、
このファイルは「どのイベントで何をするか」に専念する。
"""

import os
import sys
import time
from datetime import datetime
from typing import Any, Literal

from PyQt6.QtCore import QEvent, QObject, QSettings, Qt, QThread, QTimer
from PyQt6.QtGui import QCloseEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QFileDialog, QLineEdit, QMessageBox, QProgressDialog

import dialogs
from clip_range import (
    auto_format_clip_input,
    format_clip_time,
    parse_clip_time,
    resolve_clip_range,
)
from config import CONFIG
from folder_opener import open_folder, reveal_file
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
from live import LiveInfo, LiveMode
from main_window_ui import (
    DOWNLOAD_BUTTON_TEXT,
    DOWNLOAD_PANEL_WIDTH,
    IDLE_STATUS_TEXT,
    RECORD_BUTTON_TEXT,
    THUMBNAIL_SIZE,
    Ui_MainWindow,
    toggle_button_text,
)
from paths import FFMPEG_EXECUTABLE_NAME, get_downloads_folder, get_ffmpeg_location, log_debug
from storyboard import StoryboardTile, select_storyboard_format, storyboard_tile_for_time
from updater import (
    UpdateCheckWorker,
    UpdateDownloadWorker,
    apply_downloaded_update,
    download_dir,
    get_current_version,
)
from widgets import DownloadTaskWidget, HandleName, ScrubPreviewPopup
from workers import DownloadRequest, DownloadWorker, FormatListWorker, StoryboardFragmentWorker


def _clipboard_text() -> str:
    clipboard = QApplication.clipboard()
    return clipboard.text().strip() if clipboard is not None else ""


def _looks_like_url(text: str) -> bool:
    """自動で動画情報を取得しにいく対象か(http/httpsで始まるか)"""
    return text.strip().startswith(("http://", "https://"))


class MainWindow(Ui_MainWindow):
    # 終了時にワーカースレッドの終了を待つ上限(ミリ秒)。cancel()が効くのは進捗フックの
    # 区切りごとなので、ffmpegの結合・切り抜きの最中は即座には止まらない
    _WORKER_SHUTDOWN_WAIT_MS = 10000
    # ウィンドウの初回表示より前にアップデートのダイアログが割り込まないよう、表示後まで遅らせる時間
    _UPDATE_CHECK_DELAY_MS = 1000
    # 別のダイアログを表示中にアップデートの提案が届いた場合、閉じられるまで提案を待たせる間隔
    _UPDATE_PROMPT_RETRY_MS = 2000
    # スライダーのドラッグ中に同時に取得するストーリーボード画像の上限。ドラッグで次々に
    # 別の画像が必要になっても、スレッドと通信を際限なく増やさない(超えた分は次の操作で取り直す)
    _MAX_STORYBOARD_WORKERS = 4

    def __init__(self) -> None:
        super().__init__()

        # 並列に走っているダウンロードと、画面右上の一覧でそれぞれを表示しているウィジェット
        self._download_tasks: dict[DownloadWorker, DownloadTaskWidget] = {}
        # 録画中のタスク。一覧の先頭側にまとめて並べ、通常のダウンロードはその下に積む
        self._recording_task_widgets: list[DownloadTaskWidget] = []
        # 結果を反映する対象の取得ワーカー。URL変更・再取得で差し替わる(古い結果は捨てる)
        self.format_worker: FormatListWorker | None = None
        # 差し替え済みも含め、まだ走っている取得ワーカー(終了時に待つ対象)
        self._format_workers: set[FormatListWorker] = set()
        self.update_check_worker: UpdateCheckWorker | None = None
        self.update_download_worker: UpdateDownloadWorker | None = None
        # ダウンロード済みで、アプリ終了時(closeEvent)に適用するアップデートのパスと、その期待ハッシュ値
        self._pending_update_path: str | None = None
        self._pending_update_sha256 = ""
        # 適用待ちの間に表示している「適用中」ダイアログ。終了が取り消された場合に閉じる
        self._update_progress_dialog: QProgressDialog | None = None
        # 終了の確認で「はい」を選んだ後か(録画の保存を待ってから閉じ直すときに、確認を出し直さないため)
        self._close_confirmed = False
        # 終了時に録画の保存を待っている間に出す「保存しています」ダイアログ
        self._saving_dialog: QProgressDialog | None = None
        self.info_ready = False
        self.available_formats: list[Format] = []
        self.video_duration: float | None = None
        # 表示中の動画が配信中のライブなら、その情報(さかのぼれるか等)。通常の動画ではNone
        self.live_info: LiveInfo | None = None
        self.storyboard_format: Format | None = None
        self._storyboard_cache: dict[str, QPixmap] = {}
        self._storyboard_workers: set[StoryboardFragmentWorker] = set()
        self._pending_storyboard_urls: set[str] = set()
        # 動画が切り替わるたびに増やす番号。取得中に動画が変わった古いストーリーボードの結果を
        # 新しい動画のキャッシュに混ぜないよう、取得開始時の番号と照らし合わせる
        self._storyboard_generation = 0
        # 手動設定を開く前のウィンドウ幅。手動設定はフォーマットの列幅の分だけウィンドウを広げるため、
        # 自動設定に戻したときにこの幅へ戻す
        self._width_before_manual: int | None = None

        self._info_fetch_timer = QTimer(self)
        self._info_fetch_timer.setSingleShot(True)
        self._info_fetch_timer.setInterval(CONFIG.info_fetch_debounce_ms)
        self._info_fetch_timer.timeout.connect(lambda: self.fetch_formats(auto=True))

        # ライブの「配信開始から」の経過時間を1秒ごとに更新する(ライブを表示している間だけ動かす)
        self._live_elapsed_timer = QTimer(self)
        self._live_elapsed_timer.setInterval(1000)
        self._live_elapsed_timer.timeout.connect(self._update_live_elapsed)

        # 他のダイアログの表示中に届いたアップデートの提案(version, download_url, asset_name, sha256)と、
        # それを閉じられた後に改めて出すためのタイマー
        self._deferred_update: tuple[str, str, str, str] | None = None
        self._update_prompt_timer = QTimer(self)
        self._update_prompt_timer.setSingleShot(True)
        self._update_prompt_timer.setInterval(self._UPDATE_PROMPT_RETRY_MS)
        self._update_prompt_timer.timeout.connect(self._offer_deferred_update)

        self.settings = QSettings("ytdlp-gui", "YTDownloaderGUI")
        default_out_dir = get_downloads_folder()
        saved_out_dir = self.settings.value("last_output_dir", default_out_dir, type=str)

        self.setup_ui(saved_out_dir, get_current_version())
        self._connect_signals()
        # 入力欄をクリックした後、ラベルや背景などフォーカスを持たない場所をクリックしても
        # カーソル/フォーカス枠が残り続けるため、アプリ全体のクリックを監視して解除する
        app = QApplication.instance()
        assert app is not None
        app.installEventFilter(self)

        self.auto_paste_from_clipboard()
        self._sync_window_height()

        # ソースから直接実行している開発中は、置き換えるインストーラー/appが存在せず
        # アップデートを適用できないため、ビルド済み実行ファイルの場合のみ確認する
        if getattr(sys, "frozen", False) and CONFIG.auto_update_enabled:
            QTimer.singleShot(self._UPDATE_CHECK_DELAY_MS, self._check_for_updates)

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        # アプリ全体の全イベントがここを通るため、対象外のイベントは種類の比較だけで素通りさせる
        # (子ウィジェットのどこをクリックしても拾う必要があり、フィルタ自体はアプリ全体に置く)
        if event is None or event.type() != QEvent.Type.MouseButtonPress:
            return super().eventFilter(obj, event)
        if isinstance(event, QMouseEvent):
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
        """現在表示中のウィジェットに合わせてウィンドウの高さを追従させる。

        伸縮できる中身はログ欄だけなので、ログ表示中にユーザーが広げた高さはそのまま残し
        (ログ欄が使う)、ログを隠している間は中身にぴったり合わせる(余白しか残らないため)"""
        central = self.centralWidget()
        assert central is not None
        layout = central.layout()
        assert layout is not None
        # setVisible直後はレイアウトの最小サイズキャッシュが古いままのことがあるため、
        # sizeHintを読む前に明示的に再計算させる
        layout.invalidate()
        layout.activate()
        chrome_height = self.height() - central.height()
        target_height = layout.sizeHint().height() + chrome_height
        if self.log_view.isVisible():
            target_height = max(target_height, self.height())
        # QMainWindowは一度大きくなった最小サイズを記憶したままになることがあるため、
        # 縮める前にリセットしてから目的の高さへ合わせる。0は「指定なし」の意味で、実際の
        # 最小サイズはレイアウトの最小サイズからQtが決める(ここで計算した値を明示的に
        # 設定すると、初回表示前はchrome_heightが実際と合わず、中身が重なるほど小さくなる)
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
        # 取得中だった前のURLの結果が、新しいURLの情報として反映されないよう無効にする
        self.format_worker = None
        self.spinner.stop()
        # URLが削除・変更・別のものに貼り替えられた場合、直前の動画に対する選択
        # (フォーマット・mp3変換・クリップ範囲等)が次の動画にそのまま
        # 引き継がれてしまわないよう、都度すべての入力内容をリセットする
        self._clear_video_info()
        self.status_label.setText(IDLE_STATUS_TEXT)

        if _looks_like_url(text):
            self._info_fetch_timer.start()

    def _clear_video_info(self) -> None:
        """表示中の動画の情報(タイトル・サムネイル・フォーマット・クリップ範囲)を破棄し、
        ダウンロードできない状態に戻す"""
        self.info_ready = False
        self._show_live_info(None)
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
        self._storyboard_generation += 1
        self._pending_storyboard_urls = set()
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
        if checked and self._width_before_manual is None:
            self._width_before_manual = self.width()
        self.manual_container.setVisible(checked)
        self._sync_window_height()
        if not checked and self._width_before_manual is not None:
            # 手動設定の列幅に合わせて広がった分を戻す(_sync_window_heightで最小サイズは解除済み)
            self.resize(self._width_before_manual, self.height())
            self._width_before_manual = None
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
        text = _clipboard_text()
        if text:
            self.url_edit.setText(text)

    def auto_paste_from_clipboard(self) -> None:
        text = _clipboard_text()
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
        self._format_workers.add(worker)
        worker.finished_ok.connect(
            lambda formats, title, thumb, duration, live_info, w=worker: self.on_formats_fetched(
                formats, title, thumb, duration, w, auto, live_info
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
        live_info: LiveInfo | None = None,
    ) -> None:
        self._release_format_worker(worker)
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

        self._show_live_info(live_info)
        if live_info is not None:
            self.status_label.setText("配信中のライブを検出しました")
        else:
            self.status_label.setText(f"動画{video_count}件・音声{audio_count}件を検出しました")
        self.spinner.stop()
        self.info_ready = True
        self.download_btn.setEnabled(True)

    def _release_format_worker(self, worker: FormatListWorker) -> None:
        """結果を受け取った取得ワーカーを終了待ちの対象から外す。結果の通知はrun()が
        戻る直前に届くため、参照を手放す前に終了を待つ(走行中のQThreadが破棄されると
        プロセスごと落ちる)"""
        worker.wait()
        self._format_workers.discard(worker)

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
        によるぼやけ・歪みを避けるため)。高DPI画面では実際の画素数で作り、
        ぼやけないようにする"""
        ratio = self.thumbnail_label.devicePixelRatioF()
        logical_size = self.thumbnail_label.size()
        target_width = round(logical_size.width() * ratio)
        target_height = round(logical_size.height() * ratio)
        scaled = pixmap.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - target_width) // 2)
        y = max(0, (scaled.height() - target_height) // 2)
        fitted = scaled.copy(x, y, target_width, target_height)
        fitted.setDevicePixelRatio(ratio)
        return fitted

    def on_formats_error(self, message: str, worker: FormatListWorker | None = None, auto: bool = False) -> None:
        if worker is not None:
            self._release_format_worker(worker)
            if worker is not self.format_worker:
                return

        self.spinner.stop()
        if auto:
            self.status_label.setText("動画情報を取得できませんでした")
        else:
            self.status_label.setText("フォーマット取得に失敗しました")
            dialogs.critical(self, "フォーマット取得エラー", message)

    def _show_live_info(self, live_info: LiveInfo | None) -> None:
        """配信中のライブかどうかに合わせて、LIVE見出し・録画開始位置の行・ボタンの文言を切り替える。

        手動設定(フォーマットIDがさかのぼり用と今から用で異なる)と切り抜き範囲(長さが決まらない)は
        ライブでは使えないため、無効表示ではなく隠す。通常の動画に戻ったら元に戻す"""
        was_live = self.live_info is not None
        self.live_info = live_info
        is_live = live_info is not None
        if is_live:
            self.manual_toggle_btn.setChecked(False)
            self.detail_toggle_btn.setChecked(False)
        self.manual_toggle_btn.setVisible(not is_live)
        self.detail_toggle_btn.setVisible(not is_live)
        self.live_header.setVisible(is_live)
        self.live_start_container.setVisible(is_live)
        can_go_back = live_info is not None and live_info.can_record_from_start
        self.live_from_start_radio.setVisible(can_go_back)
        self.live_from_now_radio.setVisible(can_go_back)
        self.live_unavailable_label.setVisible(is_live and not can_go_back)
        self.live_from_start_radio.setChecked(True)
        self.download_btn.setText(RECORD_BUTTON_TEXT if is_live else DOWNLOAD_BUTTON_TEXT)
        # LIVE見出しの分だけタイトルを詰め、サムネイルの高さに収める
        title_height = THUMBNAIL_SIZE.height()
        if is_live:
            title_height -= self.live_header.maximumHeight() + 2
        self.title_label.setMaximumHeight(title_height)
        if is_live:
            self._update_live_elapsed()
            self._live_elapsed_timer.start()
        else:
            self._live_elapsed_timer.stop()
        if is_live != was_live:
            # URLを打ち替えるたびにウィンドウの高さを合わせ直さないよう、ライブと通常の動画が切り替わる時だけにする
            self._sync_window_height()

    def _update_live_elapsed(self) -> None:
        started_at = self.live_info.started_at if self.live_info is not None else None
        if started_at is None:
            self.live_elapsed_label.setText("")
            self.live_from_start_radio.setText("配信の最初から")
            return
        elapsed = format_clip_time(max(0.0, time.time() - started_at))
        self.live_elapsed_label.setText(f"配信開始から {elapsed}")
        self.live_from_start_radio.setText(f"配信の最初から（{elapsed} さかのぼる）")

    def _selected_live_mode(self) -> LiveMode | None:
        if self.live_info is None:
            return None
        if self.live_info.can_record_from_start and self.live_from_start_radio.isChecked():
            return "from_start"
        return "from_now"

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
        if len(self._storyboard_workers) >= self._MAX_STORYBOARD_WORKERS:
            return

        self._pending_storyboard_urls.add(tile.fragment_url)
        worker = StoryboardFragmentWorker(tile.fragment_url)
        self._storyboard_workers.add(worker)
        generation = self._storyboard_generation
        worker.finished_ok.connect(
            lambda data, w=worker, g=generation: self._on_storyboard_fragment_fetched(w, data, which, g)
        )
        worker.finished_error.connect(
            lambda message, w=worker, g=generation: self._on_storyboard_fragment_failed(w, g)
        )
        worker.start()

    def _apply_storyboard_tile(self, which: HandleName, sprite: QPixmap, tile: StoryboardTile) -> None:
        cropped = sprite.copy(tile.x, tile.y, tile.width, tile.height)
        self.clip_range_slider.set_preview_pixmap(which, cropped)

    def _release_storyboard_worker(self, worker: StoryboardFragmentWorker, generation: int | None) -> bool:
        """終わった取得ワーカーを手放し、その結果が今の動画のものか(使ってよいか)を返す"""
        worker.wait()
        self._storyboard_workers.discard(worker)
        if generation is not None and generation != self._storyboard_generation:
            return False
        self._pending_storyboard_urls.discard(worker.url)
        return True

    def _on_storyboard_fragment_fetched(
        self, worker: StoryboardFragmentWorker, data: bytes, which: HandleName, generation: int | None = None
    ) -> None:
        if not self._release_storyboard_worker(worker, generation):
            return

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

    def _on_storyboard_fragment_failed(self, worker: StoryboardFragmentWorker, generation: int | None = None) -> None:
        self._release_storyboard_worker(worker, generation)

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

    def _reveal_saved_file(self, path: str) -> None:
        """保存したファイルを選択した状態でフォルダを開く。完了後に移動・削除されていれば、
        フォルダだけが残っていればそれを開き、それも無ければ知らせる"""
        if os.path.isfile(path):
            reveal_file(path)
            return
        folder = os.path.dirname(path)
        if os.path.isdir(folder):
            open_folder(folder)
            return
        dialogs.information(self, "フォルダを開く", f"保存したファイルが見つかりません:\n{path}")

    def _check_for_updates(self) -> None:
        worker = UpdateCheckWorker()
        self.update_check_worker = worker
        worker.update_available.connect(self.on_update_available)
        worker.up_to_date.connect(lambda w=worker: self._on_update_check_settled(w))
        worker.check_failed.connect(lambda message, w=worker: self._on_update_check_failed(message, w))
        worker.start()

    def _on_update_check_settled(self, worker: UpdateCheckWorker) -> None:
        worker.wait()
        if worker is self.update_check_worker:
            self.update_check_worker = None

    def _on_update_check_failed(self, message: str, worker: UpdateCheckWorker) -> None:
        # バックグラウンドの自動確認なので、ネットワーク不調等で失敗してもユーザーには
        # 見せない(対処法を提示できない不安を与えるだけのため)。ログにのみ残す
        log_debug(f"_check_for_updates: アップデート確認に失敗しました ({message})")
        self._on_update_check_settled(worker)

    def on_update_available(self, version: str, download_url: str, asset_name: str, sha256: str) -> None:
        if not download_url or not asset_name or not sha256:
            return
        if self._running_downloads():
            # 更新の適用にはアプリの終了が必要なため、動画のダウンロード中には提案しない
            # (次回起動時に改めて確認される)
            log_debug(f"on_update_available: ダウンロード中のため {version} への更新提案を見送りました")
            return
        if QApplication.activeModalWidget() is not None:
            # 高解像度の確認などのダイアログの最中に割り込むと、承諾後にそのダイアログへ戻って
            # 動画のダウンロードが始められてしまい、更新の適用(終了)と衝突する。閉じられるまで待つ
            self._deferred_update = (version, download_url, asset_name, sha256)
            self._update_prompt_timer.start()
            return

        reply = dialogs.question(
            self,
            "アップデートがあります",
            f"新しいバージョン {version} が利用可能です。今すぐダウンロードしてインストールしますか?\n"
            "(インストール後、アプリは自動的に再起動します)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_update_download(download_url, asset_name, sha256)

    def _offer_deferred_update(self) -> None:
        if self._deferred_update is None:
            return
        deferred, self._deferred_update = self._deferred_update, None
        self.on_update_available(*deferred)

    def _start_update_download(self, download_url: str, asset_name: str, sha256: str) -> None:
        dest_path = os.path.join(download_dir(), asset_name)
        worker = UpdateDownloadWorker(download_url, dest_path, sha256)
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
        self._pending_update_sha256 = worker.expected_sha256
        self._update_progress_dialog = progress
        self.close()

    def on_update_download_error(
        self, message: str, worker: UpdateDownloadWorker, progress: QProgressDialog
    ) -> None:
        if worker is not self.update_download_worker:
            return
        self.update_download_worker = None
        progress.close()
        progress.deleteLater()
        dialogs.critical(self, "アップデートのダウンロードに失敗しました", message)

    def on_update_download_cancelled(self, worker: UpdateDownloadWorker, progress: QProgressDialog) -> None:
        if worker is not self.update_download_worker:
            return
        self.update_download_worker = None
        progress.close()
        progress.deleteLater()

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
        box.setTextFormat(Qt.TextFormat.PlainText)
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
        # 親(このウィンドウ)が閉じるまで残り続けないよう、結果を読んだら破棄する
        box.deleteLater()
        if clicked is best_btn:
            return format_spec
        if p1080_btn is not None and clicked is p1080_btn:
            return plan.fallback_spec
        return None

    def start_download(self) -> None:
        if self.update_download_worker is not None:
            # 更新の適用にはアプリの終了が必要なため、更新のダウンロード中に動画のダウンロードを
            # 始めると、適用時に中止するかどうかを迫られることになる
            dialogs.information(self, "アップデート中", "アップデートのダウンロード中は動画をダウンロードできません。")
            return
        if self._is_downloading_url(self.url_edit.text().strip()):
            # 同じ動画を続けて開始するのはボタンの押し間違いであることが多いため、二重に保存せず知らせる
            # (別々の作業フォルダで処理するので、同時に走らせてもファイルが壊れることはない)
            dialogs.information(self, "ダウンロード中", "この動画は既にダウンロード中です。")
            return
        request = self._build_download_request()
        if request is None or not self._prepare_output_dir(request.out_dir):
            return

        title = self.title_label.text() or request.url
        worker = DownloadWorker(request)
        task = DownloadTaskWidget(title, self.thumbnail_label.pixmap())
        is_recording = request.live_mode is not None
        task.set_live(is_recording)
        self._download_tasks[worker] = task
        self._add_task_widget(task, recording=is_recording)

        worker.progress.connect(task.set_progress)
        # 複数のダウンロードのログが混ざるため、どの動画のものか分かるように添える
        worker.log.connect(lambda msg, t=title: self.append_log(f"[{t}] {msg}"))
        worker.finished_ok.connect(lambda saved_path, w=worker: self.on_download_finished_ok(w, saved_path))
        worker.finished_error.connect(lambda message, w=worker: self.on_download_error(w, message))
        worker.cancelled.connect(lambda w=worker: self.on_download_cancelled(w))
        worker.live_state_changed.connect(lambda live, t=task: self._on_live_state_changed(t, live))
        task.cancel_requested.connect(lambda w=worker: self.cancel_download(w))
        task.stop_requested.connect(lambda w=worker: self.request_stop_recording(w))
        task.dismiss_requested.connect(lambda t=task: self._remove_task_widget(t))
        task.reveal_requested.connect(self._reveal_saved_file)
        worker.start()

        # 続けて次の動画を入力できるよう入力欄を空にする(textChangedを通じて
        # フォーマット選択・クリップ範囲等もまとめて初期化される)
        self.url_edit.clear()
        # 手動設定のフォーマットは動画ごとに選び直すものなので、既定の自動設定に戻す
        # (手動設定の列幅に合わせて広がったウィンドウ幅も、on_manual_toggledで元に戻る)
        self.manual_toggle_btn.setChecked(False)
        self.status_label.setText("ダウンロードを開始しました")

    def _is_downloading_url(self, url: str) -> bool:
        return any(worker.request.url == url for worker in self._running_downloads())

    def _running_downloads(self) -> list[DownloadWorker]:
        return [worker for worker in self._download_tasks if worker.isRunning()]

    @staticmethod
    def _is_recording(worker: DownloadWorker) -> bool:
        return worker.request.live_mode is not None

    def _add_task_widget(self, task: DownloadTaskWidget, recording: bool = False) -> None:
        """新しいものほど上に表示する。録画中のものは通常のダウンロードより上に置き、
        通常のダウンロードが何件増えてもスクロールしないうちから見えるようにする"""
        if recording:
            self.download_list_layout.insertWidget(0, task)
            self._recording_task_widgets.append(task)
        else:
            self.download_list_layout.insertWidget(len(self._recording_task_widgets), task)
        self._set_download_panel_visible(True)

    def _move_below_recordings(self, task: DownloadTaskWidget) -> None:
        """録画が終わったタスクを、録画中のものの下(通常の一覧の先頭)へ移す(録画中でなければ何もしない)"""
        if task not in self._recording_task_widgets:
            return
        self._recording_task_widgets.remove(task)
        self.download_list_layout.removeWidget(task)
        self.download_list_layout.insertWidget(len(self._recording_task_widgets), task)

    def _on_live_state_changed(self, task: DownloadTaskWidget, live: bool) -> None:
        if not live:
            self._move_below_recordings(task)

    def _remove_task_widget(self, task: DownloadTaskWidget) -> None:
        self._move_below_recordings(task)
        self.download_list_layout.removeWidget(task)
        task.deleteLater()
        # 末尾の伸縮用スペーサーだけが残ったら一覧を隠す
        if self.download_list_layout.count() <= 1:
            self._set_download_panel_visible(False)

    def _set_download_panel_visible(self, visible: bool) -> None:
        """一覧の表示/非表示に合わせて、左側の入力欄の幅が変わらないようウィンドウ幅を増減する"""
        if self.download_panel.isHidden() != visible:
            return
        # 表示した瞬間に、Qtが「左側の最小幅+一覧」までウィンドウを自動で広げることがある
        # (ライブの「配信の最初から（H:MM:SS さかのぼる）」等で左側の最小幅が広い場合)。
        # その分を二重に足さないよう、表示前の幅を基準にする
        width_before = self.width()
        self.download_panel.setVisible(visible)
        central = self.centralWidget()
        assert central is not None
        layout = central.layout()
        assert layout is not None
        delta = DOWNLOAD_PANEL_WIDTH + layout.spacing()
        if not visible:
            delta = -delta
        self.resize(width_before + delta, self.height())
        # 手動設定を開いている間に一覧が出入りした場合も、自動設定に戻したときの幅を一覧の分だけ合わせる
        if self._width_before_manual is not None:
            self._width_before_manual += delta

    def _build_download_request(self) -> DownloadRequest | None:
        """入力内容を検証・確認してダウンロード要求を組み立てる。入力エラーや
        ユーザーのキャンセルの場合は、ダイアログを出した上でNoneを返す"""
        url = self.url_edit.text().strip()
        out_dir_text = self.out_edit.text().strip()
        if not self._check_required_inputs(url, out_dir_text):
            return None
        out_dir = self._normalize_output_dir(out_dir_text)
        if out_dir is None:
            return None

        try:
            selection = self._current_format_selection()
            clip_start, clip_end = resolve_clip_range(self.clip_start_edit.text(), self.clip_end_edit.text())
            self._check_clip_within_duration(clip_start, clip_end)
        except ValueError as e:
            dialogs.warning(self, "入力エラー", str(e))
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
            live_mode=self._selected_live_mode(),
            embed_thumbnail=not self.no_thumbnail_checkbox.isChecked(),
        )

    def _check_required_inputs(self, url: str, out_dir: str) -> bool:
        if not url:
            dialogs.warning(self, "入力エラー", "URLを入力してください")
            return False
        if not out_dir:
            dialogs.warning(self, "入力エラー", "保存先フォルダを指定してください")
            return False
        if get_ffmpeg_location() is None:
            dialogs.critical(
                self,
                "ffmpegが見つかりません",
                f"ffmpegが見つかりません。アプリの ffmpeg{os.sep}{FFMPEG_EXECUTABLE_NAME} を配置するか、"
                "システムにffmpegをインストールしてPATHを通してください。",
            )
            return False
        return True

    def _normalize_output_dir(self, out_dir: str) -> str | None:
        """手入力の保存先を絶対パスに整える(入力エラーならダイアログを出してNone)。

        "~"はシェルを通さないため展開されず、"~"という名前のフォルダが作られてしまう。
        相対パスはアプリの作業フォルダ(起動方法で変わり、インストール先のこともある)基準に
        なって保存先が分からなくなるため受け付けない"""
        expanded = os.path.expanduser(out_dir)
        if not os.path.isabs(expanded):
            dialogs.warning(
                self, "入力エラー", f"保存先フォルダはフルパスで指定してください:\n{out_dir}"
            )
            return None
        normalized = os.path.normpath(expanded)
        if normalized != out_dir:
            # 実際に保存する場所が分かるよう、展開・整形した結果を入力欄にも反映する
            self.out_edit.setText(normalized)
        return normalized

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
        reply = dialogs.question(
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
            dialogs.warning(
                self, "入力エラー", f"保存先フォルダを作成できませんでした:\n{out_dir}\n\n{e}"
            )
            return False
        self.settings.setValue("last_output_dir", out_dir)
        return True

    def cancel_download(self, worker: DownloadWorker) -> None:
        worker.cancel()
        task = self._download_tasks.get(worker)
        if task is not None:
            task.set_cancelling()

    def request_stop_recording(self, worker: DownloadWorker) -> None:
        """今からの録画は止めると取り直せないため、押し間違いでないか確かめ、録画した分を
        保存するか破棄するかを選んでもらってから止める"""
        action = self._ask_recording_stop_action()
        if action == "save":
            self.cancel_download(worker)
        elif action == "discard":
            worker.discard()
            task = self._download_tasks.get(worker)
            if task is not None:
                task.set_cancelling(discarding=True)

    def _ask_recording_stop_action(self) -> Literal["save", "discard"] | None:
        """録画の停止で、保存・破棄・キャンセル(録画を続ける)のどれかを選んでもらう。
        押し間違いで録画が止まらないよう、Enter・Esc・×はいずれもキャンセルになる"""
        box = QMessageBox(self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("録画の停止")
        box.setText("録画を停止します。録画した分を保存しますか?")
        save_btn = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = box.addButton("破棄", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.setEscapeButton(cancel_btn)
        box.exec()

        clicked = box.clickedButton()
        box.deleteLater()
        if clicked is save_btn:
            return "save"
        if clicked is discard_btn:
            return "discard"
        return None

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """終了時に、走っているワーカースレッドを止めて終了を待つ。

        待たずに閉じるとQThreadがrun()(ネットワークダウンロード中やffmpegの切り抜き処理中)
        のままインタプリタ終了処理に入り、"QThread: Destroyed while thread is still running"
        を招く。yt-dlp/ffmpegが中途半端に打ち切られ、DownloadWorker側の後片付けも走らないため
        未完成ファイルの入った作業フォルダが保存先に残ってしまう。
        """
        assert event is not None
        if self._saving_dialog is not None:
            # 録画の保存を待っている間に、もう一度閉じようとした。保存が終われば自動で閉じる
            event.ignore()
            return

        running_downloads = self._running_downloads()
        if running_downloads and not self._close_confirmed:
            reply = dialogs.question(
                self,
                "ダウンロード中",
                self._close_confirmation_text(running_downloads),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                if self._pending_update_path is not None:
                    self._pending_update_path = None
                    if self._update_progress_dialog is not None:
                        self._update_progress_dialog.close()
                        self._update_progress_dialog.deleteLater()
                        self._update_progress_dialog = None
                    dialogs.information(
                        self, "アップデート", "アップデートを中止しました。次回起動時に改めて確認します。"
                    )
                event.ignore()
                return
            self._close_confirmed = True
            # 確認ダイアログを出している間に終わったものがあり得るため、改めて数え直す
            # (終わった録画を待とうとすると、保存待ちのまま二度と閉じられなくなる)
            running_downloads = self._running_downloads()
        for download in running_downloads:
            self.cancel_download(download)

        if any(self._is_recording(w) for w in running_downloads):
            # 録画の保存(結合・再格納)は長い録画では数分かかることがあり、待ち時間に上限を設けると
            # 録画を失う。UIスレッドで待つと「保存しています」の表示も描画されないため、終了はいったん
            # 取り消し、録画が全部終わったところで改めて閉じる(_close_after_recordings)
            self._show_saving_dialog()
            event.ignore()
            return

        if self.update_download_worker is not None:
            self.update_download_worker.cancel()

        # cancel()は進捗フック経由でしか効かず、後処理(ffmpegの結合・切り抜き)の最中は
        # 区切りが来るまで止まらないため、待ち時間には上限を設ける。上限はワーカーごとでなく
        # 全体で1つにして、走っているワーカーの数だけUIが固まる時間が伸びないようにする。
        # 時間切れの場合はこれ以上UIスレッドからできることがないので、記録だけ残して終了する
        deadline = time.monotonic() + self._WORKER_SHUTDOWN_WAIT_MS / 1000
        all_stopped = True
        for worker in self._running_workers():
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if not worker.wait(remaining_ms):
                all_stopped = False
                log_debug(
                    f"closeEvent: {type(worker).__name__} が"
                    f"{self._WORKER_SHUTDOWN_WAIT_MS}ms以内に終了しませんでした"
                )

        if self._pending_update_path is not None and not all_stopped:
            # 残ったスレッドが処理中のままインストーラーに強制終了されると、ダウンロードや
            # 後処理の途中のファイルが壊れるため、適用は見送って次回起動時の確認に任せる
            log_debug("closeEvent: 終了していないワーカーがあるためアップデートの適用を見送りました")
            self._pending_update_path = None

        if self._pending_update_path is not None:
            update_path = self._pending_update_path
            self._pending_update_path = None
            try:
                apply_downloaded_update(update_path, self._pending_update_sha256)
            except Exception as e:
                # 元のアプリはそのまま残っているため、終了自体は続行して手動更新を案内する
                log_debug(f"closeEvent: アップデートの適用に失敗しました ({e!r})")
                dialogs.critical(
                    self,
                    "アップデートの適用に失敗しました",
                    f"アップデートを適用できませんでした。手動でダウンロード・インストールしてください。\n\n{e}",
                )
        event.accept()

    def _close_confirmation_text(self, running_downloads: list[DownloadWorker]) -> str:
        recordings = sum(1 for w in running_downloads if self._is_recording(w))
        downloads = len(running_downloads) - recordings
        parts = []
        if recordings:
            parts.append(f"録画中の{recordings}件を停止・保存し")
        if downloads:
            parts.append(f"ダウンロード中の{downloads}件を中止し")
        return "、".join(parts) + "て終了しますか?"

    def _show_saving_dialog(self) -> None:
        dialog = QProgressDialog("録画を保存しています。保存が終わると自動的に終了します...", None, 0, 0, self)
        dialog.setWindowTitle("終了")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        # Esc・閉じるボタンで隠されると、操作を続けた後に保存が終わったところで突然アプリが終了してしまうため、
        # 保存が終わるまで出し続ける
        dialog.canceled.connect(self._keep_saving_dialog_shown)
        dialog.rejected.connect(self._keep_saving_dialog_shown)
        dialog.show()
        self._saving_dialog = dialog

    def _keep_saving_dialog_shown(self) -> None:
        # 隠す処理が終わってから表示し直す
        QTimer.singleShot(0, self._reshow_saving_dialog)

    def _reshow_saving_dialog(self) -> None:
        if self._saving_dialog is not None:
            self._saving_dialog.show()

    def _close_after_recordings(self) -> None:
        """終了の待ちの間に録画が全部終わったら、改めてウィンドウを閉じる"""
        if self._saving_dialog is None:
            return
        if any(self._is_recording(w) for w in self._running_downloads()):
            return
        self._saving_dialog.close()
        self._saving_dialog.deleteLater()
        self._saving_dialog = None
        self.close()

    def _running_workers(self) -> list[QThread]:
        """終了待ちの対象となる、現在走っているワーカースレッドを列挙する"""
        candidates = [
            *self._download_tasks,
            self.format_worker,
            *self._format_workers,
            self.update_check_worker,
            self.update_download_worker,
            *self._storyboard_workers,
        ]
        # format_workerは_format_workersにも含まれるため、同じワーカーを二重に待たないようにする
        unique = list(dict.fromkeys(w for w in candidates if w is not None))
        return [w for w in unique if w.isRunning()]

    def _release_download_worker(self, worker: DownloadWorker) -> DownloadTaskWidget | None:
        """終わったダウンロードワーカーを手放し、その表示を返す。完了の通知はrun()が
        戻る直前に届くため、参照を手放す前に終了を待つ(走行中のQThreadが破棄されるとプロセスごと落ちる)。
        録画の表示は、終わり方によらず録画中のまとまりから外す"""
        worker.wait()
        task = self._download_tasks.pop(worker, None)
        if task is not None:
            self._move_below_recordings(task)
        return task

    def on_download_finished_ok(self, worker: DownloadWorker, saved_path: str) -> None:
        """並列に何本も完了するとウィンドウが増え続けるため、フォルダは自動では開かず、
        一覧の「フォルダを開く」から開いてもらう"""
        task = self._release_download_worker(worker)
        if task is not None:
            task.mark_finished("完了", saved_path=saved_path)
        self._close_after_recordings()

    def on_download_error(self, worker: DownloadWorker, message: str) -> None:
        task = self._release_download_worker(worker)
        if task is not None:
            task.mark_finished("失敗しました")
        if not self._close_confirmed:
            # 並列に走っている中のどれが失敗したのか分かるよう、タイトルを添える。終了の待ちの間は、
            # モーダルのダイアログで終了が止まらないよう出さない(理由はログに残っている)
            title = task.title_label.text() if task is not None else worker.request.url
            dialogs.critical(self, "ダウンロード失敗", f"{title}\n\n{message}")
        self._close_after_recordings()

    def on_download_cancelled(self, worker: DownloadWorker) -> None:
        """ユーザーが自分で止めた場合は失敗ではないため、エラーのダイアログは出さない。
        結果を確かめる必要もないので、一覧からもそのまま取り除く(止まるまでは「キャンセル中...」のまま残る)"""
        task = self._release_download_worker(worker)
        if task is not None:
            self._remove_task_widget(task)
        self._close_after_recordings()
