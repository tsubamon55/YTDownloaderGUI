"""yt-dlpのフォーマット選択に対する接続点をこのファイルに集約する。

プレビュー(format_engine.py)と実ダウンロード(workers.py)で選択結果がズレないよう、
どちらも独自の選択ロジックを持たず、yt-dlpの通常の処理経路と公式の拡張点だけを使う。

- select_formats: 取得済みのformatsだけを持つ最小の動画情報を
  YoutubeDL.process_ie_result(download=False)に通し、実際に選ばれるフォーマットを求める。
- add_format_exclusion: when="pre_process"のポストプロセッサでinfo["formats"]を絞り込み、
  指定したフォーマットをformat_specの解決前に候補から外す。yt-dlpはpre_processの後で
  formatsを読み直してから選択する(process_video_resultの
  "The pre-processors may have modified the formats")。

以前はyt-dlp非公開のYoutubeDL._select_formatsが組み立てるctx dictの形を写して
build_format_selectorの戻り値を直接呼んでいたが、その形はyt-dlpの更新で変わりうるため廃止した。
"""

import copy
from collections.abc import Callable
from typing import Any

import yt_dlp
from yt_dlp.postprocessor import PostProcessor

# process_ie_resultが要求する最小限の動画情報。extractorは候補が無いときのエラーメッセージの組み立てに必須
_PREVIEW_INFO = {"id": "preview", "title": "preview", "extractor": "generic", "extractor_key": "Generic"}


def select_formats(
    formats: list[dict], format_spec: str, format_sort: list | None = None
) -> list[dict]:
    """format_specに一致する候補をformatsから選択する(ネットワークアクセスなし)。

    実際のダウンロード(DownloadWorker)と同じ選択結果を得るため、独自の選択ロジックを
    実装せず、formatsだけを持つ最小の動画情報をyt-dlpの通常の処理経路
    (process_ie_result)に通して選ばせる。一致する候補が無ければ空リストを返す。
    """
    ydl_opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "format": format_spec}
    if format_sort:
        ydl_opts["format_sort"] = format_sort
    info = {**_PREVIEW_INFO, "formats": copy.deepcopy(formats)}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return [ydl.process_ie_result(info, download=False)]
    except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError):
        return []


class ExcludeFormatsPP(PostProcessor):
    """フォーマット選択の直前(when="pre_process")に、excludeに一致するフォーマットを候補から取り除く。

    yt-dlpはpre_processの実行後に info["formats"] を読み直してから選択するため
    (YoutubeDL.process_video_resultの "The pre-processors may have modified the formats")、
    ここで取り除いたフォーマットは選ばれない。
    """

    def __init__(self, exclude: Callable[[dict], bool]):
        super().__init__()
        self._exclude = exclude

    def run(self, info: dict) -> tuple[list, dict]:
        formats = info.get("formats")
        if formats is not None:
            info["formats"] = [f for f in formats if not self._exclude(f)]
        return [], info


# postprocessor_hooksに通知される名前(クラス名から末尾の"PP"を除いたもの)
EXCLUDE_FORMATS_PP_KEY: str = ExcludeFormatsPP.pp_key()


def add_format_exclusion(ydl: yt_dlp.YoutubeDL, exclude: Callable[[dict], bool]) -> None:
    """ydlのフォーマット選択で、excludeに一致するフォーマットを候補から完全に除外する"""
    ydl.add_post_processor(ExcludeFormatsPP(exclude), when="pre_process")
