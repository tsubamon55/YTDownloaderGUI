import os
import sys
import traceback
import urllib.request

from PyQt6.QtCore import Qt, QSettings, QThread, pyqtSignal
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
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

import yt_dlp


def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_ffmpeg_location() -> str | None:
    candidates = [get_base_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(meipass)

    for base in candidates:
        ffmpeg_dir = os.path.join(base, "ffmpeg")
        if os.path.isfile(os.path.join(ffmpeg_dir, "ffmpeg.exe")):
            return ffmpeg_dir
    return None


FORMAT_OPTIONS = {
    "動画 (最高画質 mp4)": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
    "動画 (最高画質)": "bv*+ba/b",
    "音声のみ (mp3)": "audio_mp3",
    "音声のみ (最高音質)": "audio_best",
}

# 解像度を最優先しつつ、同じ解像度の中では最も互換性の高いコーデック(h264/aac)を選ぶ
BEST_QUALITY_COMPATIBLE_SORT = ["res", "codec:avc:m4a"]

# 音声ビットレートを最優先しつつ、同じビットレートの中では最も互換性の高いコーデック(aac/m4a)を選ぶ
BEST_AUDIO_COMPATIBLE_SORT = ["abr", "acodec:m4a"]


def format_size(num_bytes) -> str:
    if not num_bytes:
        return "不明"
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f}GB"
    return f"{mb:.1f}MB"


def describe_format(fmt: dict) -> str:
    format_id = fmt.get("format_id", "?")
    ext = fmt.get("ext", "?")
    vcodec = fmt.get("vcodec", "none")
    acodec = fmt.get("acodec", "none")
    has_video = vcodec and vcodec != "none"
    has_audio = acodec and acodec != "none"

    if has_video and has_audio:
        kind = "動画+音声"
    elif has_video:
        kind = "動画のみ"
    elif has_audio:
        kind = "音声のみ"
    else:
        kind = "不明"

    parts = [f"[{format_id}]", ext, kind]

    if has_video:
        resolution = fmt.get("resolution") or (
            f"{fmt.get('width')}x{fmt.get('height')}" if fmt.get("height") else None
        )
        if resolution:
            parts.append(resolution)
        fps = fmt.get("fps")
        if fps:
            parts.append(f"{fps}fps")

    if has_audio and not has_video:
        abr = fmt.get("abr")
        if abr:
            parts.append(f"{abr:.0f}kbps")

    size = fmt.get("filesize") or fmt.get("filesize_approx")
    parts.append(format_size(size))

    note = fmt.get("format_note")
    if note:
        parts.append(note)

    return " | ".join(str(p) for p in parts)


class FormatListWorker(QThread):
    finished_ok = pyqtSignal(list, str, bytes)
    finished_error = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
            }
            ffmpeg_location = get_ffmpeg_location()
            if ffmpeg_location:
                ydl_opts["ffmpeg_location"] = ffmpeg_location

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)

            formats = info.get("formats") or []
            formats = [f for f in formats if f.get("format_id")]
            formats.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)

            title = info.get("title") or ""
            thumbnail_bytes = b""
            thumbnail_url = info.get("thumbnail")
            if thumbnail_url:
                try:
                    with urllib.request.urlopen(thumbnail_url, timeout=10) as resp:
                        thumbnail_bytes = resp.read()
                except Exception:
                    thumbnail_bytes = b""

            self.finished_ok.emit(formats, title, thumbnail_bytes)
        except Exception as e:
            self.finished_error.emit(str(e))


class DownloadWorker(QThread):
    progress = pyqtSignal(float, str)
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    finished_error = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        out_dir: str,
        format_spec: str,
        postprocessors: list | None = None,
        format_sort: list | None = None,
    ):
        super().__init__()
        self.url = url
        self.out_dir = out_dir
        self.format_spec = format_spec
        self.postprocessors = postprocessors or []
        self.format_sort = format_sort
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _progress_hook(self, d):
        if self._is_cancelled:
            raise yt_dlp.utils.DownloadError("ユーザーによりキャンセルされました")

        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                percent = downloaded / total * 100
            else:
                percent = 0.0
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            self.progress.emit(percent, f"{d.get('_percent_str', '').strip()} 速度:{speed} 残り:{eta}")
        elif status == "finished":
            self.progress.emit(100.0, "ダウンロード完了、後処理中...")

    def _log_message(self, msg: str):
        self.log.emit(msg)

    def run(self):
        try:
            ydl_opts = {
                "outtmpl": os.path.join(self.out_dir, "%(title)s.%(ext)s"),
                "progress_hooks": [self._progress_hook],
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "format": self.format_spec,
            }

            if self.postprocessors:
                ydl_opts["postprocessors"] = self.postprocessors

            if self.format_sort:
                ydl_opts["format_sort"] = self.format_sort

            ffmpeg_location = get_ffmpeg_location()
            if ffmpeg_location:
                ydl_opts["ffmpeg_location"] = ffmpeg_location

            self.log.emit(f"開始: {self.url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])

            if self._is_cancelled:
                self.finished_error.emit("キャンセルされました")
            else:
                self.log.emit("完了しました")
                self.finished_ok.emit()
        except Exception as e:
            self.log.emit(f"エラー: {e}")
            self.finished_error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("yt-dlp GUI ダウンローダー")
        self.resize(640, 520)

        self.worker: DownloadWorker | None = None
        self.format_worker: FormatListWorker | None = None
        self.last_output_dir: str | None = None

        self.settings = QSettings("ytdlp-gui", "YTDownloaderGUI")
        default_out_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        saved_out_dir = self.settings.value("last_output_dir", default_out_dir, type=str)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.url_edit.textChanged.connect(self.on_url_changed)
        url_row.addWidget(self.url_edit, stretch=1)
        self.paste_btn = QPushButton("貼り付け")
        self.paste_btn.clicked.connect(self.paste_from_clipboard)
        url_row.addWidget(self.paste_btn)
        layout.addLayout(url_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("保存先:"))
        self.out_edit = QLineEdit(saved_out_dir)
        out_row.addWidget(self.out_edit, stretch=1)
        self.browse_btn = QPushButton("参照...")
        self.browse_btn.clicked.connect(self.browse_folder)
        out_row.addWidget(self.browse_btn)
        layout.addLayout(out_row)

        self.simple_format_container = QWidget()
        simple_layout = QVBoxLayout(self.simple_format_container)
        simple_layout.setContentsMargins(0, 0, 0, 0)
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("フォーマット:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMAT_OPTIONS.keys())
        fmt_row.addWidget(self.format_combo, stretch=1)
        simple_layout.addLayout(fmt_row)
        layout.addWidget(self.simple_format_container)

        self.detail_checkbox = QCheckBox("詳細フォーマットを使用(取得した一覧から選択)")
        self.detail_checkbox.toggled.connect(self.on_detail_toggled)
        layout.addWidget(self.detail_checkbox)

        self.detail_container = QWidget()
        detail_layout = QVBoxLayout(self.detail_container)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        fetch_row = QHBoxLayout()
        self.fetch_formats_btn = QPushButton("フォーマット取得")
        self.fetch_formats_btn.setEnabled(False)
        self.fetch_formats_btn.clicked.connect(self.fetch_formats)
        fetch_row.addWidget(self.fetch_formats_btn)
        fetch_row.addStretch(1)
        detail_layout.addLayout(fetch_row)

        info_row = QHBoxLayout()
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(160, 90)
        self.thumbnail_label.setScaledContents(True)
        self.thumbnail_label.setStyleSheet("background-color: rgba(128, 128, 128, 40);")
        info_row.addWidget(self.thumbnail_label)
        self.title_label = QLabel("")
        self.title_label.setWordWrap(True)
        info_row.addWidget(self.title_label, stretch=1)
        detail_layout.addLayout(info_row)

        video_row = QHBoxLayout()
        video_row.addWidget(QLabel("動画フォーマット:"))
        self.video_format_combo = QComboBox()
        self.video_format_combo.setEnabled(False)
        self.video_format_combo.currentIndexChanged.connect(self.on_detail_selection_changed)
        video_row.addWidget(self.video_format_combo, stretch=1)
        detail_layout.addLayout(video_row)

        audio_row = QHBoxLayout()
        audio_row.addWidget(QLabel("音声フォーマット:"))
        self.audio_format_combo = QComboBox()
        self.audio_format_combo.setEnabled(False)
        self.audio_format_combo.currentIndexChanged.connect(self.on_detail_selection_changed)
        audio_row.addWidget(self.audio_format_combo, stretch=1)
        detail_layout.addLayout(audio_row)

        self.merge_note_label = QLabel("")
        detail_layout.addWidget(self.merge_note_label)

        self.mp3_checkbox = QCheckBox("音声のみのダウンロードの場合、mp3に変換する")
        self.mp3_checkbox.setEnabled(False)
        detail_layout.addWidget(self.mp3_checkbox)

        layout.addWidget(self.detail_container)
        self.detail_container.setVisible(False)

        btn_row = QHBoxLayout()
        self.download_btn = QPushButton("ダウンロード開始")
        self.download_btn.clicked.connect(self.start_download)
        btn_row.addWidget(self.download_btn)
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

        self.status_label = QLabel("待機中")
        layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

        self.input_widgets = [
            self.url_edit,
            self.paste_btn,
            self.out_edit,
            self.browse_btn,
            self.format_combo,
            self.detail_checkbox,
            self.fetch_formats_btn,
            self.video_format_combo,
            self.audio_format_combo,
            self.mp3_checkbox,
        ]

        self.auto_paste_from_clipboard()

    def on_url_changed(self, text: str):
        self.fetch_formats_btn.setEnabled(bool(text.strip()) and self.detail_checkbox.isChecked())

    def on_detail_toggled(self, checked: bool):
        self.simple_format_container.setVisible(not checked)
        self.detail_container.setVisible(checked)
        self.format_combo.setEnabled(not checked)
        self.fetch_formats_btn.setEnabled(checked and bool(self.url_edit.text().strip()))
        has_items = self.video_format_combo.count() > 0
        self.video_format_combo.setEnabled(checked and has_items)
        self.audio_format_combo.setEnabled(checked and has_items)
        self.on_detail_selection_changed()

    def paste_from_clipboard(self):
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)

    def auto_paste_from_clipboard(self):
        text = QApplication.clipboard().text().strip()
        if text.startswith("http://") or text.startswith("https://"):
            self.url_edit.setText(text)

    def fetch_formats(self):
        url = self.url_edit.text().strip()
        if not url:
            return

        self.fetch_formats_btn.setEnabled(False)
        self.video_format_combo.clear()
        self.audio_format_combo.clear()
        self.video_format_combo.setEnabled(False)
        self.audio_format_combo.setEnabled(False)
        self.title_label.setText("")
        self.thumbnail_label.clear()
        self.status_label.setText("フォーマット一覧を取得中...")

        self.format_worker = FormatListWorker(url)
        self.format_worker.finished_ok.connect(self.on_formats_fetched)
        self.format_worker.finished_error.connect(self.on_formats_error)
        self.format_worker.start()

    def on_formats_fetched(self, formats: list, title: str, thumbnail_bytes: bytes):
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
                self.video_format_combo.addItem(describe_format(fmt), userData=fmt)
                video_count += 1
            elif has_audio:
                self.audio_format_combo.addItem(describe_format(fmt), userData=fmt)
                audio_count += 1

        self.video_format_combo.setEnabled(True)
        self.audio_format_combo.setEnabled(True)
        self.fetch_formats_btn.setEnabled(True)
        self.on_detail_selection_changed()

        self.title_label.setText(title)
        if thumbnail_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(thumbnail_bytes):
                self.thumbnail_label.setPixmap(pixmap)

        self.status_label.setText(f"動画{video_count}件・音声{audio_count}件のフォーマットを取得しました")

    def on_formats_error(self, message: str):
        self.fetch_formats_btn.setEnabled(True)
        self.status_label.setText("フォーマット取得に失敗しました")
        QMessageBox.critical(self, "フォーマット取得エラー", message)

    def on_detail_selection_changed(self, *_):
        if not self.detail_checkbox.isChecked():
            self.mp3_checkbox.setEnabled(False)
            self.merge_note_label.setText("")
            return

        video_fmt = self.video_format_combo.currentData()
        audio_fmt = self.audio_format_combo.currentData()

        self.mp3_checkbox.setEnabled(video_fmt is None and audio_fmt is not None)

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
            self.on_detail_toggled(self.detail_checkbox.isChecked())

    def append_log(self, msg: str):
        self.log_view.appendPlainText(msg)

    def resolve_format_spec(self) -> tuple[str, list, list | None]:
        if self.detail_checkbox.isChecked():
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
        self.status_label.setText("完了")
        self.set_inputs_enabled(True)
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(True)
        self.progress_bar.setValue(100)

    def on_finished_error(self, message: str):
        self.status_label.setText("エラーまたはキャンセル")
        self.set_inputs_enabled(True)
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        QMessageBox.critical(self, "ダウンロード失敗", message)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
