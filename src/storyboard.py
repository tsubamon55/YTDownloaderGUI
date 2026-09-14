"""ストーリーボード(YouTube等がシークバーのプレビュー用に提供する格子状サムネイル画像)から、
指定した再生時刻に対応する1マスの切り出し位置を求める純粋関数群。

QtやHTTP通信には一切依存しないため、単体テストがそのまま実行できる。
実際の画像取得はworkers.StoryboardFragmentWorkerが、切り出し・表示はwidgets.RangeSlider/
main_window.MainWindowが担当する。
"""

from dataclasses import dataclass


@dataclass
class StoryboardTile:
    """1マス分のサムネイルの位置。fragment_urlの画像をこの矩形で切り出せば、
    その時刻のサムネイルになる"""

    fragment_url: str
    x: int
    y: int
    width: int
    height: int


def select_storyboard_format(formats: list[dict], min_width: int = 0, min_height: int = 0) -> dict | None:
    """フォーマット一覧の中から、プレビュー表示に使うストーリーボードを選ぶ。

    min_width/min_height(プレビューの表示サイズ)以上の1マスを持つものの中では、
    ドラッグ中に何度も取得し直さずに済むようデータ量が最小のものを選ぶ。
    どれも指定サイズに届かない場合(短い動画等)は、拡大表示になっても
    最も画質の良い(1マスが最大の)ものを選ぶ"""
    storyboards = [
        f for f in formats
        if f.get("format_note") == "storyboard" and f.get("fragments")
    ]
    if not storyboards:
        return None

    def tile_area(f: dict) -> int:
        return (f.get("width") or 0) * (f.get("height") or 0)

    large_enough = [
        f for f in storyboards
        if (f.get("width") or 0) >= min_width and (f.get("height") or 0) >= min_height
    ]
    if large_enough:
        return min(large_enough, key=tile_area)
    return max(storyboards, key=tile_area)


def storyboard_tile_for_time(storyboard: dict, duration: float, seconds: float) -> StoryboardTile | None:
    """再生時刻(秒)に対応するサムネイルマスの位置を返す。必要な情報が
    欠けている場合はNoneを返す"""
    fps = storyboard.get("fps")
    rows = storyboard.get("rows")
    columns = storyboard.get("columns")
    width = storyboard.get("width")
    height = storyboard.get("height")
    fragments = storyboard.get("fragments") or []
    if not (fps and rows and columns and width and height and fragments and duration):
        return None

    # フォーマット自体には全体の枚数(frame_count)が直接入っていないため、
    # fps(枚数/動画長)と実際の動画長から逆算する
    frame_count = max(round(fps * duration), 1)
    tiles_per_fragment = rows * columns

    clamped_seconds = min(max(seconds, 0), duration)
    frame_index = min(int(clamped_seconds / duration * frame_count), frame_count - 1)

    fragment_index = min(frame_index // tiles_per_fragment, len(fragments) - 1)
    tile_index = min(frame_index - fragment_index * tiles_per_fragment, tiles_per_fragment - 1)
    row, col = divmod(tile_index, columns)

    return StoryboardTile(
        fragment_url=fragments[fragment_index]["url"],
        x=col * width,
        y=row * height,
        width=width,
        height=height,
    )
