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

運用ルール: requirements.txtのyt-dlpはバージョンを固定している。更新する際は
バージョンを上げてから`python -m unittest discover -s tests`を実行し、
tests/test_yt_dlp_selection.pyが通ることを確認すること。落ちた場合はyt-dlp側の
YoutubeDL._select_formats/build_format_selectorの実装差分を確認し、このファイルの
_build_ctxを追従させる。
"""

from collections.abc import Callable

import yt_dlp


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


def select_formats(
    formats: list[dict], format_spec: str, format_sort: list | None = None
) -> list[dict]:
    """format_specに一致する候補をformatsから選択する(ネットワークアクセスなし)。

    実際のダウンロード(DownloadWorker)と同じ選択結果を得るため、独自の選択ロジックを
    実装せずyt-dlp本体の選択エンジンをそのまま利用する。
    """
    ydl_opts = {"quiet": True, "no_warnings": True}
    if format_sort:
        ydl_opts["format_sort"] = format_sort
    ydl = yt_dlp.YoutubeDL(ydl_opts)
    ydl.sort_formats({"formats": formats})
    selector = ydl.build_format_selector(format_spec)
    return list(selector(_build_ctx(formats)))


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
