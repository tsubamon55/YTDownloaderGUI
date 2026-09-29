"""ダウンロード済みのローカル動画ファイルを、ffmpegで切り抜き範囲に切り出す。

yt-dlpのdownload_ranges機能はクリップ区間の有無に関わらずダウンローダを
ffmpeg直結のFFmpegFDへ強制的に切り替える(yt_dlp.downloader.get_suitable_downloader
の実装による)。この経路はyt-dlp本来のダウンローダが持つ再接続・スロットリング回避を
経由しないため、YouTube側のCDNスロットリングに引っかかると進捗が一切報告されないまま
無期限に停止することがある。そのため範囲指定はダウンローダには渡さず、まず動画全体を
通常のダウンローダで取得してから、完成したローカルファイルに対してここで切り出す。

Qtには依存しない(進捗はlogコールバックで呼び出し元へ伝える)。
"""

import os
from collections.abc import Callable

from yt_dlp.postprocessor import FFmpegPostProcessor

from config import CONFIG
from formats import codec_prefix
from paths import log_debug, remove_file_quietly

# クリップ切り出し時に正確な時刻へ合わせるため再エンコードする映像コーデックと、
# 元のコーデックに対して体感できる劣化がほぼ出ないCRF値の組(値が小さいほど高品質)。
# 未対応のコーデック(HEVC/AV1等)はffmpegの既定エンコーダ・画質設定にフォールバックする
# (config.jsonのclip_video_encoder_by_codec_prefixで調整可能)
CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX = CONFIG.clip_video_encoder_by_codec_prefix

_ATTACHED_PIC_EXT_BY_CODEC = {"png": "png", "mjpeg": "jpg", "jpeg": "jpg"}


def _is_attached_pic(stream: dict) -> bool:
    """埋め込みサムネイル(disposition=attached_picの映像ストリーム)か"""
    return stream.get("codec_type") == "video" and bool(stream.get("disposition", {}).get("attached_pic"))


def _is_main_video(stream: dict) -> bool:
    """埋め込みサムネイルではない、本編の映像ストリームか"""
    return stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")


def probe_metadata(ffpp: FFmpegPostProcessor, filepath: str) -> dict:
    """ffprobeでファイルを直接調べ、そのメタデータを返す(失敗時は空のメタデータ)。

    probe用に別途取得したextract_info()の結果を使うと、実ダウンロード時の
    フォーマット選択との間に2回のネットワークリクエストの時間差があるため、
    (フォーマットの有効期限切れ等で)実際にダウンロードされた内容とズレる
    可能性がある。確定済みのローカルファイルを直接調べることでそのズレを避ける。

    detect_vcodecとattached_pic_indicesの両方で使う共通の生データを
    1回のffprobe呼び出しで取得するためにまとめてある"""
    try:
        return ffpp.get_metadata_object(filepath)
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
    ffpp: FFmpegPostProcessor, filepath: str, metadata: dict, absolute_indices: list[int]
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
        thumb_path = f"{root}.thumb{idx}.{ext}"
        try:
            ffpp.real_run_ffmpeg(
                [(filepath, [])],
                [(thumb_path, ["-map", f"0:{idx}", "-c", "copy", "-f", "image2", "-update", "1"])],
            )
            extracted.append(thumb_path)
        except Exception as e:
            log_debug(f"extract_attached_pics: サムネイル抽出に失敗 ({e!r})")
    return extracted


def reattach_thumbnails(
    ffpp: FFmpegPostProcessor, video_path: str, video_stream_count: int, thumbnail_paths: list[str]
) -> None:
    """extract_attached_picsで抜き出しておいた画像を、切り抜き後の動画に
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
    try:
        ffpp.real_run_ffmpeg(input_specs, [(merged_path, output_opts)])
        os.replace(merged_path, video_path)
    finally:
        # ffmpegが失敗した場合、部分的に書き込まれた中間ファイルが保存先に残る。
        # この経路は呼び出し元で握りつぶされて成功扱い(finished_ok)になり
        # _cleanup_leftover_filesも走らないため、ここで確実に後片付けする
        remove_file_quietly(merged_path, "reattach_thumbnails")


def nearest_keyframe_at_or_before(
    ffpp: FFmpegPostProcessor, filepath: str, stream_index: int, target: float
) -> float:
    """ffprobeで本編映像のキーフレーム時刻を調べ、target秒以前で最も近いものを返す
    (キーフレームが見つからない場合は0.0)。

    入力側の高速-ss(-iより前)は、コンテナのシーク単位(キーフレーム位置。mkv/webmでは
    その位置に基づくクラスタ単位)までしか正確に戻れない。ここで実際に着地する時刻を
    求めておき、trim_clipがその差分だけ出力側でも正確にシークすることで、
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
        log_debug(f"nearest_keyframe_at_or_before: ffprobeでの検出に失敗 ({e!r})")
        return 0.0
    keyframe_times = (
        float(frame["pts_time"])
        for frame in metadata.get("frames", [])
        if frame.get("key_frame") and "pts_time" in frame
    )
    candidates = [t for t in keyframe_times if t <= target]
    return max(candidates) if candidates else 0.0


def _seek_options(
    ffpp: FFmpegPostProcessor, filepath: str, metadata: dict, clip_start: float | None) -> tuple[list[str], list[str]]:
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
        keyframe_time = nearest_keyframe_at_or_before(ffpp, filepath, main_index, clip_start)
    else:
        # 映像ストリームが無い(音声のみ)場合、キーフレーム制約自体が無く
        # 入力側シークだけで十分正確なため、そのまま入力側シークに委ねる
        keyframe_time = clip_start
    remainder = clip_start - keyframe_time
    accurate_seek_opts = ["-ss", str(remainder)] if remainder > 0 else []
    return ["-ss", str(keyframe_time)], accurate_seek_opts


def _trim_output_options(
    metadata: dict, pic_indices: list[int], clip_start: float | None, clip_end: float | None
) -> list[str]:
    """切り抜き本体の出力オプション(出力側の-ssは含まない)。

    -map 0で全ストリーム(本編映像・音声に加えmkvの添付ファイルなど)を出力対象に
    含めつつ、埋め込みサムネイル(attached_pic)だけは-map -0:Nで除外する
    (理由は_extract_attached_pics参照: 出力側の正確シークは低pts(通常0)の
    静止画1コマも問答無用で切り捨ててしまうため、切り抜き本体には含めず
    後段のos.replace後に_reattach_thumbnailsで単純コピーのみで付け直す)。
    音声・添付ファイルは常にコピーする。本編映像(-c:v:0)は、対応コーデックなら
    専用エンコーダ+CRFで、非対応コーデック(HEVC/AV1等)は何も指定せずffmpeg既定の
    エンコーダにフォールバックさせる(ここを"-c copy"にすると非対応コーデック時に
    本編映像までストリームコピーになり、キーフレーム単位でしか正確な時刻に合わせられず
    音声とズレて見えてしまう)
    """
    opts = ["-map", "0"]
    for idx in pic_indices:
        opts += ["-map", f"-0:{idx}"]
    opts += ["-c:a", "copy", "-c:t", "copy"]
    video_encoder = CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX.get(codec_prefix(detect_vcodec(metadata)))
    if video_encoder:
        encoder_name, crf = video_encoder
        opts += ["-c:v:0", encoder_name, "-crf", crf]
    if clip_end is not None:
        opts += ["-t", str(clip_end - (clip_start or 0))]
    return opts


def trim_clip(
    filepath: str | None, clip_start: float | None, clip_end: float | None, log: Callable[[str], None]
) -> None:
    """filepathのファイルを切り抜き範囲で切り出したものに置き換える。

    音声は数十ms単位のフレームで独立して切り出せる(キーフレーム制約がない)ため、
    コーデックを問わず常にストリームコピーする(劣化なし)。映像は正確な時刻に合わせる
    ため再エンコードが避けられないので、体感できる劣化がほぼ出ない高めのCRFを使う。

    切り出し自体に失敗しても、動画全体のダウンロードはすでに成功しているため、
    例外は外へ出さず、切り出し前の全体ファイルをそのまま残す。
    """
    if not filepath or not os.path.isfile(filepath):
        return

    log("切り抜き範囲を切り出し中...")
    ffpp = FFmpegPostProcessor(downloader=None)
    root, ext = os.path.splitext(filepath)
    trimmed_path = f"{root}.clip{ext}"
    thumbnail_paths: list[str] = []

    try:
        metadata = probe_metadata(ffpp, filepath)
        pic_indices = attached_pic_indices(metadata)
        video_stream_count = len(metadata.get("streams", [])) - len(pic_indices)
        if pic_indices:
            thumbnail_paths = extract_attached_pics(ffpp, filepath, metadata, pic_indices)

        input_opts, accurate_seek_opts = _seek_options(ffpp, filepath, metadata, clip_start)
        output_opts = accurate_seek_opts + _trim_output_options(metadata, pic_indices, clip_start, clip_end)
        ffpp.real_run_ffmpeg([(filepath, input_opts)], [(trimmed_path, output_opts)])
        os.replace(trimmed_path, filepath)

        if thumbnail_paths:
            try:
                reattach_thumbnails(ffpp, filepath, video_stream_count, thumbnail_paths)
            except Exception as e:
                log_debug(f"trim_clip: サムネイルの再添付に失敗 ({e!r})")
                log("切り抜きは完了しましたが、サムネイルの再添付に失敗しました")

        log("切り出し完了")
    except Exception as e:
        remove_file_quietly(trimmed_path, "trim_clip")
        log(f"切り抜き範囲の切り出しに失敗したため、動画全体を保存しました: {e}")
    finally:
        for path in thumbnail_paths:
            remove_file_quietly(path, "trim_clip")
