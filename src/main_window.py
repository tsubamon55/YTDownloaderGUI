"""メインウィンドウ(UI組み立てとイベントハンドリング)"""

import os
from datetime import datetime

from PyQt6.QtCore import Qt, QSettings, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from formats import (
    BEST_AUDIO_COMPATIBLE_SORT,
    BEST_QUALITY_COMPATIBLE_SORT,
    FORMAT_COLUMN_ROLE,
    FORMAT_COLUMN_WIDTHS,
    FORMAT_OPTIONS,
    describe_format_plain,
    format_columns,
)
from paths import get_downloads_folder, get_ffmpeg_location
from widgets import FormatComboBox, FormatHeaderWidget, FormatItemDelegate, SpinnerWidget
from workers import DownloadWorker, FormatListWorker

IDLE_STATUS_TEXT = "待機中"

LINK_BUTTON_STYLE = """
QPushButton {
    color: #1a73e8;
    border: none;
    padding: 2px 4px;
    background: transparent;
}
QPushButton:hover { text-decoration: underline; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube 動画ダウンローダー")
        self.resize(760, 420)

        self.worker: DownloadWorker | None = None
        self.format_worker: FormatListWorker | None = None
        self.last_output_dir: str | None = None
        self.info_ready = False

        self._info_fetch_timer = QTimer(self)
        self._info_fetch_timer.setSingleShot(True)
        self._info_fetch_timer.setInterval(700)
        self._info_fetch_timer.timeout.connect(lambda: self.fetch_formats(auto=True))

        self.settings = QSettings("ytdlp-gui", "YTDownloaderGUI")
        default_out_dir = get_downloads_folder()
        saved_out_dir = self.settings.value("last_output_dir", default_out_dir, type=str)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # --- URL入力 ---
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.url_edit.setMinimumHeight(32)
        self.url_edit.textChanged.connect(self.on_url_changed)
        url_row.addWidget(self.url_edit, stretch=1)
        self.paste_btn = QPushButton("貼り付け")
        self.paste_btn.clicked.connect(self.paste_from_clipboard)
        url_row.addWidget(self.paste_btn)
        layout.addLayout(url_row)

        # --- 動画プレビュー ---
        # wordWrap付きQLabelをQHBoxLayout経由で直接QVBoxLayoutに入れると、
        # heightForWidthの計算がずれて縦方向に大きく間延びするため、
        # 高さを固定したコンテナで包んで挙動を安定させる
        preview_container = QWidget()
        preview_container.setFixedHeight(68)
        preview_row = QHBoxLayout(preview_container)
        preview_row.setContentsMargins(0, 0, 0, 0)
        self.thumbnail_label = QLabel(preview_container)
        self.thumbnail_label.setFixedSize(120, 68)
        self.thumbnail_label.setScaledContents(True)
        self.thumbnail_label.setStyleSheet("background-color: rgba(128, 128, 128, 35); border-radius: 3px;")
        preview_row.addWidget(self.thumbnail_label)
        self.title_label = QLabel("", preview_container)
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumHeight(68)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        preview_row.addWidget(self.title_label, stretch=1)
        layout.addWidget(preview_container)

        # --- フォーマット選択 ---
        format_row = QHBoxLayout()
        self.simple_format_container = QWidget()
        simple_layout = QHBoxLayout(self.simple_format_container)
        simple_layout.setContentsMargins(0, 0, 0, 0)
        simple_layout.addWidget(QLabel("形式:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMAT_OPTIONS.keys())
        simple_layout.addWidget(self.format_combo, stretch=1)
        format_row.addWidget(self.simple_format_container, stretch=1)
        # simple_format_container が非表示のときはこのスペーサーが余白を吸収し、
        # detail_toggle_btn が引き伸ばされて中央寄りに見えるのを防ぐ
        format_row.addStretch(0)
        self.detail_toggle_btn = QPushButton("詳細設定 ▾")
        self.detail_toggle_btn.setCheckable(True)
        self.detail_toggle_btn.setFlat(True)
        self.detail_toggle_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.detail_toggle_btn.setStyleSheet(LINK_BUTTON_STYLE)
        self.detail_toggle_btn.toggled.connect(self.on_detail_toggled)
        format_row.addWidget(self.detail_toggle_btn)
        self.format_row = format_row
        layout.addLayout(format_row)

        self.detail_container = QWidget()
        detail_layout = QVBoxLayout(self.detail_container)
        detail_layout.setContentsMargins(0, 2, 0, 0)

        self.format_item_delegate = FormatItemDelegate()
        format_combo_min_width = sum(FORMAT_COLUMN_WIDTHS) + 40

        video_label = QLabel("動画:")
        audio_label = QLabel("音声:")
        label_width = max(video_label.sizeHint().width(), audio_label.sizeHint().width())
        video_label.setFixedWidth(label_width)
        audio_label.setFixedWidth(label_width)

        header_row = QHBoxLayout()
        header_spacer = QLabel("")
        header_spacer.setFixedWidth(label_width)
        header_row.addWidget(header_spacer)
        self.format_header = FormatHeaderWidget()
        header_row.addWidget(self.format_header, stretch=1)
        detail_layout.addLayout(header_row)

        video_row = QHBoxLayout()
        video_row.addWidget(video_label)
        self.video_format_combo = FormatComboBox()
        self.video_format_combo.setEnabled(False)
        self.video_format_combo.setItemDelegate(self.format_item_delegate)
        self.video_format_combo.setMinimumWidth(format_combo_min_width)
        self.video_format_combo.currentIndexChanged.connect(self.on_detail_selection_changed)
        video_row.addWidget(self.video_format_combo, stretch=1)
        detail_layout.addLayout(video_row)

        audio_row = QHBoxLayout()
        audio_row.addWidget(audio_label)
        self.audio_format_combo = FormatComboBox()
        self.audio_format_combo.setEnabled(False)
        self.audio_format_combo.setItemDelegate(self.format_item_delegate)
        self.audio_format_combo.setMinimumWidth(format_combo_min_width)
        self.audio_format_combo.currentIndexChanged.connect(self.on_detail_selection_changed)
        audio_row.addWidget(self.audio_format_combo, stretch=1)
        detail_layout.addLayout(audio_row)

        self.merge_note_label = QLabel("")
        detail_layout.addWidget(self.merge_note_label)

        mp3_checkbox_row = QHBoxLayout()
        self.mp3_checkbox = QCheckBox()
        self.mp3_checkbox.setEnabled(False)
        mp3_checkbox_row.addWidget(self.mp3_checkbox)
        self.mp3_label = QLabel("音声のみのダウンロードの場合、mp3に変換する")
        self.mp3_label.setEnabled(False)
        mp3_checkbox_row.addWidget(self.mp3_label)
        mp3_checkbox_row.addStretch()
        detail_layout.addLayout(mp3_checkbox_row)

        layout.addWidget(self.detail_container)
        self.detail_container.setVisible(False)

        # --- 保存先 ---
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("保存先:"))
        self.out_edit = QLineEdit(saved_out_dir)
        out_row.addWidget(self.out_edit, stretch=1)
        self.browse_btn = QPushButton("参照...")
        self.browse_btn.clicked.connect(self.browse_folder)
        out_row.addWidget(self.browse_btn)
        layout.addLayout(out_row)

        # --- ダウンロード ---
        btn_row = QHBoxLayout()
        self.download_btn = QPushButton("ダウンロード開始")
        self.download_btn.setMinimumHeight(40)
        download_font = self.download_btn.font()
        download_font.setBold(True)
        self.download_btn.setFont(download_font)
        self.download_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #1a73e8;
                color: white;
                font-weight: bold;
                padding: 6px 16px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #1765cc; }
            QPushButton:pressed { background-color: #145bb5; }
            QPushButton:disabled { background-color: #a7c6f5; color: #f0f0f0; }
            """
        )
        self.download_btn.clicked.connect(self.start_download)
        self.download_btn.setEnabled(False)
        btn_row.addWidget(self.download_btn, stretch=1)
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.cancel_btn.setEnabled(False)
        btn_row.addWidget(self.cancel_btn)
        self.open_folder_btn = QPushButton("フォルダを開く")
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self.open_output_folder)
        btn_row.addWidget(self.open_folder_btn)
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        status_row = QHBoxLayout()
        self.spinner = SpinnerWidget()
        status_row.addWidget(self.spinner)
        self.status_label = QLabel(IDLE_STATUS_TEXT)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.log_toggle_btn = QPushButton("ログ ▾")
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.setFlat(True)
        self.log_toggle_btn.setStyleSheet(LINK_BUTTON_STYLE)
        self.log_toggle_btn.toggled.connect(self.on_log_toggle)
        status_row.addWidget(self.log_toggle_btn)
        layout.addLayout(status_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)

        self.input_widgets = [
            self.url_edit,
            self.paste_btn,
            self.out_edit,
            self.browse_btn,
            self.format_combo,
            self.detail_toggle_btn,
            self.video_format_combo,
            self.audio_format_combo,
            self.mp3_checkbox,
            self.mp3_label,
        ]

        self.auto_paste_from_clipboard()
        self._sync_window_height()

    def _sync_window_height(self):
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

    def on_log_toggle(self, checked: bool):
        self.log_view.setVisible(checked)
        self.log_toggle_btn.setText("ログ ▴" if checked else "ログ ▾")
        self._sync_window_height()

    def on_url_changed(self, text: str):
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

    def on_detail_toggled(self, checked: bool):
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

        url = self.url_edit.text().strip()
        is_fetching = self.format_worker is not None and self.format_worker.isRunning()
        if checked and url and not has_items and not is_fetching:
            self.fetch_formats(auto=False)

    def paste_from_clipboard(self):
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)

    def auto_paste_from_clipboard(self):
        text = QApplication.clipboard().text().strip()
        if text.startswith("http://") or text.startswith("https://"):
            self.url_edit.setText(text)

    def fetch_formats(self, auto: bool = False):
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
        self.download_btn.setEnabled(False)
        self.status_label.setText("動画情報を取得中...")
        self.spinner.start()

        worker = FormatListWorker(url)
        self.format_worker = worker
        worker.finished_ok.connect(
            lambda formats, title, thumb, w=worker: self.on_formats_fetched(formats, title, thumb, w, auto)
        )
        worker.finished_error.connect(
            lambda message, w=worker: self.on_formats_error(message, w, auto)
        )
        worker.start()

    def on_formats_fetched(self, formats: list, title: str, thumbnail_bytes: bytes, worker, auto: bool = False):
        if worker is not self.format_worker:
            return

        self.video_format_combo.clear()
        self.audio_format_combo.clear()

        self.video_format_combo.addItem("なし", userData=None)
        self.audio_format_combo.addItem("なし", userData=None)

        video_count = 0
        audio_count = 0
        for fmt in formats:
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

        self.video_format_combo.setEnabled(self.detail_toggle_btn.isChecked())
        self.audio_format_combo.setEnabled(self.detail_toggle_btn.isChecked())
        self.on_detail_selection_changed()

        self.title_label.setText(title)
        if thumbnail_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(thumbnail_bytes):
                self.thumbnail_label.setPixmap(pixmap)

        self.status_label.setText(f"動画{video_count}件・音声{audio_count}件を検出しました")
        self.spinner.stop()
        self.info_ready = True
        self.download_btn.setEnabled(True)

    def on_formats_error(self, message: str, worker=None, auto: bool = False):
        if worker is not None and worker is not self.format_worker:
            return

        self.spinner.stop()
        if auto:
            self.status_label.setText("動画情報を取得できませんでした")
        else:
            self.status_label.setText("フォーマット取得に失敗しました")
            QMessageBox.critical(self, "フォーマット取得エラー", message)

    def on_detail_selection_changed(self, *_):
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

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダを選択", self.out_edit.text())
        if folder:
            self.out_edit.setText(folder)
            self.settings.setValue("last_output_dir", folder)

    def open_output_folder(self):
        if self.last_output_dir and os.path.isdir(self.last_output_dir):
            os.startfile(self.last_output_dir)

    def set_inputs_enabled(self, enabled: bool):
        for widget in self.input_widgets:
            widget.setEnabled(enabled)
        if enabled:
            self.on_detail_toggled(self.detail_toggle_btn.isChecked())

    def append_log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {msg}")

    def resolve_format_spec(self) -> tuple[str, list, list | None]:
        if self.detail_toggle_btn.isChecked():
            video_fmt = self.video_format_combo.currentData()
            audio_fmt = self.audio_format_combo.currentData()

            if video_fmt is None and audio_fmt is None:
                raise ValueError("動画または音声のフォーマットを選択してください")

            postprocessors = []
            if video_fmt is not None and audio_fmt is not None:
                format_spec = f"{video_fmt['format_id']}+{audio_fmt['format_id']}"
            elif video_fmt is not None:
                format_spec = video_fmt["format_id"]
            else:
                format_spec = audio_fmt["format_id"]
                if self.mp3_checkbox.isChecked():
                    postprocessors = [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }
                    ]
            return format_spec, postprocessors, None

        format_label = self.format_combo.currentText()
        format_key = FORMAT_OPTIONS[format_label]
        if format_key == "audio_mp3":
            return (
                "ba/b",
                [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
                None,
            )
        if format_key == "audio_best":
            return "ba/b", [], BEST_AUDIO_COMPATIBLE_SORT
        if format_label == "動画 (最高画質)":
            return format_key, [], BEST_QUALITY_COMPATIBLE_SORT
        return format_key, [], None

    def start_download(self):
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
                "同梱のffmpegフォルダが見つかりません。アプリの ffmpeg\\ffmpeg.exe を確認してください。",
            )
            return

        try:
            format_spec, postprocessors, format_sort = self.resolve_format_spec()
        except ValueError as e:
            QMessageBox.warning(self, "入力エラー", str(e))
            return

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

        self.worker = DownloadWorker(url, out_dir, format_spec, postprocessors, format_sort)
        self.worker.progress.connect(self.on_progress)
        self.worker.log.connect(self.append_log)
        self.worker.finished_ok.connect(self.on_finished_ok)
        self.worker.finished_error.connect(self.on_finished_error)
        self.worker.start()

    def cancel_download(self):
        if self.worker is not None:
            self.worker.cancel()
            self.status_label.setText("キャンセル中...")

    def on_progress(self, percent: float, text: str):
        self.progress_bar.setValue(int(percent))
        self.status_label.setText(text)

    def on_finished_ok(self):
        self.spinner.stop()
        self.status_label.setText("完了")
        self.set_inputs_enabled(True)
        self.download_btn.setEnabled(self.info_ready)
        self.cancel_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(True)
        self.progress_bar.setValue(100)

    def on_finished_error(self, message: str):
        self.spinner.stop()
        self.status_label.setText("エラーまたはキャンセル")
        self.set_inputs_enabled(True)
        self.download_btn.setEnabled(self.info_ready)
        self.cancel_btn.setEnabled(False)
        QMessageBox.critical(self, "ダウンロード失敗", message)
