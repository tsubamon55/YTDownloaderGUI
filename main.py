import os
import sys
import traceback

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
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


class DownloadWorker(QThread):
    progress = pyqtSignal(float, str)
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    finished_error = pyqtSignal(str)

    def __init__(self, url: str, out_dir: str, format_label: str):
        super().__init__()
        self.url = url
        self.out_dir = out_dir
        self.format_label = format_label
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
            format_key = FORMAT_OPTIONS[self.format_label]

            ydl_opts = {
                "outtmpl": os.path.join(self.out_dir, "%(title)s.%(ext)s"),
                "progress_hooks": [self._progress_hook],
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }

            ffmpeg_location = get_ffmpeg_location()
            if ffmpeg_location:
                ydl_opts["ffmpeg_location"] = ffmpeg_location

            if format_key == "audio_mp3":
                ydl_opts["format"] = "ba/b"
                ydl_opts["postprocessors"] = [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ]
            elif format_key == "audio_best":
                ydl_opts["format"] = "ba/b"
            else:
                ydl_opts["format"] = format_key

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
        self.resize(640, 420)

        self.worker: DownloadWorker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        url_row.addWidget(self.url_edit)
        layout.addLayout(url_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("保存先:"))
        self.out_edit = QLineEdit(os.path.join(os.path.expanduser("~"), "Downloads"))
        out_row.addWidget(self.out_edit)
        self.browse_btn = QPushButton("参照...")
        self.browse_btn.clicked.connect(self.browse_folder)
        out_row.addWidget(self.browse_btn)
        layout.addLayout(out_row)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("形式:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMAT_OPTIONS.keys())
        fmt_row.addWidget(self.format_combo)
        layout.addLayout(fmt_row)

        btn_row = QHBoxLayout()
        self.download_btn = QPushButton("ダウンロード開始")
        self.download_btn.clicked.connect(self.start_download)
        btn_row.addWidget(self.download_btn)
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.cancel_btn.setEnabled(False)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("待機中")
        layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダを選択", self.out_edit.text())
        if folder:
            self.out_edit.setText(folder)

    def append_log(self, msg: str):
        self.log_view.appendPlainText(msg)

    def start_download(self):
        url = self.url_edit.text().strip()
        out_dir = self.out_edit.text().strip()
        format_label = self.format_combo.currentText()

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
        os.makedirs(out_dir, exist_ok=True)

        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("ダウンロード中...")

        self.worker = DownloadWorker(url, out_dir, format_label)
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
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(100)

    def on_finished_error(self, message: str):
        self.status_label.setText("エラーまたはキャンセル")
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
