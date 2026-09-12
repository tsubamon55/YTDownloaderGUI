"""バックグラウンド処理(フォーマット一覧取得・ダウンロード)を行うQThread群"""

import os
import time
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

import yt_dlp

from formats import format_size
from paths import get_ffmpeg_location


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
        self._logged_format_ids: set[str] = set()
        self._final_filepath: str | None = None
        self._start_time: float | None = None
        self._active_postprocessors: dict[str, int] = {}
        self._unique_title: str | None = None

    def cancel(self):
        self._is_cancelled = True

    def _cleanup_leftover_files(self):
        """キャンセル時にダウンロード先へ残った未完成ファイル(.part等)を削除する"""
        if not self._unique_title or not os.path.isdir(self.out_dir):
            return
        prefix = f"{self._unique_title}."
        for name in os.listdir(self.out_dir):
            if not name.startswith(prefix):
                continue
            path = os.path.join(self.out_dir, name)
            try:
                os.remove(path)
                self.log.emit(f"未完了ファイルを削除しました: {name}")
            except OSError:
                pass

    @staticmethod
    def _describe_selected_format(info: dict) -> str:
        vcodec = info.get("vcodec") or "none"
        acodec = info.get("acodec") or "none"
        has_video = vcodec != "none"
        has_audio = acodec != "none"
        kind = "映像+音声" if has_video and has_audio else ("映像" if has_video else "音声")

        parts = [f"使用フォーマット: [{info.get('format_id')}] {kind} ({info.get('ext')})"]
        if has_video:
            width, height = info.get("width"), info.get("height")
            resolution = info.get("resolution") or (f"{width}x{height}" if width and height else "不明")
            fps = info.get("fps")
            parts.append(f"解像度:{resolution}" + (f" {fps}fps" if fps else ""))
            parts.append(f"映像コーデック:{vcodec}")
        if has_audio:
            abr = info.get("abr")
            parts.append(f"音声コーデック:{acodec}" + (f" 約{round(abr)}kbps" if abr else ""))

        size = info.get("filesize") or info.get("filesize_approx")
        if size:
            parts.append(f"サイズ:{format_size(size)}")

        return " / ".join(parts)

    def _progress_hook(self, d):
        if self._is_cancelled:
            raise yt_dlp.utils.DownloadError("ユーザーによりキャンセルされました")

        status = d.get("status")
        if status == "downloading":
            info = d.get("info_dict") or {}
            fmt_id = info.get("format_id")
            if fmt_id and fmt_id not in self._logged_format_ids:
                self._logged_format_ids.add(fmt_id)
                self.log.emit(self._describe_selected_format(info))

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
            filename = d.get("filename")
            if filename:
                self.log.emit(f"コンポーネントのダウンロード完了: {os.path.basename(filename)}")
            self.progress.emit(100.0, "ダウンロード完了、後処理中...")

    def _postprocessor_hook(self, d):
        status = d.get("status")
        name = d.get("postprocessor", "")
        if status == "started":
            count = self._active_postprocessors.get(name, 0)
            self._active_postprocessors[name] = count + 1
            if count == 0:
                self.log.emit(f"後処理開始: {name}")
        elif status == "finished":
            info = d.get("info_dict") or {}
            filepath = info.get("filepath")
            if filepath:
                self._final_filepath = filepath
            count = max(self._active_postprocessors.get(name, 1) - 1, 0)
            self._active_postprocessors[name] = count
            if count == 0:
                self.log.emit(f"後処理完了: {name}")

    def _resolve_unique_title(self, title: str) -> str:
        sanitized = yt_dlp.utils.sanitize_filename(title, restricted=False)
        if not os.path.isdir(self.out_dir):
            return sanitized
        existing_stems = {os.path.splitext(name)[0] for name in os.listdir(self.out_dir)}
        if sanitized not in existing_stems:
            return sanitized
        counter = 1
        while True:
            candidate = f"{sanitized} ({counter})"
            if candidate not in existing_stems:
                return candidate
            counter += 1

    def run(self):
        self._start_time = time.monotonic()
        try:
            ffmpeg_location = get_ffmpeg_location()

            self.log.emit(f"開始: {self.url}")

            with yt_dlp.YoutubeDL(
                {"noplaylist": True, "quiet": True, "no_warnings": True}
            ) as probe_ydl:
                probe_info = probe_ydl.extract_info(self.url, download=False)
            self._unique_title = self._resolve_unique_title(probe_info.get("title") or "video")
            self.log.emit(f"保存ファイル名(拡張子除く): {self._unique_title}")

            ydl_opts = {
                "outtmpl": os.path.join(self.out_dir, f"{self._unique_title}.%(ext)s"),
                "progress_hooks": [self._progress_hook],
                "postprocessor_hooks": [self._postprocessor_hook],
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "format": self.format_spec,
                "writethumbnail": True,
                "postprocessors": [*self.postprocessors, {"key": "EmbedThumbnail"}],
            }

            if self.format_sort:
                ydl_opts["format_sort"] = self.format_sort

            if ffmpeg_location:
                ydl_opts["ffmpeg_location"] = ffmpeg_location

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])

            if self._is_cancelled:
                self._cleanup_leftover_files()
                self.finished_error.emit("キャンセルされました")
            else:
                elapsed = time.monotonic() - self._start_time
                if self._final_filepath and os.path.isfile(self._final_filepath):
                    size = format_size(os.path.getsize(self._final_filepath))
                    self.log.emit(f"保存先: {self._final_filepath} ({size})")
                self.log.emit(f"所要時間: {elapsed:.1f}秒")
                self.log.emit("完了しました")
                self.finished_ok.emit()
        except Exception as e:
            self.log.emit(f"エラー: {e}")
            if self._is_cancelled:
                self._cleanup_leftover_files()
            self.finished_error.emit(str(e))
