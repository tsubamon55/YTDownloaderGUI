"""バックグラウンド処理(フォーマット一覧取得・ダウンロード)を行うQThread群"""

import os
import time
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

import yt_dlp
from yt_dlp.postprocessor import FFmpegPostProcessor

from clip_range import clip_range_label
from formats import format_size, is_codec_container_mismatch, protocol_rank
from paths import get_ffmpeg_location, log_debug
from yt_dlp_selection import make_filtering_format_selector


class FormatListWorker(QThread):
    # 4番目の要素(動画の長さ・秒)はNoneを取り得るためobject型で宣言する
    finished_ok = pyqtSignal(list, str, bytes, object)
    finished_error = pyqtSignal(str)

    # 存在しない解像度への推測URL(yt-dlpが実在確認せずに組み立てたもの)に
    # 当たった場合に備え、上位候補を複数試す。多すぎるとタイムアウトが
    # 積み重なるため妥当な件数に制限する。
    MAX_THUMBNAIL_CANDIDATES = 5

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    @staticmethod
    def _thumbnail_url_candidates(info: dict) -> list[str]:
        """画質が高い順にサムネイルURL候補を返す(重複除去済み)"""
        candidates = []
        seen = set()

        thumbnail_url = info.get("thumbnail")
        if thumbnail_url:
            candidates.append(thumbnail_url)
            seen.add(thumbnail_url)

        def sort_key(t):
            return (
                t.get("preference") if t.get("preference") is not None else -1,
                t.get("width") if t.get("width") is not None else -1,
                t.get("height") if t.get("height") is not None else -1,
            )

        thumbnails = info.get("thumbnails") or []
        for t in sorted(thumbnails, key=sort_key, reverse=True):
            url = t.get("url")
            if url and url not in seen:
                seen.add(url)
                candidates.append(url)

        return candidates

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
            formats.sort(
                key=lambda f: (f.get("height") or 0, protocol_rank(f), f.get("tbr") or 0),
                reverse=True,
            )

            title = info.get("title") or ""
            thumbnail_bytes = b""
            candidates = self._thumbnail_url_candidates(info)[: self.MAX_THUMBNAIL_CANDIDATES]
            for candidate_url in candidates:
                try:
                    with urllib.request.urlopen(candidate_url, timeout=10) as resp:
                        data = resp.read()
                    if data:
                        thumbnail_bytes = data
                        break
                except Exception as e:
                    log_debug(f"FormatListWorker: サムネイル取得に失敗 ({candidate_url!r}: {e!r})")
                    continue

            self.finished_ok.emit(formats, title, thumbnail_bytes, info.get("duration"))
        except Exception as e:
            self.finished_error.emit(str(e))


class StoryboardFragmentWorker(QThread):
    """クリップ範囲スライダーのドラッグ中プレビュー用に、ストーリーボード
    (シークバー用サムネイル格子)の1枚のスプライト画像を取得する"""

    finished_ok = pyqtSignal(bytes)
    finished_error = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            with urllib.request.urlopen(self.url, timeout=10) as resp:
                data = resp.read()
            self.finished_ok.emit(data)
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
        exclude_mismatched: bool = False,
        start_time: float | None = None,
        end_time: float | None = None,
    ):
        super().__init__()
        self.url = url
        self.out_dir = out_dir
        self.format_spec = format_spec
        self.postprocessors = postprocessors or []
        self.format_sort = format_sort
        self.exclude_mismatched = exclude_mismatched
        self.start_time = start_time
        self.end_time = end_time
        self._is_cancelled = False
        self._logged_format_ids: set[str] = set()
        self._final_filepath: str | None = None
        self._start_time: float | None = None
        self._active_postprocessors: dict[str, int] = {}
        self._unique_title: str | None = None
        self._component_ids: list[str | None] = []
        self._component_weights: list[float] = [1.0]
        self._completed_weight: float = 0.0
        self._current_component_index: int = 0

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
            except OSError as e:
                log_debug(f"_cleanup_leftover_files: {name} の削除に失敗 ({e!r})")

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

    def _init_component_weights(self, probe_info: dict):
        """映像+音声を別々にダウンロードする形式向けに、各コンポーネントの
        推定サイズ比から全体進捗に対する重みを求めておく(サイズ不明な場合は均等割り)"""
        components = probe_info.get("requested_formats") or [probe_info]
        self._component_ids = [c.get("format_id") for c in components]
        sizes = [c.get("filesize") or c.get("filesize_approx") or 0 for c in components]
        total_size = sum(sizes)
        if total_size > 0 and all(sizes):
            self._component_weights = [s / total_size for s in sizes]
        else:
            self._component_weights = [1.0 / len(components) for _ in components]

    @staticmethod
    def _format_eta(seconds: float) -> str:
        if seconds < 0 or seconds != seconds:  # NaN check
            return "--:--"
        seconds = int(seconds)
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

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

            if fmt_id in self._component_ids:
                component_index = self._component_ids.index(fmt_id)
            else:
                component_index = self._current_component_index
            component_weight = (
                self._component_weights[component_index]
                if component_index < len(self._component_weights)
                else 0.0
            )

            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            component_percent = downloaded / total if total else 0.0
            percent = min((self._completed_weight + component_weight * component_percent) * 100, 100.0)

            # コンポーネント切り替え時に残り時間表示が乱高下しないよう、
            # 全体の経過時間と進捗率から残り時間を推定する
            elapsed = time.monotonic() - self._start_time if self._start_time else 0.0
            if percent > 0:
                eta = self._format_eta(elapsed * (100 - percent) / percent)
            else:
                eta = "--:--"
            speed = d.get("_speed_str", "").strip()
            self.progress.emit(percent, f"{percent:.1f}% 速度:{speed} 残り:{eta}")
        elif status == "finished":
            filename = d.get("filename")
            if filename:
                self.log.emit(f"コンポーネントのダウンロード完了: {os.path.basename(filename)}")
            info = d.get("info_dict") or {}
            fmt_id = info.get("format_id")
            if fmt_id in self._component_ids:
                component_index = self._component_ids.index(fmt_id)
            else:
                component_index = self._current_component_index
            if component_index < len(self._component_weights):
                self._completed_weight += self._component_weights[component_index]
            self._current_component_index = component_index + 1

            if self._current_component_index >= len(self._component_weights):
                self.progress.emit(100.0, "ダウンロード完了、後処理中...")
            else:
                self.progress.emit(min(self._completed_weight * 100, 100.0), "次のコンポーネントを準備中...")

    THUMBNAIL_EMBEDDABLE_EXTS = {
        "mp3", "mkv", "mka", "ogg", "opus", "flac", "m4a", "mp4", "m4v", "mov",
    }

    # クリップ切り出し時に正確な時刻へ合わせるため再エンコードする映像コーデックと、
    # 元のコーデックに対して体感できる劣化がほぼ出ないCRF値の組(値が小さいほど高品質)。
    # 未対応のコーデック(HEVC/AV1等)はffmpegの既定エンコーダ・画質設定にフォールバックする
    _CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX = {
        "avc1": ("libx264", "18"),
        "h264": ("libx264", "18"),
        "vp9": ("libvpx-vp9", "31"),
        "vp09": ("libvpx-vp9", "31"),
        "vp8": ("libvpx", "10"),
        "vp08": ("libvpx", "10"),
    }

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

    def _expected_ext(self, probe_info: dict) -> str | None:
        for pp in self.postprocessors:
            if pp.get("key") != "FFmpegExtractAudio":
                continue
            preferred = pp.get("preferredcodec")
            # "best"(未指定込み)は元の音声コーデックによって最終拡張子が変わる
            # (既に音声のみ・良コーデックならffmpegはスキップし元の拡張子のまま)ため、
            # 確定拡張子とはみなさずprobe_infoの拡張子をそのまま採用する
            if preferred and preferred != "best":
                return preferred
        ext = probe_info.get("ext")
        if ext == "webm" and probe_info.get("requested_formats") and probe_info.get("thumbnails"):
            # 映像+音声の結合時、サムネイル埋め込み(EmbedThumbnail)のためyt-dlpがwebmをmkvへ切り替える
            ext = "mkv"
        return ext

    def _build_title(self, probe_info: dict) -> str:
        """フル動画のダウンロードと保存先ファイルが混同されないよう、クリップ範囲を
        指定した場合はタイトルに範囲を付記する(例: "Title [1:00-2:00]")"""
        title = probe_info.get("title") or "video"
        clip_label = clip_range_label(self.start_time, self.end_time)
        if clip_label:
            title = f"{title} [{clip_label}]"
        return title

    def _resolve_unique_title(self, title: str, expected_ext: str | None) -> str:
        sanitized = yt_dlp.utils.sanitize_filename(title, restricted=False)
        if not os.path.isdir(self.out_dir):
            return sanitized

        def conflicts(stem: str) -> bool:
            if expected_ext is None:
                # 最終拡張子が特定できない場合は、同名の拡張子違いも含めて衝突とみなす
                return any(
                    os.path.splitext(name)[0] == stem for name in os.listdir(self.out_dir)
                )
            return os.path.isfile(os.path.join(self.out_dir, f"{stem}.{expected_ext}"))

        if not conflicts(sanitized):
            return sanitized
        counter = 1
        while True:
            candidate = f"{sanitized} ({counter})"
            if not conflicts(candidate):
                return candidate
            counter += 1

    def _build_format_selector(self):
        """簡易設定ではコンテナ/コーデックが一致しない非推奨フォーマットを候補から
        完全に除外した上でformat_specを解決する。詳細設定でユーザーが明示的にIDを
        指定した場合はexclude_mismatched=Falseとなり、そのまま尊重する。"""
        if not self.exclude_mismatched:
            return self.format_spec
        return make_filtering_format_selector(self.format_spec, is_codec_container_mismatch)

    @staticmethod
    def _selected_vcodec(probe_info: dict) -> str | None:
        """実際にダウンロードした映像コーデックを返す(音声のみの場合はNone)。
        映像+音声を別々にダウンロードした場合はrequested_formatsの中から探す"""
        for fmt in probe_info.get("requested_formats") or [probe_info]:
            vcodec = fmt.get("vcodec")
            if vcodec and vcodec != "none":
                return vcodec
        return None

    def _trim_clip_locally(self, vcodec: str | None = None) -> None:
        """ダウンロード済みファイルをffmpegでローカルに切り出し、self._final_filepathを
        切り出し後のファイルで置き換える。

        yt-dlpのdownload_ranges機能はクリップ区間の有無に関わらずダウンローダを
        ffmpeg直結のFFmpegFDへ強制的に切り替える(yt_dlp.downloader.get_suitable_downloader
        の実装による)。この経路はyt-dlp本来のダウンローダが持つ再接続・スロットリング回避を
        経由しないため、YouTube側のCDNスロットリングに引っかかると進捗が一切報告されないまま
        無期限に停止することがある。そのため範囲指定はダウンローダには渡さず、まず動画全体を
        通常のダウンローダで取得してから、完成したローカルファイルに対してここで切り出す。

        音声は数十ms単位のフレームで独立して切り出せる(キーフレーム制約がない)ため、
        コーデックを問わず常にストリームコピーする(劣化なし)。映像は正確な時刻に合わせる
        ため再エンコードが避けられないので、体感できる劣化がほぼ出ない高めのCRFを使う。
        """
        if not self._final_filepath or not os.path.isfile(self._final_filepath):
            return

        self.log.emit("クリップ範囲を切り出し中...")
        ffpp = FFmpegPostProcessor(downloader=None)
        root, ext = os.path.splitext(self._final_filepath)
        trimmed_path = f"{root}.clip{ext}"

        # -ssを-iより前(入力側)に置くことで、区間の先頭まで一気にシークしてから
        # 必要な範囲だけを再エンコードする(yt-dlpのFFmpegFDが行う高速+正確シークと同じ手法)
        input_opts = ["-ss", str(self.start_time)] if self.start_time else []

        output_opts = ["-c:a", "copy"]
        codec_prefix = (vcodec or "").split(".")[0].lower()
        video_encoder = self._CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX.get(codec_prefix)
        if video_encoder:
            encoder_name, crf = video_encoder
            output_opts += ["-c:v", encoder_name, "-crf", crf]
        if self.end_time is not None:
            output_opts += ["-t", str(self.end_time - (self.start_time or 0))]

        ffpp.real_run_ffmpeg([(self._final_filepath, input_opts)], [(trimmed_path, output_opts)])

        os.replace(trimmed_path, self._final_filepath)
        self.log.emit("切り出し完了")

    def run(self):
        self._start_time = time.monotonic()
        try:
            ffmpeg_location = get_ffmpeg_location()
            if ffmpeg_location:
                # yt-dlpは一部の内部チェック(例: クリップ区間指定時のffmpeg利用可否判定)で
                # ydl_optsのffmpeg_locationを見ずFFmpegPostProcessor()を無引数生成するため、
                # そちらが参照するcontextvarにも明示的に設定しておく
                FFmpegPostProcessor._ffmpeg_location.set(ffmpeg_location)

            self.log.emit(f"開始: {self.url}")
            if self.start_time is not None or self.end_time is not None:
                start_text = self._format_eta(self.start_time) if self.start_time is not None else "先頭"
                end_text = self._format_eta(self.end_time) if self.end_time is not None else "末尾"
                self.log.emit(f"クリップ範囲: {start_text} 〜 {end_text}")

            format_selector = self._build_format_selector()
            probe_opts = {
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "format": format_selector,
            }
            if self.format_sort:
                probe_opts["format_sort"] = self.format_sort
            with yt_dlp.YoutubeDL(probe_opts) as probe_ydl:
                probe_info = probe_ydl.extract_info(self.url, download=False)
            expected_ext = self._expected_ext(probe_info)
            self._unique_title = self._resolve_unique_title(self._build_title(probe_info), expected_ext)
            self.log.emit(f"保存ファイル名(拡張子除く): {self._unique_title}")
            self._init_component_weights(probe_info)

            ydl_opts = {
                "outtmpl": os.path.join(self.out_dir, f"{self._unique_title}.%(ext)s"),
                "progress_hooks": [self._progress_hook],
                "postprocessor_hooks": [self._postprocessor_hook],
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "format": format_selector,
                "writethumbnail": True,
                "postprocessors": list(self.postprocessors),
            }
            if expected_ext in self.THUMBNAIL_EMBEDDABLE_EXTS:
                ydl_opts["postprocessors"].append({"key": "EmbedThumbnail"})
            else:
                ydl_opts["writethumbnail"] = False

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
                if self.start_time is not None or self.end_time is not None:
                    self._trim_clip_locally(self._selected_vcodec(probe_info))
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
