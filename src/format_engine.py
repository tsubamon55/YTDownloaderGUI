"""フォーマット選択・解像度比較のビジネスロジック(UIに依存しない純粋関数群)

MainWindowから抜き出した「どのformat_specを使うか」「yt-dlpが実際に何を選ぶか」
「高解像度確認ダイアログに何を表示すべきか」の判断ロジックをまとめる。
QtやQMessageBoxには一切依存しないため、単体テストがそのまま実行できる。
"""

import copy
from dataclasses import dataclass
from typing import NamedTuple

from config import CONFIG
from formats import (
    FormatKey,
    filter_mismatched_formats,
    find_format_option,
    format_filesize,
    format_option,
    format_size,
    format_spec_1080p,
    is_codec_container_mismatch,
)
from paths import log_debug
from yt_dlp_selection import select_formats


class FormatSelection(NamedTuple):
    """yt-dlpに渡すフォーマット指定一式"""

    spec: str
    postprocessors: list[dict]
    sort: list[str] | None


def extract_audio_postprocessor(codec: str) -> dict:
    """音声トラックだけを取り出す(必要ならcodecへ変換する)yt-dlpの後処理設定"""
    postprocessor = {"key": "FFmpegExtractAudio", "preferredcodec": codec}
    if codec == "mp3":
        postprocessor["preferredquality"] = CONFIG.mp3_quality
    return postprocessor


def resolve_format_spec(
    manual_mode: bool,
    video_fmt: dict | None,
    audio_fmt: dict | None,
    mp3_checked: bool,
    format_label: str,
) -> FormatSelection:
    """UIの選択状態からyt-dlpに渡すformat_spec/postprocessors/format_sortを決定する。

    手動設定で動画・音声のどちらも未選択の場合、自動設定で未知の形式が指定された場合は
    ValueErrorを送出する。
    """
    if manual_mode:
        return _resolve_manual_selection(video_fmt, audio_fmt, mp3_checked)

    option = find_format_option(format_label)
    if option is None:
        raise ValueError(f"未知の形式です: {format_label}")
    postprocessors = [extract_audio_postprocessor(option.extract_audio_codec)] if option.extract_audio_codec else []
    return FormatSelection(option.spec, postprocessors, option.sort)


def _resolve_manual_selection(video_fmt: dict | None, audio_fmt: dict | None, mp3_checked: bool) -> FormatSelection:
    if video_fmt is None and audio_fmt is None:
        raise ValueError("動画または音声のフォーマットを選択してください")

    if video_fmt is not None and audio_fmt is not None:
        return FormatSelection(f"{video_fmt['format_id']}+{audio_fmt['format_id']}", [], None)
    if video_fmt is not None:
        return FormatSelection(video_fmt["format_id"], [], None)
    postprocessors = [extract_audio_postprocessor("mp3")] if mp3_checked else []
    return FormatSelection(audio_fmt["format_id"], postprocessors, None)


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
    except Exception as e:
        log_debug(f"select_best_format: format_spec={format_spec!r} の選択に失敗 ({e!r})")
        return None
    return selected[0] if selected else None


def estimate_selection_size(selected: dict | None) -> int | None:
    if not selected:
        return None
    total = 0
    for part in selected.get("requested_formats") or [selected]:
        size = format_filesize(part)
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


def compute_auto_format_note(available_formats: list, format_label: str) -> str:
    """自動設定の「動画 (最高画質 mp4)」がH.264限定のため本来の最高画質より
    解像度が落ちる場合のみ、その旨を伝える注記文を返す。落ちない場合は空文字。"""
    option = find_format_option(format_label)
    if not available_formats or option is None or option.key is not FormatKey.VIDEO_BEST_MP4:
        return ""

    mp4_resolution = selection_resolution(select_best_format(available_formats, option.spec, option.sort))
    best = format_option(FormatKey.VIDEO_BEST)
    best_resolution = selection_resolution(select_best_format(available_formats, best.spec, best.sort))

    if mp4_resolution is None or best_resolution is None:
        return ""

    _, mp4_height = mp4_resolution
    _, best_height = best_resolution
    if mp4_height < best_height:
        return f"※ 互換性優先のため画質が{mp4_height}pに制限されます(本来の最高画質は{best_height}p)"
    return ""


@dataclass(frozen=True)
class HighResolutionPlan:
    """confirm_high_resolution_downloadがダイアログに表示すべき内容の判定結果。

    needs_confirmation が False の場合、ダイアログ自体を出さず"best"を採用してよい。
    """

    needs_confirmation: bool
    message: str = ""
    fallback_spec: str | None = None

    @property
    def has_fallback(self) -> bool:
        return self.fallback_spec is not None


def plan_high_resolution_confirmation(
    available_formats: list,
    format_label: str,
    format_spec: str,
    format_sort: list | None,
) -> HighResolutionPlan:
    """自動設定の最高画質が1920x1080を超える場合に確認が必要かどうかと、
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
    )


def mismatched_selected_formats(*formats: dict | None) -> list[dict]:
    """手動設定で選択中のフォーマットのうち、コンテナ/コーデックが一致しない非推奨のものを返す"""
    return [fmt for fmt in formats if fmt is not None and is_codec_container_mismatch(fmt)]
