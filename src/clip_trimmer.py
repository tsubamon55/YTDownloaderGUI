"""ダウンロード済みのローカル動画ファイルを、ffmpegで切り抜き範囲に切り出す。

yt-dlpのdownload_ranges機能はクリップ区間の有無に関わらずダウンローダを
ffmpeg直結のFFmpegFDへ強制的に切り替える(yt_dlp.downloader.get_suitable_downloader
の実装による)。この経路はyt-dlp本来のダウンローダが持つ再接続・スロットリング回避を
経由しないため、YouTube側のCDNスロットリングに引っかかると進捗が一切報告されないまま
無期限に停止することがある。そのため範囲指定はダウンローダには渡さず、まず動画全体を
通常のダウンローダで取得してから、完成したローカルファイルに対してここで切り出す。

Qtには依存しない(進捗はlogコールバックで呼び出し元へ伝える)。
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence

from yt_dlp.utils import Popen

from config import CONFIG
from formats import codec_prefix
from paths import log_debug, remove_file_quietly

# クリップ切り出し時に正確な時刻へ合わせるため再エンコードする映像コーデック(ffprobeの
# codec_name)ごとの、ffmpegエンコーダと元に対して体感できる劣化がほぼ出ないCRF値の組
# (値が小さいほど高品質。config.jsonのclip_video_encoder_by_codec_prefixで調整可能)
CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX = CONFIG.clip_video_encoder_by_codec_prefix

# 表に無いコーデック(HEVC/AV1等)の再エンコード先。ffmpeg任せにするとwebmではlibvpx-vp9が
# 既定ビットレート(画質指定なし)で使われ大きく劣化するため、どのビルドにも入っている
# エンコーダへ、出力コンテナに入れられるコーデックで明示的に寄せる
_FALLBACK_CODEC_BY_EXT = {".webm": "vp9"}
_FALLBACK_CODEC = "h264"

# CRFだけでは画質が決まらないエンコーダ向けの追加指定。libvpx-vp9は-b:v 0で初めて
# 固定画質モードになり、libvpx(VP8)は-b:vが上限として効くため十分大きくしておく
_ENCODER_EXTRA_OPTIONS = {
    "libvpx-vp9": ["-b:v", "0"],
    "libvpx": ["-b:v", "50M"],
}

# キーフレームを探す際に、目標時刻からどれだけ手前から読み始めるか(秒)。ffprobeは
# 読み始め位置の直前のキーフレームへ戻ってから読むため、間隔がこれより長い動画でも見つかる
_KEYFRAME_SEARCH_WINDOW_SECONDS = 60

_ATTACHED_PIC_EXT_BY_CODEC = {"png": "png", "mjpeg": "jpg", "jpeg": "jpg"}


class ClipCancelledError(Exception):
    """切り抜き処理中にユーザーがキャンセルした"""


def _find_executable(location: str | None, name: str) -> str | None:
    """location(同梱ffmpegのフォルダ等)にあるnameの実行ファイル。無ければPATHから探す"""
    if location:
        candidate = os.path.join(location, name + (".exe" if sys.platform == "win32" else ""))
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(name)


class FFmpegRunner:
    """ffmpeg/ffprobeをキャンセル可能に実行する。

    yt-dlpのFFmpegRunner.real_run_ffmpeg/get_metadata_objectと同じ形で呼べるが、
    あちらは完了まで戻らないため、長い再エンコード中にキャンセル・アプリ終了ができなかった。
    ここでは一定間隔でis_cancelledを確かめ、キャンセルされたらプロセスを止めて
    ClipCancelledErrorを送出する。実行ファイルの場所は呼び出し元が明示的に渡す
    (yt-dlpの非公開のcontextvarに頼らない)"""

    _POLL_SECONDS = 0.2

    def __init__(self, ffmpeg_location: str | None, is_cancelled: Callable[[], bool] | None = None):
        self.executable = _find_executable(ffmpeg_location, "ffmpeg")
        self.probe_executable = _find_executable(ffmpeg_location, "ffprobe")
        self._is_cancelled = is_cancelled or (lambda: False)

    def _run(self, cmd: list[str]) -> tuple[str, str, int]:
        if self._is_cancelled():
            raise ClipCancelledError()
        # yt-dlpのPopenは、Windowsでコンソール窓を出さない設定とPyInstaller環境向けの
        # 環境変数の補正を行う。stdinは閉じておき、ffmpegが入力待ちで止まらないようにする
        with Popen(cmd, text=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            while True:
                try:
                    stdout, stderr = proc.communicate(timeout=self._POLL_SECONDS)
                    return stdout or "", stderr or "", proc.returncode
                except subprocess.TimeoutExpired:
                    if self._is_cancelled():
                        proc.kill()
                        proc.communicate()
                        raise ClipCancelledError() from None

    def real_run_ffmpeg(
        self, input_path_opts: Sequence[tuple[str, list[str]]], output_path_opts: Sequence[tuple[str, list[str]]]
    ) -> str:
        if self.executable is None:
            raise FileNotFoundError("ffmpegが見つかりません")
        cmd = [self.executable, "-y", "-loglevel", "repeat+info"]
        for path, opts in input_path_opts:
            cmd += [*opts, "-i", _file_argument(path)]
        for path, opts in output_path_opts:
            cmd += [*opts, "-movflags", "+faststart", _file_argument(path)]
        _, stderr, returncode = self._run(cmd)
        if returncode != 0:
            lines = stderr.strip().splitlines()
            raise RuntimeError(lines[-1] if lines else f"ffmpegが終了コード{returncode}で失敗しました")
        # yt-dlpと同様、出力の更新日時を入力(ダウンロードしたファイル)に合わせる
        oldest_mtime = min(os.stat(path).st_mtime for path, _ in input_path_opts)
        for path, _ in output_path_opts:
            try:
                os.utime(path, (oldest_mtime, oldest_mtime))
            except OSError as e:
                log_debug(f"FFmpegRunner: {path} の更新日時の設定に失敗 ({e!r})")
        return stderr

    def get_metadata_object(self, path: str, opts: Sequence[str] = ()) -> dict:
        if self.probe_executable is None:
            raise FileNotFoundError("ffprobeが見つかりません")
        cmd = [self.probe_executable, "-hide_banner", "-show_format", "-show_streams", "-print_format", "json"]
        cmd += [*opts, _file_argument(path)]
        stdout, stderr, returncode = self._run(cmd)
        if returncode != 0:
            raise RuntimeError(f"ffprobeが終了コード{returncode}で失敗しました: {stderr.strip()[-500:]}")
        return json.loads(stdout)


def _file_argument(path: str) -> str:
    # ファイル名に":"が含まれてもプロトコル指定と解釈されないよう、yt-dlpと同じく"file:"を付ける
    return f"file:{path}"


def _unused_path(root: str, tag: str, ext: str) -> str:
    """"{root}.{tag}{ext}"形式の中間ファイル名のうち、まだ存在しないものを返す。
    ffmpegは出力先を-yで無確認に上書きし、失敗時の後片付けでも削除するため、
    保存先に偶然同名のユーザーのファイルがあると壊してしまう。その場合は番号を付けて避ける"""
    path = f"{root}.{tag}{ext}"
    counter = 1
    while os.path.exists(path):
        path = f"{root}.{tag}{counter}{ext}"
        counter += 1
    return path


def _is_attached_pic(stream: dict) -> bool:
    """埋め込みサムネイル(disposition=attached_picの映像ストリーム)か"""
    return stream.get("codec_type") == "video" and bool(stream.get("disposition", {}).get("attached_pic"))


def _is_main_video(stream: dict) -> bool:
    """埋め込みサムネイルではない、本編の映像ストリームか"""
    return stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")


def probe_metadata(ffmpeg: FFmpegRunner, filepath: str) -> dict:
    """ffprobeでファイルを直接調べ、そのメタデータを返す(失敗時は空のメタデータ)。

    probe用に別途取得したextract_info()の結果を使うと、実ダウンロード時の
    フォーマット選択との間に2回のネットワークリクエストの時間差があるため、
    (フォーマットの有効期限切れ等で)実際にダウンロードされた内容とズレる
    可能性がある。確定済みのローカルファイルを直接調べることでそのズレを避ける。

    detect_vcodecとattached_pic_indicesの両方で使う共通の生データを
    1回のffprobe呼び出しで取得するためにまとめてある"""
    try:
        return ffmpeg.get_metadata_object(filepath)
    except ClipCancelledError:
        raise
    except Exception as e:
        log_debug(f"probe_metadata: ffprobeでの検出に失敗 ({e!r})")
        return {}


def main_video_stream_index(metadata: dict) -> int | None:
    """ffprobeのメタデータから、本編映像(埋め込みサムネイルを除く)ストリームの
    絶対インデックス(ffprobeの-select_streamsにそのまま渡せる値)を返す"""
    for i, stream in enumerate(metadata.get("streams", [])):
        if _is_main_video(stream):
            return i
    return None


def detect_vcodec(metadata: dict) -> str | None:
    """ffprobeのメタデータから、実際に書き出された本編映像のコーデックを返す
    (音声のみの場合はNone)。

    埋め込みサムネイルは別の映像ストリーム(disposition=attached_pic)として
    検出されるため、本編映像のコーデックを正しく判定できるよう除外する"""
    index = main_video_stream_index(metadata)
    return None if index is None else metadata["streams"][index].get("codec_name")


def attached_pic_indices(metadata: dict) -> list[int]:
    """ffprobeのメタデータから、埋め込みサムネイル(disposition=attached_pic)の
    映像ストリームの絶対インデックス(-map/-select_streamsにそのまま渡せる値)を
    全て返す(本編映像は除く。見つからない場合は空リスト)"""
    return [i for i, stream in enumerate(metadata.get("streams", [])) if _is_attached_pic(stream)]


def extract_attached_pics(
    ffmpeg: FFmpegRunner, filepath: str, metadata: dict, absolute_indices: list[int]
) -> list[str]:
    """埋め込みサムネイル(attached_pic)を個別の画像ファイルへ抽出する。

    切り抜き処理は出力側で正確な時刻へシークするが、この-ssはストリームコピーする
    全ての出力ストリームに一律で及び、attached_pic(先頭付近の低いpts、通常0の
    1フレームだけの静止画)も本編と無関係に対象時刻より前として切り捨ててしまう
    (ffmpegの単一の出力パイプラインではストリーム毎に-ssの適用有無を選べない)。
    そのため切り抜き本体からはattached_picを除外し、シークの影響を受けない
    単発のffmpeg呼び出しでここで先に画像として抜き出しておき、切り抜き完了後に
    reattach_thumbnailsで単純に付け直す(yt-dlp本体のEmbedThumbnailPPがmp4/mov等で
    使うffmpegフォールバック手法と同じ、動画+画像を2入力でマージする方式)"""
    root, _ = os.path.splitext(filepath)
    extracted = []
    for idx in absolute_indices:
        codec_name = metadata["streams"][idx].get("codec_name") or ""
        ext = _ATTACHED_PIC_EXT_BY_CODEC.get(codec_name.lower(), "jpg")
        thumb_path = _unused_path(root, f"thumb{idx}", f".{ext}")
        try:
            ffmpeg.real_run_ffmpeg(
                [(filepath, [])],
                [(thumb_path, ["-map", f"0:{idx}", "-c", "copy", "-f", "image2", "-update", "1"])],
            )
            extracted.append(thumb_path)
        except Exception as e:
            # 途中まで書き込まれた画像が保存先に残らないようにする
            remove_file_quietly(thumb_path, "extract_attached_pics")
            if isinstance(e, ClipCancelledError):
                for path in extracted:
                    remove_file_quietly(path, "extract_attached_pics")
                raise
            log_debug(f"extract_attached_pics: サムネイル抽出に失敗 ({e!r})")
    return extracted


def reattach_thumbnails(
    ffmpeg: FFmpegRunner, video_path: str, output_stream_count: int, thumbnail_paths: list[str]
) -> None:
    """extract_attached_picsで抜き出しておいた画像を、切り抜き後の動画に
    単純なコピーのみで付け直す(シークを一切伴わないため対象時刻の影響を受けない)。
    output_stream_countは切り抜き後の動画自体が持つストリーム数(映像・音声・添付ファイル等の
    全種類。attached_picは除く)で、disposition指定に使う出力側の絶対インデックスを
    組み立てるのに必要"""
    root, ext = os.path.splitext(video_path)
    merged_path = _unused_path(root, "thumbmerge", ext)
    input_specs: list[tuple[str, list[str]]] = [(video_path, [])]
    input_specs += [(path, []) for path in thumbnail_paths]
    output_opts = ["-map", "0"]
    for i in range(len(thumbnail_paths)):
        output_opts += ["-map", str(i + 1)]
    output_opts += ["-c", "copy"]
    for i in range(len(thumbnail_paths)):
        output_opts += [f"-disposition:{output_stream_count + i}", "attached_pic"]
    try:
        ffmpeg.real_run_ffmpeg(input_specs, [(merged_path, output_opts)])
        os.replace(merged_path, video_path)
    finally:
        # ffmpegが失敗した場合、部分的に書き込まれた中間ファイルが保存先に残る。
        # この経路は呼び出し元で握りつぶされて成功扱い(finished_ok)になり
        # _cleanup_leftover_filesも走らないため、ここで確実に後片付けする
        remove_file_quietly(merged_path, "reattach_thumbnails")


def nearest_keyframe_at_or_before(
    ffmpeg: FFmpegRunner, filepath: str, stream_index: int, target: float, start_time: float = 0.0
) -> float:
    """ffprobeで本編映像のキーフレーム時刻を調べ、target秒以前で最も近いものを返す
    (キーフレームが見つからない場合は0.0)。

    入力側の高速-ss(-iより前)は、コンテナのシーク単位(キーフレーム位置。mkv/webmでは
    その位置に基づくクラスタ単位)までしか正確に戻れない。ここで実際に着地する時刻を
    求めておき、trim_clipがその差分だけ出力側でも正確にシークすることで、
    ストリームコピーする音声も目標時刻まで正確に合わせられる(差分を求めず同じ時刻を
    単純に2回指定すると、着地点からさらに丸ごとtarget秒分シークしてしまい動画終盤の
    切り抜きで入力範囲を飛び越え、出力が空になる)。

    target・戻り値はffmpegの-ssと同じくファイルの開始時刻(start_time)からの相対時刻で、
    ffprobeが返すpts_timeは開始時刻を含む絶対時刻のため、start_timeで換算する
    (開始時刻が0でないファイルで換算を怠ると、着地点がその分ずれて音声がずれる)。

    skip_frame=nokeyでキーフレームのパケットだけを対象にし、さらにread_intervalsで
    目標時刻の手前だけを読むため、対象区間やファイル全体をフルデコードするより大幅に軽い"""
    absolute_target = target + start_time
    read_from = max(absolute_target - _KEYFRAME_SEARCH_WINDOW_SECONDS, 0.0)
    try:
        metadata = ffmpeg.get_metadata_object(
            filepath,
            opts=[
                "-select_streams", str(stream_index),
                "-read_intervals", f"{_ffmpeg_time(read_from)}%{_ffmpeg_time(absolute_target + 1)}",
                "-skip_frame", "nokey",
                "-show_frames",
            ],
        )
    except ClipCancelledError:
        raise
    except Exception as e:
        log_debug(f"nearest_keyframe_at_or_before: ffprobeでの検出に失敗 ({e!r})")
        return 0.0
    candidates = []
    for frame in metadata.get("frames", []):
        if not frame.get("key_frame"):
            continue
        try:
            pts_time = float(frame["pts_time"])
        except (KeyError, TypeError, ValueError):
            continue
        if pts_time <= absolute_target:
            candidates.append(pts_time)
    return max(max(candidates) - start_time, 0.0) if candidates else 0.0


def _start_time(metadata: dict) -> float:
    """ファイルの開始時刻(秒)。ffmpegの入力側-ssはこの時刻を起点に数える"""
    try:
        return float(metadata.get("format", {}).get("start_time") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ffmpeg_time(seconds: float) -> str:
    """ffmpegの時刻指定用の文字列。str(float)は1e-4未満で指数表記(9.9e-06等)になり、
    ffmpegが時刻として受け付けないため、固定小数点で表す(末尾の余分な0は落とす)"""
    text = f"{seconds:.6f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def _seek_options(
    ffmpeg: FFmpegRunner, filepath: str, metadata: dict, clip_start: float | None
) -> tuple[list[str], list[str]]:
    """切り抜き開始位置へのシーク指定を (入力側の-ss, 出力側の正確シーク用-ss) で返す。

    -ssを-iより前(入力側)に置くことで、区間の先頭まで一気にシークしてから
    必要な範囲だけを再エンコードする(yt-dlpのFFmpegFDが行う高速+正確シークと同じ手法)。
    ただしこの入力側シークはキーフレーム(mkv/webmではその位置に基づくクラスタ単位)
    までしか正確に戻れず、コンテナによっては本編映像の目標時刻より数秒前の
    位置までしか進まない。本編映像は再エンコードのため後段で余剰分が破棄され
    正確な時刻に合うが、ストリームコピーする音声はその破棄が効かず、シーク後の
    位置からそのままコピーされてしまうため、本編映像より数秒早い音声が出力され
    ズレて聞こえる。そこで実際に着地するキーフレーム時刻をffprobeで求め、
    その差分(余り)だけを-iの直後(=output_opts側)に追加の-ssとして指定し、
    コピーストリームも含めて目標時刻まで正確にシークさせる。ここで単純に同じ
    時刻を2回指定してしまうと、着地点からさらに丸ごと目標時刻分だけシークする
    ことになり、動画終盤の切り抜きで入力範囲を飛び越えて出力が空になる
    """
    if not clip_start:
        return [], []
    main_index = main_video_stream_index(metadata)
    if main_index is not None:
        keyframe_time = nearest_keyframe_at_or_before(ffmpeg, filepath, main_index, clip_start, _start_time(metadata))
    else:
        # 映像ストリームが無い(音声のみ)場合、キーフレーム制約自体が無く
        # 入力側シークだけで十分正確なため、そのまま入力側シークに委ねる
        keyframe_time = clip_start
    remainder = clip_start - keyframe_time
    accurate_seek_opts = ["-ss", _ffmpeg_time(remainder)] if remainder > 0 else []
    return ["-ss", _ffmpeg_time(keyframe_time)], accurate_seek_opts


def _video_encoder_options(metadata: dict, ext: str) -> list[str]:
    """本編映像を再エンコードするエンコーダ・画質の指定(映像が無ければ空)"""
    vcodec = codec_prefix(detect_vcodec(metadata))
    if not vcodec:
        return []
    setting = _encoder_setting(vcodec) or _encoder_setting(_FALLBACK_CODEC_BY_EXT.get(ext.lower(), _FALLBACK_CODEC))
    if setting is None:
        return []
    encoder_name, crf = setting
    return ["-c:v:0", encoder_name, "-crf", crf, *_ENCODER_EXTRA_OPTIONS.get(encoder_name, [])]


def _encoder_setting(codec: str) -> tuple[str, str] | None:
    """config.jsonの値が[エンコーダ名, CRF値]の形でなければ使わない"""
    setting = CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX.get(codec)
    if isinstance(setting, list) and len(setting) == 2 and all(isinstance(v, str) and v for v in setting):
        return setting[0], setting[1]
    if setting is not None:
        log_debug(f"_encoder_setting: {codec!r} の再エンコード設定が不正なため無視します ({setting!r})")
    return None


def _trim_output_options(
    metadata: dict, pic_indices: list[int], clip_start: float | None, clip_end: float | None, ext: str = ""
) -> list[str]:
    """切り抜き本体の出力オプション(出力側の-ssは含まない)。

    -map 0で全ストリーム(本編映像・音声に加えmkvの添付ファイルなど)を出力対象に
    含めつつ、埋め込みサムネイル(attached_pic)だけは-map -0:Nで除外する
    (理由はextract_attached_pics参照: 出力側の正確シークは低pts(通常0)の
    静止画1コマも問答無用で切り捨ててしまうため、切り抜き本体には含めず
    後段のos.replace後にreattach_thumbnailsで単純コピーのみで付け直す)。
    音声・添付ファイルは常にコピーする。本編映像(-c:v:0)は常に再エンコードする
    (ここを"-c copy"にするとキーフレーム単位でしか正確な時刻に合わせられず、
    音声とズレて見えてしまう)。エンコーダは_video_encoder_options参照
    """
    opts = ["-map", "0"]
    for idx in pic_indices:
        opts += ["-map", f"-0:{idx}"]
    opts += ["-c:a", "copy", "-c:t", "copy"]
    opts += _video_encoder_options(metadata, ext)
    if clip_end is not None:
        opts += ["-t", _ffmpeg_time(clip_end - (clip_start or 0))]
    return opts


def trim_clip(
    filepath: str | None,
    clip_start: float | None,
    clip_end: float | None,
    log: Callable[[str], None],
    ffmpeg_location: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    """filepathのファイルを切り抜き範囲で切り出したものに置き換える。

    音声は数十ms単位のフレームで独立して切り出せる(キーフレーム制約がない)ため、
    コーデックを問わず常にストリームコピーする(劣化なし)。映像は正確な時刻に合わせる
    ため再エンコードが避けられないので、体感できる劣化がほぼ出ない高めのCRFを使う。

    切り出し自体に失敗しても、動画全体のダウンロードはすでに成功しているため、
    例外は外へ出さず、切り出し前の全体ファイルをそのまま残す。
    is_cancelledがTrueを返した場合は実行中のffmpegを止め、中間ファイルを片付けてから
    ClipCancelledErrorを送出する(filepathは切り出し前の全体ファイルのまま残る)。
    """
    if not filepath or not os.path.isfile(filepath):
        return

    log("切り抜き範囲を切り出し中...")
    ffmpeg = FFmpegRunner(ffmpeg_location, is_cancelled)
    root, ext = os.path.splitext(filepath)
    trimmed_path = _unused_path(root, "clip", ext)
    thumbnail_paths: list[str] = []

    try:
        metadata = probe_metadata(ffmpeg, filepath)
        pic_indices = attached_pic_indices(metadata)
        output_stream_count = len(metadata.get("streams", [])) - len(pic_indices)
        if pic_indices:
            thumbnail_paths = extract_attached_pics(ffmpeg, filepath, metadata, pic_indices)

        input_opts, accurate_seek_opts = _seek_options(ffmpeg, filepath, metadata, clip_start)
        output_opts = accurate_seek_opts + _trim_output_options(metadata, pic_indices, clip_start, clip_end, ext)
        ffmpeg.real_run_ffmpeg([(filepath, input_opts)], [(trimmed_path, output_opts)])
        os.replace(trimmed_path, filepath)

        if thumbnail_paths:
            try:
                reattach_thumbnails(ffmpeg, filepath, output_stream_count, thumbnail_paths)
            except ClipCancelledError:
                # 切り抜き自体は完了しているため、サムネイルが無いだけの完成品として扱う
                log("サムネイルの再添付はキャンセルされました")
            except Exception as e:
                log_debug(f"trim_clip: サムネイルの再添付に失敗 ({e!r})")
                log("切り抜きは完了しましたが、サムネイルの再添付に失敗しました")

        log("切り出し完了")
    except ClipCancelledError:
        remove_file_quietly(trimmed_path, "trim_clip")
        raise
    except Exception as e:
        remove_file_quietly(trimmed_path, "trim_clip")
        log(f"切り抜き範囲の切り出しに失敗したため、動画全体を保存しました: {e}")
    finally:
        for path in thumbnail_paths:
            remove_file_quietly(path, "trim_clip")
