"""バックグラウンド処理(フォーマット一覧取得・ダウンロード)を行うQThread群"""

import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import yt_dlp
from PyQt6.QtCore import QThread, pyqtSignal
from yt_dlp.postprocessor import FFmpegPostProcessor

from clip_range import clip_range_label, format_clip_time
from clip_trimmer import trim_clip
from config import CONFIG
from errors import describe_error
from formats import (
    Format,
    format_filesize,
    format_size,
    has_audio,
    has_video,
    is_codec_container_mismatch,
    protocol_rank,
)
from paths import get_ffmpeg_location, log_debug, remove_file_quietly
from yt_dlp_selection import EXCLUDE_FORMATS_PP_KEY, add_format_exclusion


def _base_ydl_opts(format_sort: list[str] | None = None, ffmpeg_location: str | None = None) -> dict[str, Any]:
    """このアプリの全てのYoutubeDL呼び出しに共通するオプション(出力の抑止・プレイリスト展開の無効化)"""
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if format_sort:
        opts["format_sort"] = format_sort
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
    return opts


@dataclass(frozen=True)
class DownloadRequest:
    """DownloadWorkerに渡す、ダウンロード1件分の指定"""

    url: str
    out_dir: str
    format_spec: str
    postprocessors: list[dict] = field(default_factory=list)
    format_sort: list[str] | None = None
    # 自動設定ではコンテナ/コーデック不一致の非推奨フォーマットを候補から外す
    # (手動設定でユーザーが明示的に選んだIDはそのまま尊重するためFalse)
    exclude_mismatched: bool = False
    # 切り抜き範囲(秒)。Noneは「先頭から」「末尾まで」
    clip_start: float | None = None
    clip_end: float | None = None

    @property
    def has_clip(self) -> bool:
        return self.clip_start is not None or self.clip_end is not None


class FormatListWorker(QThread):
    # 4番目の要素(動画の長さ・秒)はNoneを取り得るためobject型で宣言する
    finished_ok = pyqtSignal(list, str, bytes, object)
    finished_error = pyqtSignal(str)

    # 存在しない解像度への推測URL(yt-dlpが実在確認せずに組み立てたもの)に
    # 当たった場合に備え、上位候補を複数試す。多すぎるとタイムアウトが
    # 積み重なるため妥当な件数に制限する。(config.jsonのthumbnail_max_candidatesで調整可能)
    MAX_THUMBNAIL_CANDIDATES = CONFIG.thumbnail_max_candidates
    # サムネイルはGoogleのCDN(i.ytimg.com)から数十〜数百KB程度の画像を取得するだけの
    # 軽い処理で、正常時は1秒未満で応答が返る。通信が詰まった異常系で1候補あたり
    # 待たされる時間を抑えるため、一般的なWeb APIの目安より短めに設定する
    # (候補は複数回試すため、最悪ケースはこの秒数×MAX_THUMBNAIL_CANDIDATESになる。
    # config.jsonのthumbnail_fetch_timeout_secondsで調整可能)
    THUMBNAIL_FETCH_TIMEOUT_SECONDS = CONFIG.thumbnail_fetch_timeout_seconds

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    @staticmethod
    def _thumbnail_url_candidates(info: Format) -> list[str]:
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

    def run(self) -> None:
        try:
            ydl_opts = _base_ydl_opts(ffmpeg_location=get_ffmpeg_location())
            ydl_opts["skip_download"] = True

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
                    with urllib.request.urlopen(
                        candidate_url, timeout=self.THUMBNAIL_FETCH_TIMEOUT_SECONDS
                    ) as resp:
                        data = resp.read()
                    if data:
                        thumbnail_bytes = data
                        break
                except Exception as e:
                    log_debug(f"FormatListWorker: サムネイル取得に失敗 ({candidate_url!r}: {e!r})")
                    continue

            self.finished_ok.emit(formats, title, thumbnail_bytes, info.get("duration"))
        except Exception as e:
            self.finished_error.emit(describe_error(e, "動画情報の取得"))


class StoryboardFragmentWorker(QThread):
    """クリップ範囲スライダーのドラッグ中プレビュー用に、ストーリーボード
    (シークバー用サムネイル格子)の1枚のスプライト画像を取得する"""

    finished_ok = pyqtSignal(bytes)
    finished_error = pyqtSignal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def run(self) -> None:
        try:
            with urllib.request.urlopen(self.url, timeout=CONFIG.storyboard_fetch_timeout_seconds) as resp:
                data = resp.read()
            self.finished_ok.emit(data)
        except Exception as e:
            self.finished_error.emit(describe_error(e, "ストーリーボードの取得"))


class DownloadWorker(QThread):
    progress = pyqtSignal(float, str)
    log = pyqtSignal(str)
    finished_ok = pyqtSignal()
    finished_error = pyqtSignal(str)

    def __init__(self, request: DownloadRequest):
        super().__init__()
        self.request = request
        self._is_cancelled = False
        self._logged_format_ids: set[str] = set()
        self._final_filepath: str | None = None
        self._started_at: float | None = None
        self._active_postprocessors: dict[str, int] = {}
        self._unique_title: str | None = None
        self._preexisting_names: set[str] = set()
        self._component_ids: list[str | None] = []
        self._component_weights: list[float] = [1.0]
        self._completed_weight: float = 0.0
        self._current_component_index: int = 0

    # yt-dlpが途中経過として作る未完成・中間ファイルの名前パターン。ダウンロード中の
    # .part / .ytdl / .temp (分割取得時は .part-Frag12 のように連番が付く)と、
    # 映像・音声を別々に取得したときの結合前ファイル(例: "My Video.f137.mp4")。
    # これらは完成品ではないため、開始前から残っていても保護対象にはしない
    _INCOMPLETE_NAME_PATTERN = re.compile(
        r"\.(part|ytdl|temp)(-Frag\d+)?$|\.f\d+\.[^.]+$", re.IGNORECASE
    )

    def cancel(self) -> None:
        self._is_cancelled = True

    def _snapshot_preexisting_files(self) -> None:
        """ダウンロード開始前から保存先に存在していた、同名(拡張子違いを含む)の
        完成済みファイルを記録する。

        _resolve_unique_titleは最終拡張子が特定できる場合「同じ拡張子」しか衝突と
        みなさないため、例えば既に My Video.mp3 がある状態で同じ動画をmp4で落とすと
        タイトルは (1) も付かず My Video のままになる。この状態で失敗すると
        _cleanup_leftover_filesの前方一致が無関係な過去の完成ファイルまで巻き込んで
        しまうため、ここで除外対象を控えておく。ただし .part 等の未完成ファイルは
        前回の失敗の残骸であり今回も消してよいので、保護対象から外す。
        """
        self._preexisting_names = set()
        if not self._unique_title or not os.path.isdir(self.request.out_dir):
            return
        prefix = f"{self._unique_title}."
        try:
            names = os.listdir(self.request.out_dir)
        except OSError as e:
            log_debug(f"_snapshot_preexisting_files: 保存先の一覧取得に失敗 ({e!r})")
            return
        self._preexisting_names = {
            os.path.normcase(name)
            for name in names
            if name.startswith(prefix) and not self._INCOMPLETE_NAME_PATTERN.search(name)
        }

    def _cleanup_leftover_files(self, preserve_final: bool = False) -> None:
        """キャンセル時・エラー時に、今回のダウンロードで保存先へ残った未完成ファイル
        (.part等)を削除する。開始前から存在していたファイルは今回の生成物ではないため
        削除しない(_snapshot_preexisting_files参照)。

        preserve_final=Trueの場合、既に完成している最終出力ファイル(self._final_filepath)は
        削除対象から除外する。本編の生成自体は成功し、その後のサムネイル埋め込み等の
        後処理だけが失敗したケースで、完成済みファイルまで消してしまわないようにするため。
        """
        if not self._unique_title or not os.path.isdir(self.request.out_dir):
            return
        prefix = f"{self._unique_title}."
        keep_path = None
        if preserve_final and self._final_filepath and os.path.isfile(self._final_filepath):
            keep_path = os.path.normcase(os.path.abspath(self._final_filepath))
        for name in os.listdir(self.request.out_dir):
            if not name.startswith(prefix):
                continue
            if os.path.normcase(name) in self._preexisting_names:
                continue
            path = os.path.join(self.request.out_dir, name)
            if keep_path and os.path.normcase(os.path.abspath(path)) == keep_path:
                continue
            if remove_file_quietly(path, "_cleanup_leftover_files"):
                self.log.emit(f"未完了ファイルを削除しました: {name}")

    @staticmethod
    def _describe_selected_format(info: Format) -> str:
        vcodec = info.get("vcodec") or "none"
        acodec = info.get("acodec") or "none"
        video = has_video(info)
        audio = has_audio(info)
        kind = "映像+音声" if video and audio else ("映像" if video else "音声")

        parts = [f"使用フォーマット: [{info.get('format_id')}] {kind} ({info.get('ext')})"]
        if video:
            width, height = info.get("width"), info.get("height")
            resolution = info.get("resolution") or (f"{width}x{height}" if width and height else "不明")
            fps = info.get("fps")
            parts.append(f"解像度:{resolution}" + (f" {fps}fps" if fps else ""))
            parts.append(f"映像コーデック:{vcodec}")
        if audio:
            abr = info.get("abr")
            parts.append(f"音声コーデック:{acodec}" + (f" 約{round(abr)}kbps" if abr else ""))

        size = format_filesize(info)
        if size:
            parts.append(f"サイズ:{format_size(size)}")

        return " / ".join(parts)

    def _init_component_weights(self, probe_info: Format) -> None:
        """映像+音声を別々にダウンロードする形式向けに、各コンポーネントの
        推定サイズ比から全体進捗に対する重みを求めておく(サイズ不明な場合は均等割り)"""
        components = probe_info.get("requested_formats") or [probe_info]
        self._component_ids = [c.get("format_id") for c in components]
        sizes = [format_filesize(c) or 0 for c in components]
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

    def _component_index(self, fmt_id: str | None) -> int:
        """進捗フックが報告しているのが何番目のコンポーネント(映像/音声)かを求める"""
        if fmt_id in self._component_ids:
            return self._component_ids.index(fmt_id)
        return self._current_component_index

    def _progress_hook(self, hook_info: dict) -> None:
        if self._is_cancelled:
            raise yt_dlp.utils.DownloadError("ユーザーによりキャンセルされました")

        status = hook_info.get("status")
        if status == "downloading":
            self._on_component_downloading(hook_info)
        elif status == "finished":
            self._on_component_finished(hook_info)

    def _on_component_downloading(self, hook_info: dict) -> None:
        info = hook_info.get("info_dict") or {}
        fmt_id = info.get("format_id")
        if fmt_id and fmt_id not in self._logged_format_ids:
            self._logged_format_ids.add(fmt_id)
            self.log.emit(self._describe_selected_format(info))

        component_index = self._component_index(fmt_id)
        component_weight = (
            self._component_weights[component_index]
            if component_index < len(self._component_weights)
            else 0.0
        )

        total = hook_info.get("total_bytes") or hook_info.get("total_bytes_estimate")
        downloaded = hook_info.get("downloaded_bytes", 0)
        component_percent = downloaded / total if total else 0.0
        percent = min((self._completed_weight + component_weight * component_percent) * 100, 100.0)

        # コンポーネント切り替え時に残り時間表示が乱高下しないよう、
        # 全体の経過時間と進捗率から残り時間を推定する
        elapsed = time.monotonic() - self._started_at if self._started_at else 0.0
        eta = self._format_eta(elapsed * (100 - percent) / percent) if percent > 0 else "--:--"
        speed = hook_info.get("_speed_str", "").strip()
        self.progress.emit(percent, f"{percent:.1f}% 速度:{speed} 残り:{eta}")

    def _on_component_finished(self, hook_info: dict) -> None:
        filename = hook_info.get("filename")
        if filename:
            self.log.emit(f"コンポーネントのダウンロード完了: {os.path.basename(filename)}")
        info = hook_info.get("info_dict") or {}
        component_index = self._component_index(info.get("format_id"))
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

    def _postprocessor_hook(self, hook_info: dict) -> None:
        status = hook_info.get("status")
        name = hook_info.get("postprocessor", "")
        if name == EXCLUDE_FORMATS_PP_KEY:
            # フォーマット選択前の候補除外(add_format_exclusion)はユーザーから見た後処理ではないため表示しない
            return
        if status == "started":
            count = self._active_postprocessors.get(name, 0)
            self._active_postprocessors[name] = count + 1
            if count == 0:
                self.log.emit(f"後処理開始: {name}")
        elif status == "finished":
            info = hook_info.get("info_dict") or {}
            filepath = info.get("filepath")
            if filepath:
                self._final_filepath = filepath
            count = max(self._active_postprocessors.get(name, 1) - 1, 0)
            self._active_postprocessors[name] = count
            if count == 0:
                self.log.emit(f"後処理完了: {name}")

    def _expected_ext(self, probe_info: Format) -> str | None:
        for pp in self.request.postprocessors:
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

    def _build_title(self, probe_info: Format) -> str:
        """フル動画のダウンロードと保存先ファイルが混同されないよう、クリップ範囲を
        指定した場合はタイトルに範囲を付記する(例: "Title [1:00-2:00]")"""
        title = probe_info.get("title") or "video"
        clip_label = clip_range_label(self.request.clip_start, self.request.clip_end)
        if clip_label:
            title = f"{title} [{clip_label}]"
        return title

    def _resolve_unique_title(
        self, title: str, expected_ext: str | None, source_ext: str | None = None
    ) -> str:
        """source_extは後処理(音声抽出等)で変換される前の拡張子。yt-dlpはその名前の
        ファイルが既にあるとダウンロード済みとみなして変換の入力に使い、変換後に削除して
        しまうため、最終拡張子と同様に衝突とみなす"""
        sanitized = yt_dlp.utils.sanitize_filename(title, restricted=False)
        if not os.path.isdir(self.request.out_dir):
            return sanitized
        exts = {ext for ext in (expected_ext, source_ext) if ext}

        def conflicts(stem: str) -> bool:
            if expected_ext is None:
                # 最終拡張子が特定できない場合は、同名の拡張子違いも含めて衝突とみなす
                return any(
                    os.path.splitext(name)[0] == stem for name in os.listdir(self.request.out_dir)
                )
            return any(
                os.path.isfile(os.path.join(self.request.out_dir, f"{stem}.{ext}")) for ext in exts
            )

        if not conflicts(sanitized):
            return sanitized
        counter = 1
        while True:
            candidate = f"{sanitized} ({counter})"
            if not conflicts(candidate):
                return candidate
            counter += 1

    def _register_format_exclusion(self, ydl: yt_dlp.YoutubeDL) -> None:
        """自動設定ではコンテナ/コーデックが一致しない非推奨フォーマットを、format_specの
        解決より前に候補から完全に除外する。手動設定でユーザーが明示的にIDを
        指定した場合はexclude_mismatched=Falseとなり、そのまま尊重する。"""
        if self.request.exclude_mismatched:
            add_format_exclusion(ydl, is_codec_container_mismatch)

    def _trim_clip_locally(self) -> None:
        """ダウンロード済みの最終ファイルを切り抜き範囲で切り出す(詳細はclip_trimmer参照)"""
        trim_clip(self._final_filepath, self.request.clip_start, self.request.clip_end, self.log.emit)

    def run(self) -> None:
        self._started_at = time.monotonic()
        try:
            ffmpeg_location = get_ffmpeg_location()
            if ffmpeg_location:
                # yt-dlpは一部の内部チェック(例: クリップ区間指定時のffmpeg利用可否判定)で
                # ydl_optsのffmpeg_locationを見ずFFmpegPostProcessor()を無引数生成するため、
                # そちらが参照するcontextvarにも明示的に設定しておく
                FFmpegPostProcessor._ffmpeg_location.set(ffmpeg_location)

            self._log_request()
            probe_info = self._probe()
            expected_ext = self._prepare_output_name(probe_info)

            download_opts = self._build_download_opts(expected_ext, ffmpeg_location)
            with yt_dlp.YoutubeDL(download_opts) as ydl:
                self._register_format_exclusion(ydl)
                ydl.download([self.request.url])

            if self._is_cancelled:
                self._finish_cancelled_after_download()
            else:
                self._finish_success()
        except Exception as e:
            # キャンセル・ネットワーク切断・その他の失敗いずれの場合も、保存先に
            # 中途半端な.part等のファイルが残らないよう必ず削除する。ただし本編の
            # ダウンロード/マージ自体は完了しており、後続の後処理だけが失敗した
            # ケースでは、完成済みファイルは残す
            self._cleanup_leftover_files(preserve_final=True)
            message = str(e) if self._is_cancelled else describe_error(e, "ダウンロード")
            self.log.emit(f"エラー: {message}")
            self.finished_error.emit(message)

    def _log_request(self) -> None:
        self.log.emit(f"開始: {self.request.url}")
        if self.request.has_clip:
            start = self.request.clip_start
            end = self.request.clip_end
            start_text = format_clip_time(start) if start is not None else "先頭"
            end_text = format_clip_time(end) if end is not None else "末尾"
            self.log.emit(f"切り抜き範囲: {start_text} 〜 {end_text}")

    def _probe(self) -> Format:
        """実ダウンロードの前に情報だけを取得し、保存ファイル名・拡張子・進捗の重み付けに使う"""
        probe_opts = _base_ydl_opts(self.request.format_sort)
        probe_opts["format"] = self.request.format_spec
        with yt_dlp.YoutubeDL(probe_opts) as probe_ydl:
            self._register_format_exclusion(probe_ydl)
            return probe_ydl.extract_info(self.request.url, download=False)

    def _prepare_output_name(self, probe_info: Format) -> str | None:
        """保存ファイル名(拡張子除く)を確定し、失敗時の後片付けの準備をする。最終拡張子の見込みを返す"""
        expected_ext = self._expected_ext(probe_info)
        self._unique_title = self._resolve_unique_title(
            self._build_title(probe_info), expected_ext, probe_info.get("ext")
        )
        self.log.emit(f"保存ファイル名(拡張子除く): {self._unique_title}")
        self._snapshot_preexisting_files()
        self._init_component_weights(probe_info)
        return expected_ext

    def _build_download_opts(self, expected_ext: str | None, ffmpeg_location: str | None) -> dict:
        assert self._unique_title is not None  # _prepare_output_nameで確定済み
        opts = _base_ydl_opts(self.request.format_sort, ffmpeg_location)
        opts.update({
            # タイトルや保存先の"%("が出力テンプレートとして解釈されないよう、保存先はpathsで
            # 渡し、タイトルの"%"はエスケープする(ずれると衝突判定・後片付けが効かなくなる)
            "paths": {"home": self.request.out_dir},
            "outtmpl": f"{self._unique_title.replace('%', '%%')}.%(ext)s",
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "format": self.request.format_spec,
            "writethumbnail": True,
            "postprocessors": list(self.request.postprocessors),
        })
        if expected_ext in self.THUMBNAIL_EMBEDDABLE_EXTS:
            opts["postprocessors"].append({"key": "EmbedThumbnail"})
        else:
            opts["writethumbnail"] = False
        return opts

    def _finish_cancelled_after_download(self) -> None:
        # download()が例外を投げずに戻ってきた=本編のダウンロードも後処理も
        # 完了している。後処理中にキャンセルを押した場合がこれにあたるため、
        # 完成済みの最終ファイルは削除せずに残す(未完成の中間ファイルのみ削除)
        self._cleanup_leftover_files(preserve_final=True)
        if self._final_filepath and os.path.isfile(self._final_filepath):
            self.log.emit(f"完成済みのファイルは残しました: {self._final_filepath}")
        self.finished_error.emit("キャンセルされました")

    def _finish_success(self) -> None:
        if self.request.has_clip:
            self._trim_clip_locally()
        assert self._started_at is not None  # run()の冒頭で設定済み
        elapsed = time.monotonic() - self._started_at
        if self._final_filepath and os.path.isfile(self._final_filepath):
            size = format_size(os.path.getsize(self._final_filepath))
            self.log.emit(f"保存先: {self._final_filepath} ({size})")
        self.log.emit(f"所要時間: {elapsed:.1f}秒")
        self.log.emit("完了しました")
        self.finished_ok.emit()
