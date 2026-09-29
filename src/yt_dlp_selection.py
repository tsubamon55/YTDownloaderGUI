"""yt-dlpのフォーマット選択エンジンに対する接続点をこのファイルに集約する。

yt-dlpは`format_spec`文字列(例: "bv*+ba/b")を解釈するDSLパーサを
`YoutubeDL.build_format_selector`として公開しているが、それが返すselector関数は
{"formats": [...], "has_merged_format": bool, "incomplete_formats": bool} という
ctx dictを引数に取る。このctxの組み立て自体はyt_dlp.YoutubeDL._select_formats(非公開)
の実装そのものであり、正式に安定が保証されたAPIではない。

以前はformat_engine.py(プレビュー用の選択)とworkers.py(ダウンロード時の非推奨
フォーマット除外)がそれぞれ独立にこの非公開の契約を仮定していたため、yt-dlpの
更新で契約が変わった場合に片方だけ追従し忘れてプレビューと実ダウンロードの結果が
ズレる恐れがあった。この非公開の契約に触れる箇所をこのモジュールの_build_ctxに
一本化し、tests/test_yt_dlp_selection.pyで直接検証することで、yt-dlpの更新時に
真っ先にここで検知できるようにする。

運用ルール: requirements.inのyt-dlpはバージョンを固定している。更新する際は
バージョンを上げてロックファイルを再生成してから`python -m unittest discover -s tests`を実行し、
tests/test_yt_dlp_selection.pyが通ることを確認すること。落ちた場合はyt-dlp側の
YoutubeDL._select_formats/build_format_selectorの実装差分を確認し、このファイルの
_build_ctxを追従させる。
"""

import copy
from collections.abc import Callable
from typing import Any

import yt_dlp
from yt_dlp.postprocessor import PostProcessor


def _build_ctx(formats: list[dict]) -> dict:
    """yt_dlp.YoutubeDL._select_formatsが内部で組み立てるctxと同じ形を作る。"""
    return {
        "formats": formats,
        "has_merged_format": any(
            "none" not in (f.get("acodec"), f.get("vcodec")) for f in formats
        ),
        "incomplete_formats": (
            all(f.get("vcodec") == "none" for f in formats)
            or all(f.get("acodec") == "none" for f in formats)
        ),
    }


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


def make_filtering_format_selector(
    format_spec: str, exclude: Callable[[dict], bool]
) -> Callable[[dict], list[dict]]:
    """excludeに一致するフォーマットを候補から除外した上でformat_specを解決する
    selector関数を作る。戻り値はyt-dlpのydl_opts["format"]にそのまま渡せる。

    (has_merged_format/incomplete_formatsは除外前の全フォーマットから計算される
    値なので、除外後のフォーマットに合わせて_build_ctxで計算し直す)
    """
    base_selector = yt_dlp.YoutubeDL({"quiet": True}).build_format_selector(format_spec)

    def selector(ctx: dict) -> list[dict]:
        filtered_formats = [f for f in ctx["formats"] if not exclude(f)]
        return base_selector(_build_ctx(filtered_formats))

    return selector


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
