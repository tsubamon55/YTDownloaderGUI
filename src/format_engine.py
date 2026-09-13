"""フォーマット選択・解像度比較のビジネスロジック(UIに依存しない純粋関数群)

MainWindowから抜き出した「どのformat_specを使うか」「yt-dlpが実際に何を選ぶか」
「高解像度確認ダイアログに何を表示すべきか」の判断ロジックをまとめる。
QtやQMessageBoxには一切依存しないため、単体テストがそのまま実行できる。
"""

import copy
from dataclasses import dataclass

from formats import (
    BEST_AUDIO_COMPATIBLE_SORT,
    BEST_QUALITY_COMPATIBLE_SORT,
    FORMAT_OPTIONS,
    filter_mismatched_formats,
    format_size,
    format_spec_1080p,
    is_codec_container_mismatch,
)
from yt_dlp_selection import select_formats

MP3_POSTPROCESSOR = {
    "key": "FFmpegExtractAudio",
    "preferredcodec": "mp3",
    "preferredquality": "192",
}


def resolve_format_spec(
    detail_mode: bool,
    video_fmt: dict | None,
    audio_fmt: dict | None,
    mp3_checked: bool,
    format_label: str,
) -> tuple[str, list, list | None]:
    """UIの選択状態からyt-dlpに渡すformat_spec/postprocessors/format_sortを決定する。

    戻り値: (format_spec, postprocessors, format_sort)
    詳細設定で動画・音声のどちらも未選択の場合はValueErrorを送出する。
    """
    if detail_mode:
        if video_fmt is None and audio_fmt is None:
            raise ValueError("動画または音声のフォーマットを選択してください")

        postprocessors = []
        if video_fmt is not None and audio_fmt is not None:
            format_spec = f"{video_fmt['format_id']}+{audio_fmt['format_id']}"
        elif video_fmt is not None:
            format_spec = video_fmt["format_id"]
        else:
            format_spec = audio_fmt["format_id"]
            if mp3_checked:
                postprocessors = [dict(MP3_POSTPROCESSOR)]
        return format_spec, postprocessors, None

    format_key = FORMAT_OPTIONS[format_label]
    if format_key == "audio_mp3":
        return "ba/b", [dict(MP3_POSTPROCESSOR)], None
    if format_key == "audio_m4a":
        # 音声のみに限定できない場合の"ba"フォールバックで動画結合フォーマットが
        # 選ばれてしまう事態に備え、常に音声トラックのみを取り出す後処理を付ける
        # (対象が既に音声のみ・良コーデックならffmpegは何もせずスキップする)
        return (
            "ba[ext=m4a]/ba[acodec^=mp4a]/ba",
            [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}],
            None,
        )
    if format_key == "audio_best":
        # "ba"に一致するフォーマットが無い場合の"/b"フォールバックで動画結合
        # フォーマットが選ばれてしまう事態に備え、音声トラックのみを取り出す
        return (
            "ba/b",
            [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}],
            BEST_AUDIO_COMPATIBLE_SORT,
        )
    if format_label == "動画 (最高画質)":
        return format_key, [], BEST_QUALITY_COMPATIBLE_SORT
    return format_key, [], None


def select_best_format(
    available_formats: list, format_spec: str, format_sort: list | None
) -> dict | None:
    """実際のダウンロード(DownloadWorker)と全く同じformat_spec/format_sortをyt-dlp本体の
    選択エンジンに通し、実際に選ばれるフォーマットを求める(ネットワークアクセスなし)。
    プレビュー用の選択ロジックを独自実装すると、実際のダウンロード結果とズレる恐れがあるため、
    yt-dlpの選択ロジックそのものを再利用して一貫性を保つ。"""
    if not available_formats:
        return None
    formats = filter_mismatched_formats(copy.deepcopy(available_formats))
    try:
        selected = select_formats(formats, format_spec, format_sort)
    except Exception:
        return None
    return selected[0] if selected else None


def estimate_selection_size(selected: dict | None) -> int | None:
    if not selected:
        return None
    total = 0
    for part in selected.get("requested_formats") or [selected]:
        size = part.get("filesize") or part.get("filesize_approx")
        if not size:
            # いずれかの構成要素のサイズが不明な場合、合計値も不正確になるため不明として扱う
            return None
        total += size
    return total


def selection_resolution(selected: dict | None) -> tuple[int, int] | None:
    if not selected:
        return None
    for part in selected.get("requested_formats") or [selected]:
        height = part.get("height")
        width = part.get("width")
        if height:
            return width or 0, height
    return None


def compute_simple_format_note(available_formats: list, format_label: str) -> str:
    """簡易設定の「動画 (最高画質 mp4)」がH.264限定のため本来の最高画質より
    解像度が落ちる場合のみ、その旨を伝える注記文を返す。落ちない場合は空文字。"""
    if not available_formats or format_label != "動画 (最高画質 mp4)":
        return ""

    mp4_selected = select_best_format(available_formats, FORMAT_OPTIONS[format_label], None)
    mp4_resolution = selection_resolution(mp4_selected)

    best_label = "動画 (最高画質)"
    best_selected = select_best_format(available_formats, FORMAT_OPTIONS[best_label], BEST_QUALITY_COMPATIBLE_SORT)
    best_resolution = selection_resolution(best_selected)

    if mp4_resolution is None or best_resolution is None:
        return ""

    _, mp4_height = mp4_resolution
    _, best_height = best_resolution
    if mp4_height < best_height:
        return f"※ 互換性優先のため画質が{mp4_height}pに制限されます(本来の最高画質は{best_height}p)"
    return ""


@dataclass
class HighResolutionPlan:
    """confirm_high_resolution_downloadがダイアログに表示すべき内容の判定結果。

    needs_confirmation が False の場合、ダイアログ自体を出さず"best"を採用してよい。
    """

    needs_confirmation: bool
    message: str = ""
    fallback_spec: str | None = None
    has_fallback: bool = False


def plan_high_resolution_confirmation(
    available_formats: list,
    format_label: str,
    format_spec: str,
    format_sort: list | None,
) -> HighResolutionPlan:
    """簡易設定の最高画質が1920x1080を超える場合に確認が必要かどうかと、
    確認する場合に表示するメッセージ・1080p版のformat_specを判定する。
    縦型動画では width/height が landscape と逆転するため、長辺・短辺で判定する。"""
    best_selected = select_best_format(available_formats, format_spec, format_sort)
    resolution = selection_resolution(best_selected)
    if resolution is None:
        return HighResolutionPlan(needs_confirmation=False)

    width, height = resolution
    long_side, short_side = max(width, height), min(width, height)
    if long_side <= 1920 and short_side <= 1080:
        return HighResolutionPlan(needs_confirmation=False)

    best_size = estimate_selection_size(best_selected)

    # width<=1920/height<=1080のような単純なフィルタでは縦型動画の向きを
    # 判定できないため、実際に選ばれた最高画質フォーマットの向きから判定する
    is_portrait = height > width
    fallback_spec = format_spec_1080p(format_label, is_portrait)
    fallback_selected = select_best_format(available_formats, fallback_spec, format_sort)
    fallback_resolution = selection_resolution(fallback_selected)
    fallback_size = estimate_selection_size(fallback_selected)

    resolution_text = f"{width}x{height}"
    size_text = f"約{format_size(best_size)}" if best_size else "不明"

    message = f"最高画質は {resolution_text}({size_text})です。\n1080pを超える解像度のため、ファイルサイズが大きくなります。"
    has_fallback = fallback_resolution is not None
    if has_fallback:
        fallback_width, fallback_height = fallback_resolution
        fallback_resolution_text = f"{fallback_width}x{fallback_height}"
        fallback_size_text = f"約{format_size(fallback_size)}" if fallback_size else "不明"
        message += f"\n1080pにすると {fallback_resolution_text}({fallback_size_text})になります。"

    return HighResolutionPlan(
        needs_confirmation=True,
        message=message,
        fallback_spec=fallback_spec if has_fallback else None,
        has_fallback=has_fallback,
    )


def mismatched_selected_formats(*formats: dict | None) -> list[dict]:
    """詳細設定で選択中のフォーマットのうち、コンテナ/コーデックが一致しない非推奨のものを返す"""
    return [fmt for fmt in formats if fmt is not None and is_codec_container_mismatch(fmt)]
