"""バックグラウンド処理(フォーマット一覧取得・ダウンロード)を行うQThread群"""

import os
import re
import shutil
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import yt_dlp
from PyQt6.QtCore import QThread, pyqtSignal
from yt_dlp.postprocessor import FFmpegPostProcessor

from clip_range import clip_range_label, format_clip_time
from clip_trimmer import ClipCancelledError, trim_clip
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
from live import (
    LIVE_ENDED_TEXT,
    LIVE_RECORDING_PERCENT,
    LIVE_SAVING_TEXT,
    CatchUpTracker,
    LiveInfo,
    LiveMode,
    catch_up_text,
    recording_text,
    strip_live_title_suffix,
)
from live_recorder import LiveRecorder, RecordingProgress, live_inputs, recording_extension, remux_recording
from paths import get_ffmpeg_location, hide_path_quietly, log_debug, remove_file_quietly
from yt_dlp_selection import EXCLUDE_FORMATS_PP_KEY, add_format_exclusion


def _base_ydl_opts(format_sort: list[str] | None = None, ffmpeg_location: str | None = None) -> dict[str, Any]:
    """このアプリの全てのYoutubeDL呼び出しに共通するオプション(出力の抑止・プレイリスト展開の無効化)。
    端末から起動した場合にyt-dlpがエラー文へANSIの色コードを埋め込み、それがそのまま
    ダイアログに表示されてしまうため、色付けも無効にする。
    quietだけではライブの録画中の進捗表示が標準出力に出続けるため、noprogressも付ける"""
    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "color": "no_color",
    }
    if format_sort:
        opts["format_sort"] = format_sort
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
    return opts


# サムネイル・ストーリーボードの1枚あたりの上限。通常は数十〜数百KBのため十分な余裕がある。
# 応答が異常に大きい場合に、メモリを使い切るまで読み続けないようにする
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def fetch_image_bytes(url: str, timeout: float, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """http(s)のURLから画像を取得する。抽出結果のURLは動画サイト側の応答に由来するため、
    file:等のローカル資源を読むスキームは受け付けず、max_bytesを超える応答はエラーにする"""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"対応していないURLです: {url[:100]}")
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"画像が大きすぎます(上限{max_bytes}バイト)")
    return data


def _log_traceback(context: str) -> None:
    """ユーザーには要約したメッセージだけを見せる例外について、原因を追えるよう
    トレースバックをcrash.logに残す(except節の中から呼ぶこと)"""
    log_debug(f"{context}\n{traceback.format_exc()}")


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
    # ライブの録画の開始位置。Noneは通常の動画
    live_mode: LiveMode | None = None

    @property
    def has_clip(self) -> bool:
        return self.clip_start is not None or self.clip_end is not None


class FormatListWorker(QThread):
    # 4番目の要素(動画の長さ・秒)と5番目の要素(ライブの情報 LiveInfo)はNoneを取り得るためobject型で宣言する
    finished_ok = pyqtSignal(list, str, bytes, object, object)
    finished_error = pyqtSignal(str)

    UPCOMING_LIVE_ERROR = "配信開始前です。配信が始まってから再度お試しください"
    # 配信開始前のライブに対して、yt-dlpはinfoを返さずにこの文言(英語)を含むエラーを送出することが多い
    _UPCOMING_LIVE_ERROR_MARKER = "live event will begin"

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

    def _extract(self, ffmpeg_location: str | None, live_from_start: bool = False) -> Format:
        ydl_opts = _base_ydl_opts(ffmpeg_location=ffmpeg_location)
        ydl_opts["skip_download"] = True
        if live_from_start:
            ydl_opts["live_from_start"] = True
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(self.url, download=False)

    def _check_live(self, info: Format, ffmpeg_location: str | None) -> tuple[LiveInfo, str]:
        """配信中のライブについて、配信の最初からさかのぼって録画できるかを確かめ、(LiveInfo, タイトル)を返す。

        さかのぼれない配信ではyt-dlpがExtractorErrorを送出する。通信エラー等で確かめられなかった
        場合も、録画自体はできるよう、さかのぼれないものとして扱う。タイトルは、今から録画するための
        抽出でyt-dlpが末尾に付けた日時を除いたものにする"""
        started_at = info.get("release_timestamp")
        fallback_title = strip_live_title_suffix(info.get("title") or "")
        try:
            from_start_info = self._extract(ffmpeg_location, live_from_start=True)
        except Exception as e:
            log_debug(f"FormatListWorker: 配信の最初からは録画できません ({e!r})")
            return LiveInfo(started_at, can_record_from_start=False), fallback_title
        return LiveInfo(started_at, can_record_from_start=True), from_start_info.get("title") or fallback_title

    def run(self) -> None:
        try:
            ffmpeg_location = get_ffmpeg_location()
            info = self._extract(ffmpeg_location)
            if info.get("live_status") == "is_upcoming":
                raise ValueError(self.UPCOMING_LIVE_ERROR)

            title = info.get("title") or ""
            live_info: LiveInfo | None = None
            if info.get("live_status") == "is_live":
                live_info, title = self._check_live(info, ffmpeg_location)

            formats = info.get("formats") or []
            formats = [f for f in formats if f.get("format_id")]
            formats.sort(
                key=lambda f: (f.get("height") or 0, protocol_rank(f), f.get("tbr") or 0),
                reverse=True,
            )

            thumbnail_bytes = b""
            candidates = self._thumbnail_url_candidates(info)[: self.MAX_THUMBNAIL_CANDIDATES]
            for candidate_url in candidates:
                try:
                    data = fetch_image_bytes(candidate_url, self.THUMBNAIL_FETCH_TIMEOUT_SECONDS)
                    if data:
                        thumbnail_bytes = data
                        break
                except Exception as e:
                    log_debug(f"FormatListWorker: サムネイル取得に失敗 ({candidate_url!r}: {e!r})")
                    continue

            self.finished_ok.emit(formats, title, thumbnail_bytes, info.get("duration"), live_info)
        except Exception as e:
            _log_traceback(f"FormatListWorker: 動画情報の取得に失敗 ({self.url!r})")
            if self._UPCOMING_LIVE_ERROR_MARKER in str(e) or str(e) == self.UPCOMING_LIVE_ERROR:
                self.finished_error.emit(self.UPCOMING_LIVE_ERROR)
            else:
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
            data = fetch_image_bytes(self.url, CONFIG.storyboard_fetch_timeout_seconds)
            self.finished_ok.emit(data)
        except Exception as e:
            self.finished_error.emit(describe_error(e, "ストーリーボードの取得"))


class DownloadWorker(QThread):
    progress = pyqtSignal(float, str)
    log = pyqtSignal(str)
    # 保存先へ移した完成品のパス(画面から、そのファイルを選択した状態でフォルダを開くため)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)
    # ユーザーによるキャンセルで止まった。失敗とは区別し、呼び出し側がエラー表示を出さずに済むようにする
    cancelled = pyqtSignal()
    # ライブの録画中ならTrue。停止を受け付けた時・配信が終わった時にFalseを1回だけ送り、
    # 画面はダウンロード一覧の上部への固定を外す
    live_state_changed = pyqtSignal(bool)

    LIVE_NO_DATA_ERROR = "録画できたデータがありません"

    def __init__(self, request: DownloadRequest):
        super().__init__()
        self.request = request
        self._is_cancelled = False
        self._logged_format_ids: set[str] = set()
        self._final_filepath: str | None = None
        self._started_at: float | None = None
        # 残り時間の推定に使う、実際のダウンロード(情報取得の後)が始まった時刻
        self._download_started_at: float | None = None
        self._active_postprocessors: dict[str, int] = {}
        # 保存ファイル名(拡張子除く)。作業フォルダの中は空なので、重複番号は保存先へ移すときに付ける
        self._file_stem: str | None = None
        # このダウンロード専用の作業フォルダ(保存先の中に作る)。完成品だけを保存先へ移し、
        # 残りはフォルダごと消すことで、並列に走る他のダウンロードや既存のファイルに触れない
        self._work_dir: str | None = None
        self._component_ids: list[str | None] = []
        self._component_weights: list[float] = [1.0]
        self._completed_weight: float = 0.0
        self._current_component_index: int = 0
        # ライブの録画の状態(_start_live_stateで設定する)
        self._catch_up: CatchUpTracker | None = None
        self._live_started_at: float | None = None
        self._live_bytes: dict[str, int] = {}
        self._live_ended = False

    # 作業フォルダ名の接頭辞。アプリが強制終了して残った場合でも、何のフォルダか見分けられるようにする
    # TODO: アプリのクラッシュ・強制終了時は_discard_work_dirが走らず、作業フォルダが保存先に残る。
    #   起動時等に古い作業フォルダを片付ける処理が必要。ただし同じ保存先を別のインスタンスが
    #   使用中の可能性があるため、使用中かどうかを判別できる仕組み(ロックファイル等)と合わせて実装すること
    #   (READMEの「既知の問題」にも記載)
    WORK_DIR_PREFIX = ".ytdl-working-"

    # yt-dlpが途中経過として作る未完成・中間ファイルの名前パターン。ダウンロード中の
    # .part / .ytdl / .temp (分割取得時は .part-Frag12 のように連番が付く)と、
    # 映像・音声を別々に取得したときの結合前ファイル(例: "My Video.f137.mp4")。
    # 完成品の場所が後処理フックから分からなかった場合に、作業フォルダから完成品を探す際に除外する
    _INCOMPLETE_NAME_PATTERN = re.compile(
        r"\.(part|ytdl|temp)(-Frag\d+)?$|\.f\d+\.[^.]+$", re.IGNORECASE
    )

    def cancel(self) -> None:
        """通常の動画では中止、ライブでは録画の停止(それまでの分を保存する)"""
        self._is_cancelled = True

    def _create_work_dir(self) -> None:
        """保存先の中に作業フォルダを作る。最後の移動が同じドライブ内の名前変更で済み、
        大きな動画でもコピーや追加の空き容量が要らないよう、システムの一時フォルダは使わない。
        フォルダ名の一意性はmkdtempが保証するため、並列に走る他のダウンロードとは重ならない"""
        os.makedirs(self.request.out_dir, exist_ok=True)
        self._work_dir = tempfile.mkdtemp(prefix=self.WORK_DIR_PREFIX, dir=self.request.out_dir)
        hide_path_quietly(self._work_dir)

    def _discard_work_dir(self) -> None:
        """作業フォルダを中身ごと削除する。完成品は先に保存先へ移してあるため、残っているのは
        未完成・中間ファイルだけ。消せなくても完了・失敗の通知は止めないよう、例外は投げない"""
        work_dir, self._work_dir = self._work_dir, None
        if work_dir is None:
            return
        shutil.rmtree(work_dir, ignore_errors=True)
        if os.path.exists(work_dir):
            log_debug(f"_discard_work_dir: 作業フォルダを削除できませんでした ({work_dir})")

    def _completed_files(self, allow_guess: bool) -> list[str]:
        """作業フォルダ内の、保存先へ移す完成品。

        基本は後処理フックで分かった最終ファイル。後処理が1つも走らない形式ではそれが分からないため、
        allow_guess=True(本編・後処理とも成功した場合)に限り、未完成・中間ファイル以外を完成品とみなす。
        失敗時に推測すると、先に書き出されたサムネイル画像等を完成品として残してしまう"""
        if self._work_dir is None:
            return []
        # 保存先へ移した後に失敗した場合、_final_filepathは移動先を指しているため、作業フォルダ内の
        # ものに限る(移動先のファイルを "Title (1)" へもう一度移し直してしまわないように)
        if (
            self._final_filepath
            and os.path.dirname(self._final_filepath) == self._work_dir
            and os.path.isfile(self._final_filepath)
        ):
            return [self._final_filepath]
        if not allow_guess:
            return []
        try:
            names = os.listdir(self._work_dir)
        except OSError as e:
            log_debug(f"_completed_files: 作業フォルダの一覧取得に失敗 ({e!r})")
            return []
        return [
            os.path.join(self._work_dir, name)
            for name in sorted(names)
            if not self._INCOMPLETE_NAME_PATTERN.search(name)
        ]

    def _move_to_output(self, path: str) -> str:
        """作業フォルダの完成品を保存先へ移し、移動先のパスを返す。

        同名のファイルがあれば "Title (1).mp4" のように番号を付ける。名前の確保は空ファイルを
        排他的に作成(O_EXCL)して行うため、同じタイトルの別の動画が同時に完了しても、
        別のアプリのインスタンスと重なっても、既存のファイルを上書きしない"""
        name = os.path.basename(path)
        stem, ext = os.path.splitext(name)
        counter = 0
        while True:
            candidate = name if counter == 0 else f"{stem} ({counter}){ext}"
            dest = os.path.join(self.request.out_dir, candidate)
            try:
                fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                counter += 1
                continue
            os.close(fd)
            break
        try:
            os.replace(path, dest)
        except OSError:
            remove_file_quietly(dest, "_move_to_output")
            raise
        return dest

    def _deliver_completed_files(self, allow_guess: bool) -> list[str]:
        """完成品を保存先へ移し、移動先のパスを返す。最終ファイルの場所も移動先に更新する"""
        delivered = [self._move_to_output(path) for path in self._completed_files(allow_guess)]
        if delivered:
            self._final_filepath = delivered[0]
        return delivered

    def _keep_completed_file(self) -> None:
        """キャンセル時でも、本編も後処理も完成済みであれば(後処理の終わり際のキャンセル)
        保存先へ移して残す。移せなくてもキャンセルの通知は止めない"""
        try:
            delivered = self._deliver_completed_files(allow_guess=False)
        except OSError as e:
            log_debug(f"_keep_completed_file: 完成済みのファイルを保存先へ移せませんでした ({e!r})")
            return
        for path in delivered:
            self.log.emit(f"完成済みのファイルは残しました: {path}")

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
        status = hook_info.get("status")
        if self._is_cancelled:
            if self.request.live_mode is None:
                raise yt_dlp.utils.DownloadError("ユーザーによりキャンセルされました")
            if status == "downloading":
                # ライブの停止は失敗ではない。yt-dlpはKeyboardInterruptをライブの正常な終わりとして扱い、
                # それまでの分の結合等の後処理まで行う。映像・音声の両スレッドをそれぞれ止めるため、
                # 1回きりではなく呼ばれるたびに送出する
                raise KeyboardInterrupt

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

        if self.request.live_mode is not None:
            self._on_live_downloading(fmt_id or "", hook_info)
            return

        component_index = self._component_index(fmt_id)
        component_weight = (
            self._component_weights[component_index]
            if component_index < len(self._component_weights)
            else 0.0
        )

        total = hook_info.get("total_bytes") or hook_info.get("total_bytes_estimate")
        downloaded = hook_info.get("downloaded_bytes") or 0
        # total_bytes_estimateは推定値のため、実際の受信量がそれを上回ることがある。
        # 1を超えると、このコンポーネントの重みを超えて次の分まで進んだ表示になってしまう
        component_percent = min(downloaded / total, 1.0) if total else 0.0
        percent = min((self._completed_weight + component_weight * component_percent) * 100, 100.0)

        # コンポーネント切り替え時に残り時間表示が乱高下しないよう、
        # ダウンロード開始からの経過時間と進捗率から残り時間を推定する。
        # 開始前の情報取得(_probe)の時間を含めると、その分だけ残り時間が長く見積もられる
        now = time.monotonic()
        if self._download_started_at is None:
            self._download_started_at = now
        elapsed = now - self._download_started_at
        eta = self._format_eta(elapsed * (100 - percent) / percent) if percent > 0 else "--:--"
        self.progress.emit(percent, self._format_progress_text(percent, hook_info.get("_speed_str", ""), eta))

    def _start_live_state(self, probe_info: Format) -> None:
        """ライブの録画を始める前に、さかのぼりの進み具合の計算を用意し、画面へ録画中であることを知らせる"""
        components = probe_info.get("requested_formats") or [probe_info]
        self._catch_up = CatchUpTracker(len(components))
        self._live_started_at = probe_info.get("release_timestamp")
        self.live_state_changed.emit(True)

    def _end_live_state(self) -> None:
        """録画が終わった(停止・配信終了)ことを1回だけ知らせ、保存中の表示に切り替える"""
        if self._live_ended:
            return
        self._live_ended = True
        self.progress.emit(LIVE_RECORDING_PERCENT, LIVE_SAVING_TEXT if self._is_cancelled else LIVE_ENDED_TEXT)
        self.live_state_changed.emit(False)

    def _on_live_downloading(self, fmt_id: str, hook_info: dict) -> None:
        """配信の最初からの録画の進捗。追いつくまではさかのぼりの割合、追いついた後は録画時間とサイズを出す"""
        self._live_bytes[fmt_id] = hook_info.get("downloaded_bytes") or 0
        if self._download_started_at is None:
            self._download_started_at = time.monotonic()
        assert self._catch_up is not None  # _start_live_stateで用意済み
        percent = self._catch_up.update(fmt_id, hook_info.get("fragment_index"), hook_info.get("fragment_count"))
        if percent is not None:
            self.progress.emit(percent, catch_up_text(percent))
            return
        self.progress.emit(
            LIVE_RECORDING_PERCENT, recording_text(self._live_recorded_seconds(), sum(self._live_bytes.values()))
        )

    def _live_recorded_seconds(self) -> float:
        """録画できている長さ。配信の最初から録画しているため、配信開始からの経過時間と同じになる"""
        if self._live_started_at is not None:
            return time.time() - self._live_started_at
        assert self._download_started_at is not None
        return time.monotonic() - self._download_started_at

    @staticmethod
    def _format_progress_text(percent: float, speed: str, eta: str) -> str:
        """画面右上の狭い一覧に収まるよう短くした進捗表示(例: "42% 1.20MiB/s 残り01:00")。
        割合は進捗バーでも分かるため整数にし、速度が分からない間("Unknown B/s")は省く"""
        parts = [f"{percent:.0f}%"]
        speed = speed.strip()
        if speed and not speed.startswith("Unknown"):
            parts.append(speed)
        parts.append(f"残り{eta}")
        return " ".join(parts)

    def _on_component_finished(self, hook_info: dict) -> None:
        if self.request.live_mode is not None:
            # ライブでは停止・配信終了のどちらでも、各コンポーネントがここを通ってから結合される
            self._end_live_state()
            return
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

    def _register_format_exclusion(self, ydl: yt_dlp.YoutubeDL) -> None:
        """自動設定ではコンテナ/コーデックが一致しない非推奨フォーマットを、format_specの
        解決より前に候補から完全に除外する。手動設定でユーザーが明示的にIDを
        指定した場合はexclude_mismatched=Falseとなり、そのまま尊重する。"""
        if self.request.exclude_mismatched:
            add_format_exclusion(ydl, is_codec_container_mismatch)

    def _trim_clip_locally(self, ffmpeg_location: str | None) -> None:
        """ダウンロード済みの最終ファイルを切り抜き範囲で切り出す(詳細はclip_trimmer参照)"""
        trim_clip(
            self._final_filepath,
            self.request.clip_start,
            self.request.clip_end,
            self.log.emit,
            ffmpeg_location=ffmpeg_location,
            is_cancelled=lambda: self._is_cancelled,
        )

    @staticmethod
    def _set_default_ffmpeg_location(ffmpeg_location: str) -> None:
        """yt-dlpは一部の内部処理(外部ダウンローダFFmpegFDの利用可否判定)でydl_optsの
        ffmpeg_locationを見ずにFFmpegPostProcessor()を無引数で生成するため、そちらが参照する
        既定値(yt-dlp自身のCLIも--ffmpeg-locationで設定しているcontextvar)にも設定しておく。
        非公開の属性のため、無くなっていても処理は止めずに記録だけ残す"""
        location_var = getattr(FFmpegPostProcessor, "_ffmpeg_location", None)
        if location_var is None or not hasattr(location_var, "set"):
            log_debug("DownloadWorker: FFmpegPostProcessor._ffmpeg_locationが無いため既定のffmpegの場所を設定できません")
            return
        location_var.set(ffmpeg_location)

    def _raise_if_cancelled(self) -> None:
        if self._is_cancelled:
            raise yt_dlp.utils.DownloadError("ユーザーによりキャンセルされました")

    def run(self) -> None:
        try:
            self._run()
        finally:
            # 通知の前に片付け済みだが、想定外の経路で抜けた場合も作業フォルダを残さない
            self._discard_work_dir()

    def _run(self) -> None:
        self._started_at = time.monotonic()
        ffmpeg_location: str | None = None
        try:
            ffmpeg_location = get_ffmpeg_location()
            if ffmpeg_location:
                self._set_default_ffmpeg_location(ffmpeg_location)

            self._log_request()
            probe_info = self._probe(ffmpeg_location)
            expected_ext = self._prepare_output_name(probe_info)
            # 情報取得(通信)の最中は止められないため、終わった時点でキャンセルを確かめる
            self._raise_if_cancelled()
            self._create_work_dir()

            if self.request.live_mode == "from_now":
                self._record_live_from_now(probe_info, ffmpeg_location)
            else:
                download_opts = self._build_download_opts(expected_ext, ffmpeg_location)
                if self.request.live_mode is not None:
                    self._start_live_state(probe_info)
                with yt_dlp.YoutubeDL(download_opts) as ydl:
                    self._register_format_exclusion(ydl)
                    ydl.download([self.request.url])

            if self.request.live_mode is not None:
                # 停止・配信終了のどちらでも、録画できた分を保存する(完了の経路で終わる)
                self._end_live_state()
                self._finish_success(ffmpeg_location)
            elif self._is_cancelled:
                self._finish_cancelled_after_download()
            else:
                self._finish_success(ffmpeg_location)
        except ClipCancelledError:
            # 切り抜きの途中で止めた場合、残っているのは切り抜く前の動画全体で、
            # 要求された範囲のファイルではないため、完成品として保存先へ移さずに片付ける
            self._finish_with_cleanup(preserve_final=False)
        except KeyboardInterrupt:
            # yt-dlpがライブの停止をライブの正常な終わりとして扱わずにそのまま送出した場合。
            # BaseExceptionのため下のexcept Exceptionでは捕まらず、放っておくとスレッドが例外で落ちて
            # 画面に結果が届かない。結合前の断片しか残っていないため、失敗として知らせる
            self._end_live_state()
            self._finish_with_cleanup(preserve_final=False, error=self.LIVE_NO_DATA_ERROR)
        except Exception as e:
            if self._is_cancelled and self.request.live_mode is None:
                # 後処理の途中で止まった場合も、要求どおりのファイルにはなっていないため残さない
                self._finish_with_cleanup(preserve_final=False)
                return
            _log_traceback(f"DownloadWorker: ダウンロードに失敗 ({self.request.url!r})")
            # ネットワーク切断・その他の失敗いずれの場合も、作業フォルダごと削除する。
            # 本編のダウンロード/結合が済み、サムネイル埋め込み・切り抜き等の後処理だけが
            # 失敗した場合も、要求どおりのファイルにはなっていないため完全な失敗とみなし、
            # 中途半端な完成品は保存先へ移さない
            self._finish_with_cleanup(preserve_final=False, error=describe_error(e, "ダウンロード"))

    def _finish_with_cleanup(self, preserve_final: bool, error: str | None = None) -> None:
        """後片付けをしてから、キャンセル(errorがNone)または失敗を通知する。
        preserve_final=Trueの場合、完成済みの最終ファイルだけは保存先へ移して残す"""
        try:
            if preserve_final:
                self._keep_completed_file()
        finally:
            self._discard_work_dir()
            # 後片付けが想定外に失敗しても、UIがダウンロード中のまま固まらないよう必ず通知する
            if error is None:
                self.log.emit("キャンセルしました")
                self.cancelled.emit()
            else:
                self.log.emit(f"エラー: {error}")
                self.finished_error.emit(error)

    def _log_request(self) -> None:
        self.log.emit(f"開始: {self.request.url}")
        if self.request.has_clip:
            start = self.request.clip_start
            end = self.request.clip_end
            start_text = format_clip_time(start) if start is not None else "先頭"
            end_text = format_clip_time(end) if end is not None else "末尾"
            self.log.emit(f"切り抜き範囲: {start_text} 〜 {end_text}")

    def _probe(self, ffmpeg_location: str | None = None) -> Format:
        """実ダウンロードの前に情報だけを取得し、保存ファイル名・拡張子・進捗の重み付けに使う。
        映像と音声の結合(bv*+ba)を選べるかはffmpegの有無で変わるため、実ダウンロードと
        同じffmpeg_locationを渡して選択結果を揃える"""
        probe_opts = _base_ydl_opts(self.request.format_sort, ffmpeg_location)
        probe_opts["format"] = self.request.format_spec
        if self.request.live_mode == "from_now":
            # 今から録画するライブのHLSは映像のみ・音声のみに分かれ、音声はext=mp4でacodecも不明なため、
            # 形式の指定(ba[ext=m4a]や、映像+音声が1本になったものを選ぶ"/b")のどれにも一致しないことがある。
            # その場合に限り、最良の映像と音声の組み合わせを選ぶ(YouTubeのライブはH.264+AACのためmp4に入る)
            probe_opts["format"] = f"{self.request.format_spec}/bv*+ba"
        if self.request.live_mode == "from_start":
            # 実ダウンロードと同じく、さかのぼり用のフォーマット(DASH)から選ぶ
            probe_opts["live_from_start"] = True
        with yt_dlp.YoutubeDL(probe_opts) as probe_ydl:
            self._register_format_exclusion(probe_ydl)
            return probe_ydl.extract_info(self.request.url, download=False)

    def _prepare_output_name(self, probe_info: Format) -> str | None:
        """保存ファイル名(拡張子除く)を決め、最終拡張子の見込みを返す。作業フォルダの中は空なので
        ここでは重複を気にせず、既存のファイルとの重複は保存先へ移すとき(_move_to_output)に避ける。
        (mp3変換時にyt-dlpが変換前の名前のファイルを「ダウンロード済み」とみなして流用・削除してしまう
        問題も、空の作業フォルダでは起きない)"""
        expected_ext = self._expected_ext(probe_info)
        self._file_stem = yt_dlp.utils.sanitize_filename(self._build_title(probe_info), restricted=False)
        self._init_component_weights(probe_info)
        return expected_ext

    def _build_download_opts(self, expected_ext: str | None, ffmpeg_location: str | None) -> dict:
        assert self._file_stem is not None  # _prepare_output_nameで確定済み
        assert self._work_dir is not None  # _create_work_dirで作成済み
        opts = _base_ydl_opts(self.request.format_sort, ffmpeg_location)
        opts.update({
            # タイトルや保存先の"%("が出力テンプレートとして解釈されないよう、保存先はpathsで
            # 渡し、タイトルの"%"はエスケープする(ずれると保存されるファイル名が変わってしまう)
            "paths": {"home": self._work_dir},
            "outtmpl": f"{self._file_stem.replace('%', '%%')}.%(ext)s",
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "format": self.request.format_spec,
            "writethumbnail": True,
            "postprocessors": list(self.request.postprocessors),
        })
        if self.request.live_mode == "from_start":
            # 区切りの並列取得(concurrent_fragment_downloads)は指定しない。並列にすると
            # 進捗フックからのKeyboardInterruptで止まらなくなり、取得も速くならなかった
            opts["live_from_start"] = True
        if expected_ext in self.THUMBNAIL_EMBEDDABLE_EXTS:
            opts["postprocessors"].append({"key": "EmbedThumbnail"})
        else:
            opts["writethumbnail"] = False
        return opts

    # 今からの録画で、ffmpegが書く途中経過のファイル名(作業フォルダの中)
    _LIVE_RECORDING_NAME = "recording.ts"

    def _record_live_from_now(self, probe_info: Format, ffmpeg_location: str | None) -> None:
        """今からのライブ録画(LiveRecorder)を行い、選ばれた形式のファイルへ入れ替える。
        通信断等でffmpegが途中で止まっても、録画できた分があれば保存する"""
        assert self._work_dir is not None and self._file_stem is not None
        ts_path = os.path.join(self._work_dir, self._LIVE_RECORDING_NAME)
        for fmt in probe_info.get("requested_formats") or [probe_info]:
            self.log.emit(self._describe_selected_format(fmt))
        recorder = LiveRecorder(
            ffmpeg_location,
            live_inputs(probe_info),
            ts_path,
            on_progress=self._on_live_recording_progress,
            should_stop=lambda: self._is_cancelled,
        )
        self._start_live_state(probe_info)
        returncode = recorder.record()
        self._end_live_state()

        if not os.path.isfile(ts_path) or os.path.getsize(ts_path) == 0:
            raise RuntimeError(self.LIVE_NO_DATA_ERROR)
        if returncode != 0 and not self._is_cancelled:
            self.log.emit(f"録画が途中で止まりました(ffmpegの終了コード{returncode})。録画できた分を保存します")

        codec = self._extract_audio_codec()
        output_path = os.path.join(self._work_dir, f"{self._file_stem}.{recording_extension(codec)}")
        remux_recording(ffmpeg_location, ts_path, output_path, codec)
        remove_file_quietly(ts_path, "_record_live_from_now")
        self._final_filepath = output_path

    def _on_live_recording_progress(self, progress: RecordingProgress) -> None:
        self.progress.emit(LIVE_RECORDING_PERCENT, recording_text(progress.seconds, progress.size_bytes))

    def _extract_audio_codec(self) -> str | None:
        """音声のみの形式で指定された、音声を取り出す後処理(FFmpegExtractAudio)の変換先"""
        for pp in self.request.postprocessors:
            if pp.get("key") == "FFmpegExtractAudio":
                return pp.get("preferredcodec") or "best"
        return None

    def _finish_cancelled_after_download(self) -> None:
        # download()が例外を投げずに戻ってきた=本編のダウンロードも後処理も
        # 完了している。後処理中にキャンセルを押した場合がこれにあたるため、
        # 完成済みの最終ファイルは削除せずに残す(未完成の中間ファイルのみ削除)
        self._finish_with_cleanup(preserve_final=True)

    def _finish_success(self, ffmpeg_location: str | None = None) -> None:
        if self.request.has_clip:
            self._trim_clip_locally(ffmpeg_location)
        delivered = self._deliver_completed_files(allow_guess=True)
        if not delivered:
            if self.request.live_mode is not None:
                raise RuntimeError(self.LIVE_NO_DATA_ERROR)
            raise RuntimeError("ダウンロードしたファイルが見つかりませんでした")
        self._discard_work_dir()
        assert self._started_at is not None  # run()の冒頭で設定済み
        elapsed = time.monotonic() - self._started_at
        for path in delivered:
            self.log.emit(f"保存先: {path} ({format_size(os.path.getsize(path))})")
        self.log.emit(f"所要時間: {elapsed:.1f}秒")
        self.log.emit("完了しました")
        self.finished_ok.emit(delivered[0])
