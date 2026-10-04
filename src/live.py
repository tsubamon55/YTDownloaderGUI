"""ライブ配信の録画に関する、Qt・yt-dlpに依存しない計算(タイトル・さかのぼりの進み具合・進捗の文言)"""

import re
from dataclasses import dataclass
from typing import Literal

from clip_range import clip_range_label, format_clip_time
from formats import format_size

# 録画の開始位置。"from_start"は配信の最初からさかのぼって、"from_now"は今から録画する
LiveMode = Literal["from_start", "from_now"]


@dataclass(frozen=True)
class LiveInfo:
    """動画情報の取得で分かった、配信中のライブの情報"""

    # 配信の開始時刻(UNIX時刻)。不明ならNone
    started_at: float | None
    # 配信の最初からさかのぼって録画できるか(YouTube側でさかのぼり用の区切りが提供されているか)
    can_record_from_start: bool


# 今から録画するための抽出では、yt-dlpがタイトル末尾に現在日時(" 2026-10-02 19:06")を付ける。
# 画面には配信のタイトルそのものを見せたいため、末尾の日時だけを取り除く
_LIVE_TITLE_SUFFIX = re.compile(r" \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def strip_live_title_suffix(title: str) -> str:
    return _LIVE_TITLE_SUFFIX.sub("", title)


def live_range_label(
    stream_started_at: float | None,
    recording_started_at: float | None,
    recorded_seconds: float | None,
    reached_end: bool,
) -> str | None:
    """録画した範囲を、配信開始からの時刻でファイル名に添えるラベルにする(例: "0:59-1:13")。
    クリップ範囲のラベル(clip_range_label)と同じく、配信の最初からの側・最後までの側は空にし、
    最初から最後まで録画できた場合はNoneを返す。recording_started_atがNoneなら配信の最初から
    録画したとみなす。配信開始時刻・録画した長さが分からず範囲を表せない場合もNone"""
    if recording_started_at is None:
        start = None
    elif stream_started_at is None:
        return None
    else:
        start = max(round(recording_started_at - stream_started_at), 0)
    end = None
    if not reached_end:
        if recorded_seconds is None:
            return None
        end = (start or 0) + round(recorded_seconds)
    return clip_range_label(start, end)


# progressシグナルで「割合がない(録画中)」ことを表す値。DownloadTaskWidgetは進捗バーを不定表示にする
LIVE_RECORDING_PERCENT = -1.0
LIVE_SAVING_TEXT = "保存しています..."
LIVE_ENDED_TEXT = "配信終了・保存しています..."
LIVE_DISCARDING_TEXT = "破棄しています..."


def catch_up_text(percent: float) -> str:
    return f"さかのぼり取得中 {percent:.0f}%"


def recording_text(seconds: float, size_bytes: int | None) -> str:
    """録画中の表示(例: "録画中 12:34 · 345.0MB")。サイズが分からない間は省く"""
    parts = [f"録画中 {format_clip_time(seconds)}"]
    if size_bytes:
        parts.append(format_size(size_bytes))
    return " · ".join(parts)


# 配信の最後尾から何区切り以内に入ったら「追いついた」とみなすか(YouTubeの区切りは約2秒)
CAUGHT_UP_FRAGMENT_MARGIN = 3


class CatchUpTracker:
    """配信の最初からの録画で、映像・音声それぞれの区切りの進み具合から、さかのぼり取得の割合を求める。

    yt-dlpは映像と音声を別々のスレッドで取得し、音声の方がずっと速く進むため、遅い方を全体の進み具合とする"""

    def __init__(self, component_count: int) -> None:
        self._component_count = component_count
        self._positions: dict[str, tuple[int, int]] = {}
        self.caught_up = False

    def update(self, format_id: str, fragment_index: int | None, fragment_count: int | None) -> float | None:
        """進捗フックの報告を反映し、さかのぼり取得の割合(0〜100)を返す。追いついた後はNone"""
        if self.caught_up:
            return None
        if fragment_index is not None and fragment_count:
            self._positions[format_id] = (fragment_index, fragment_count)
        if len(self._positions) < self._component_count:
            return 0.0
        if all(count - index <= CAUGHT_UP_FRAGMENT_MARGIN for index, count in self._positions.values()):
            self.caught_up = True
            return None
        return min(index / count for index, count in self._positions.values()) * 100
