"""バックグラウンド処理(フォーマット一覧取得・ダウンロード)を行うQThread群"""

import http.client
import os
import socket
import ssl
import time
import urllib.error
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

import yt_dlp
from yt_dlp.networking.exceptions import TransportError
from yt_dlp.postprocessor import FFmpegPostProcessor

from clip_range import clip_range_label
from config import CONFIG
from formats import format_size, is_codec_container_mismatch, protocol_rank
from paths import get_ffmpeg_location, log_debug
from yt_dlp_selection import make_filtering_format_selector

# ネットワーク切断・タイムアウト等を表す例外型。yt-dlpは内部でurllib/http.client/sslの
# 例外を捕まえてDownloadError等でラップし直すため、直接の型だけでなく原因チェーン
# (__cause__/__context__、およびyt-dlp独自のexc_info属性)も辿って判定する。
# ConnectionErrorは意図的に含めない。そのサブクラスのBrokenPipeErrorは、ffmpeg
# 等のサブプロセスとのパイプが切れた場合(ディスク容量不足やクラッシュ等、
# ネットワークとは無関係のローカル要因)でも発生するため、丸ごと含めると誤診断になる
_NETWORK_ERROR_TYPES = (
    urllib.error.URLError,
    socket.timeout,
    TimeoutError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    http.client.HTTPException,
    ssl.SSLError,
    TransportError,
)


def is_network_error(exc: BaseException) -> bool:
    """例外(またはその原因チェーン)にネットワーク関連の例外が含まれるかを調べる"""
    seen: set[int] = set()
    pending = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, urllib.error.HTTPError):
            # HTTPErrorはURLErrorのサブクラスだが、これはサーバーから正常にHTTP応答が
            # 返ってきた場合(404/403/429等)であり、ネットワーク切断とは別の問題なので除外する
            pass
        elif isinstance(current, _NETWORK_ERROR_TYPES):
            return True
        # yt_dlp.utils.DownloadErrorはsys.exc_info()のタプルを保持しており、
        # 暗黙の例外チェーン(__context__)が働かないケースがあるため明示的にも辿る
        exc_info = getattr(current, "exc_info", None)
        if exc_info and len(exc_info) > 1:
            pending.append(exc_info[1])
        pending.append(current.__cause__)
        pending.append(current.__context__)
    return False


def describe_error(exc: Exception, action: str) -> str:
    """例外からユーザー向けのエラーメッセージを組み立てる。ネットワーク切断が
    原因と判定できる場合は、原因を明示した文言にする(actionは「ダウンロード」
    「動画情報の取得」等、中断された処理を表す名詞)"""
    if is_network_error(exc):
        return (
            f"ネットワーク接続が切断されたため、{action}を中断しました。"
            f"接続を確認してから再度お試しください。(詳細: {exc})"
        )
    return str(exc)


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

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
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

    def _cleanup_leftover_files(self, preserve_final: bool = False):
        """キャンセル時・エラー時にダウンロード先へ残った未完成ファイル(.part等)を削除する。

        preserve_final=Trueの場合、既に完成している最終出力ファイル(self._final_filepath)は
        削除対象から除外する。本編の生成自体は成功し、その後のサムネイル埋め込み等の
        後処理だけが失敗したケースで、完成済みファイルまで消してしまわないようにするため。
        """
        if not self._unique_title or not os.path.isdir(self.out_dir):
            return
        prefix = f"{self._unique_title}."
        keep_path = None
        if preserve_final and self._final_filepath and os.path.isfile(self._final_filepath):
            keep_path = os.path.normcase(os.path.abspath(self._final_filepath))
        for name in os.listdir(self.out_dir):
            if not name.startswith(prefix):
                continue
            path = os.path.join(self.out_dir, name)
            if keep_path and os.path.normcase(os.path.abspath(path)) == keep_path:
                continue
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
    # (config.jsonのclip_video_encoder_by_codec_prefixで調整可能)
    _CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX = CONFIG.clip_video_encoder_by_codec_prefix

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
        """自動設定ではコンテナ/コーデックが一致しない非推奨フォーマットを候補から
        完全に除外した上でformat_specを解決する。手動設定でユーザーが明示的にIDを
        指定した場合はexclude_mismatched=Falseとなり、そのまま尊重する。"""
        if not self.exclude_mismatched:
            return self.format_spec
        return make_filtering_format_selector(self.format_spec, is_codec_container_mismatch)

    @staticmethod
    def _probe_video_streams(ffpp: FFmpegPostProcessor, filepath: str) -> dict:
        """ffprobeでファイルを直接調べ、そのメタデータを返す(失敗時は空のメタデータ)。

        probe用に別途取得したextract_info()の結果を使うと、実ダウンロード時の
        フォーマット選択との間に2回のネットワークリクエストの時間差があるため、
        (フォーマットの有効期限切れ等で)実際にダウンロードされた内容とズレる
        可能性がある。確定済みのローカルファイルを直接調べることでそのズレを避ける。

        _detect_vcodecと_attached_pic_absolute_indicesの両方で使う共通の生データを
        1回のffprobe呼び出しで取得するためにまとめてある"""
        try:
            return ffpp.get_metadata_object(filepath)
        except Exception as e:
            log_debug(f"_probe_video_streams: ffprobeでの検出に失敗 ({e!r})")
            return {}

    @staticmethod
    def _detect_vcodec(metadata: dict) -> str | None:
        """ffprobeのメタデータから、実際に書き出された本編映像のコーデックを返す
        (音声のみの場合はNone)。

        埋め込みサムネイルは別の映像ストリーム(disposition=attached_pic)として
        検出されるため、本編映像のコーデックを正しく判定できるよう除外する"""
        for stream in metadata.get("streams", []):
            if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic"):
                return stream.get("codec_name")
        return None

    @staticmethod
    def _attached_pic_absolute_indices(metadata: dict) -> list[int]:
        """ffprobeのメタデータから、埋め込みサムネイル(disposition=attached_pic)の
        映像ストリームの絶対インデックス(-map/-select_streamsにそのまま渡せる値)を
        全て返す(本編映像は除く。見つからない場合は空リスト)"""
        return [
            i
            for i, stream in enumerate(metadata.get("streams", []))
            if stream.get("codec_type") == "video" and stream.get("disposition", {}).get("attached_pic")
        ]

    _ATTACHED_PIC_EXT_BY_CODEC = {"png": "png", "mjpeg": "jpg", "jpeg": "jpg"}

    def _extract_attached_pics(
        self, ffpp: FFmpegPostProcessor, filepath: str, metadata: dict, absolute_indices: list[int]
    ) -> list[str]:
        """埋め込みサムネイル(attached_pic)を個別の画像ファイルへ抽出する。

        切り抜き処理は出力側で正確な時刻へシークするが、この-ssはストリームコピーする
        全ての出力ストリームに一律で及び、attached_pic(先頭付近の低いpts、通常0の
        1フレームだけの静止画)も本編と無関係に対象時刻より前として切り捨ててしまう
        (ffmpegの単一の出力パイプラインではストリーム毎に-ssの適用有無を選べない)。
        そのため切り抜き本体からはattached_picを除外し、シークの影響を受けない
        単発のffmpeg呼び出しでここで先に画像として抜き出しておき、切り抜き完了後に
        _reattach_thumbnailsで単純に付け直す(yt-dlp本体のEmbedThumbnailPPがmp4/mov等で
        使うffmpegフォールバック手法と同じ、動画+画像を2入力でマージする方式)"""
        root, _ = os.path.splitext(filepath)
        extracted = []
        for idx in absolute_indices:
            codec_name = metadata["streams"][idx].get("codec_name") or ""
            ext = self._ATTACHED_PIC_EXT_BY_CODEC.get(codec_name.lower(), "jpg")
            thumb_path = f"{root}.thumb{idx}.{ext}"
            try:
                ffpp.real_run_ffmpeg(
                    [(filepath, [])],
                    [(thumb_path, ["-map", f"0:{idx}", "-c", "copy", "-f", "image2", "-update", "1"])],
                )
                extracted.append(thumb_path)
            except Exception as e:
                log_debug(f"_extract_attached_pics: サムネイル抽出に失敗 ({e!r})")
        return extracted

    @staticmethod
    def _reattach_thumbnails(
        ffpp: FFmpegPostProcessor, video_path: str, video_stream_count: int, thumbnail_paths: list[str]
    ) -> None:
        """_extract_attached_picsで抜き出しておいた画像を、切り抜き後の動画に
        単純なコピーのみで付け直す(シークを一切伴わないため対象時刻の影響を受けない)。
        video_stream_countは切り抜き後の動画自体が持つ出力ストリーム数(attached_pic除く)で、
        disposition指定に使う出力側の絶対インデックスを組み立てるのに必要"""
        root, ext = os.path.splitext(video_path)
        merged_path = f"{root}.thumbmerge{ext}"
        input_specs = [(video_path, [])] + [(path, []) for path in thumbnail_paths]
        output_opts = ["-map", "0"]
        for i in range(len(thumbnail_paths)):
            output_opts += ["-map", str(i + 1)]
        output_opts += ["-c", "copy"]
        for i in range(len(thumbnail_paths)):
            output_opts += [f"-disposition:{video_stream_count + i}", "attached_pic"]
        ffpp.real_run_ffmpeg(input_specs, [(merged_path, output_opts)])
        os.replace(merged_path, video_path)

    @staticmethod
    def _main_video_stream_absolute_index(metadata: dict) -> int | None:
        """ffprobeのメタデータから、本編映像(埋め込みサムネイルを除く)ストリームの
        絶対インデックス(ffprobeの-select_streamsにそのまま渡せる値)を返す"""
        for i, stream in enumerate(metadata.get("streams", [])):
            if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic"):
                return i
        return None

    @staticmethod
    def _nearest_keyframe_at_or_before(
        ffpp: FFmpegPostProcessor, filepath: str, stream_index: int, target: float
    ) -> float:
        """ffprobeで本編映像のキーフレーム時刻を調べ、target秒以前で最も近いものを返す
        (キーフレームが見つからない場合は0.0)。

        入力側の高速-ss(-iより前)は、コンテナのシーク単位(キーフレーム位置。mkv/webmでは
        その位置に基づくクラスタ単位)までしか正確に戻れない。ここで実際に着地する時刻を
        求めておき、_trim_clip_locallyがその差分だけ出力側でも正確にシークすることで、
        ストリームコピーする音声も目標時刻まで正確に合わせられる(差分を求めず同じ時刻を
        単純に2回指定すると、着地点からさらに丸ごとtarget秒分シークしてしまい動画終盤の
        切り抜きで入力範囲を飛び越え、出力が空になる)。

        skip_frame=nokeyでキーフレームのパケットだけを対象にするため、対象区間を
        フルデコードするより大幅に軽い"""
        try:
            metadata = ffpp.get_metadata_object(
                filepath,
                opts=["-select_streams", str(stream_index), "-skip_frame", "nokey", "-show_frames"],
            )
        except Exception as e:
            log_debug(f"_nearest_keyframe_at_or_before: ffprobeでの検出に失敗 ({e!r})")
            return 0.0
        keyframe_times = (
            float(frame["pts_time"])
            for frame in metadata.get("frames", [])
            if frame.get("key_frame") and "pts_time" in frame
        )
        candidates = [t for t in keyframe_times if t <= target]
        return max(candidates) if candidates else 0.0

    def _trim_clip_locally(self) -> None:
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

        切り出し自体に失敗しても、動画全体のダウンロードはすでに成功しているため、
        ダウンロード全体を失敗扱いにはせず、切り出し前の全体ファイルをそのまま残す。
        """
        if not self._final_filepath or not os.path.isfile(self._final_filepath):
            return

        self.log.emit("切り抜き範囲を切り出し中...")
        ffpp = FFmpegPostProcessor(downloader=None)
        root, ext = os.path.splitext(self._final_filepath)
        trimmed_path = f"{root}.clip{ext}"
        thumbnail_paths: list[str] = []

        try:
            metadata = self._probe_video_streams(ffpp, self._final_filepath)
            vcodec = self._detect_vcodec(metadata)
            attached_pic_indices = self._attached_pic_absolute_indices(metadata)
            video_stream_count = len(metadata.get("streams", [])) - len(attached_pic_indices)

            if attached_pic_indices:
                thumbnail_paths = self._extract_attached_pics(
                    ffpp, self._final_filepath, metadata, attached_pic_indices
                )

            # -ssを-iより前(入力側)に置くことで、区間の先頭まで一気にシークしてから
            # 必要な範囲だけを再エンコードする(yt-dlpのFFmpegFDが行う高速+正確シークと同じ手法)。
            # ただしこの入力側シークはキーフレーム(mkv/webmではその位置に基づくクラスタ単位)
            # までしか正確に戻れず、コンテナによっては本編映像の目標時刻より数秒前の
            # 位置までしか進まない。本編映像は再エンコードのため後段で余剰分が破棄され
            # 正確な時刻に合うが、ストリームコピーする音声はその破棄が効かず、シーク後の
            # 位置からそのままコピーされてしまうため、本編映像より数秒早い音声が出力され
            # ズレて聞こえる。そこで実際に着地するキーフレーム時刻をffprobeで求め、
            # その差分(余り)だけを-iの直後(=output_opts側)に追加の-ssとして指定し、
            # コピーストリームも含めて目標時刻まで正確にシークさせる。ここで単純に同じ
            # 時刻を2回指定してしまうと、着地点からさらに丸ごと目標時刻分だけシークする
            # ことになり、動画終盤の切り抜きで入力範囲を飛び越えて出力が空になる
            input_opts = []
            accurate_seek_opts = []
            if self.start_time:
                main_video_index = self._main_video_stream_absolute_index(metadata)
                if main_video_index is not None:
                    keyframe_time = self._nearest_keyframe_at_or_before(
                        ffpp, self._final_filepath, main_video_index, self.start_time
                    )
                else:
                    # 映像ストリームが無い(音声のみ)場合、キーフレーム制約自体が無く
                    # 入力側シークだけで十分正確なため、そのまま入力側シークに委ねる
                    keyframe_time = self.start_time
                input_opts = ["-ss", str(keyframe_time)]
                remainder = self.start_time - keyframe_time
                if remainder > 0:
                    accurate_seek_opts = ["-ss", str(remainder)]

            # -map 0で全ストリーム(本編映像・音声に加えmkvの添付ファイルなど)を出力対象に
            # 含めつつ、埋め込みサムネイル(attached_pic)だけは-map -0:Nで除外する
            # (理由は_extract_attached_pics参照: 出力側の正確シークは低pts(通常0)の
            # 静止画1コマも問答無用で切り捨ててしまうため、切り抜き本体には含めず
            # 後段のos.replace後に_reattach_thumbnailsで単純コピーのみで付け直す)。
            # 音声・添付ファイルは常にコピーする。本編映像(-c:v:0)は、対応コーデックなら
            # 専用エンコーダ+CRFで、非対応コーデック(HEVC/AV1等)は何も指定せずffmpeg既定の
            # エンコーダにフォールバックさせる(ここを"-c copy"にすると非対応コーデック時に
            # 本編映像までストリームコピーになり、キーフレーム単位でしか正確な時刻に合わせられず
            # 音声とズレて見えてしまう)
            output_opts = accurate_seek_opts + ["-map", "0"]
            for idx in attached_pic_indices:
                output_opts += ["-map", f"-0:{idx}"]
            output_opts += ["-c:a", "copy", "-c:t", "copy"]
            codec_prefix = (vcodec or "").split(".")[0].lower()
            video_encoder = self._CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX.get(codec_prefix)
            if video_encoder:
                encoder_name, crf = video_encoder
                output_opts += ["-c:v:0", encoder_name, "-crf", crf]
            if self.end_time is not None:
                output_opts += ["-t", str(self.end_time - (self.start_time or 0))]

            ffpp.real_run_ffmpeg([(self._final_filepath, input_opts)], [(trimmed_path, output_opts)])
            os.replace(trimmed_path, self._final_filepath)

            if thumbnail_paths:
                try:
                    self._reattach_thumbnails(ffpp, self._final_filepath, video_stream_count, thumbnail_paths)
                except Exception as e:
                    log_debug(f"_trim_clip_locally: サムネイルの再添付に失敗 ({e!r})")
                    self.log.emit("切り抜きは完了しましたが、サムネイルの再添付に失敗しました")

            self.log.emit("切り出し完了")
        except Exception as e:
            if os.path.isfile(trimmed_path):
                try:
                    os.remove(trimmed_path)
                except OSError as cleanup_error:
                    log_debug(f"_trim_clip_locally: 切り出し失敗後の一時ファイル削除に失敗 ({cleanup_error!r})")
            self.log.emit(f"切り抜き範囲の切り出しに失敗したため、動画全体を保存しました: {e}")
        finally:
            for path in thumbnail_paths:
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError as cleanup_error:
                        log_debug(f"_trim_clip_locally: サムネイル一時ファイルの削除に失敗 ({cleanup_error!r})")

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
                self.log.emit(f"切り抜き範囲: {start_text} 〜 {end_text}")

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
                    self._trim_clip_locally()
                elapsed = time.monotonic() - self._start_time
                if self._final_filepath and os.path.isfile(self._final_filepath):
                    size = format_size(os.path.getsize(self._final_filepath))
                    self.log.emit(f"保存先: {self._final_filepath} ({size})")
                self.log.emit(f"所要時間: {elapsed:.1f}秒")
                self.log.emit("完了しました")
                self.finished_ok.emit()
        except Exception as e:
            # キャンセル・ネットワーク切断・その他の失敗いずれの場合も、保存先に
            # 中途半端な.part等のファイルが残らないよう必ず削除する。ただし本編の
            # ダウンロード/マージ自体は完了しており、後続の後処理だけが失敗した
            # ケースでは、完成済みファイルは残す
            self._cleanup_leftover_files(preserve_final=True)
            message = str(e) if self._is_cancelled else describe_error(e, "ダウンロード")
            self.log.emit(f"エラー: {message}")
            self.finished_error.emit(message)
