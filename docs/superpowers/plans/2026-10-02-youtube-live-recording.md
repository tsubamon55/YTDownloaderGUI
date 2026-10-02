# YouTube ライブ配信の録画 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 配信中の YouTube ライブを、配信の最初から(さかのぼれる場合の既定)または今から録画し、停止・配信終了・アプリ終了のいずれでも再生できるファイルとして保存する。録画中のタスクはダウンロード一覧の上部に固定する。

**Architecture:** 動画情報の取得(`FormatListWorker`)でライブとさかのぼり可否を判定し、`LiveInfo` として画面に渡す。録画は既存の `DownloadWorker` が担い、「配信の最初から」は yt-dlp の `live_from_start`(停止は進捗フックからの `KeyboardInterrupt`)、「今から」は新規の `LiveRecorder` が ffmpeg を直接動かす(停止は標準入力への `q`)。どちらも外向きのシグナルは同じ形で、画面は `live_state_changed` で一覧の固定を外す。

**Tech Stack:** Python 3.13、PyQt6、yt-dlp 2026.08.19、ffmpeg、unittest

**Spec:** `docs/superpowers/specs/2026-10-02-youtube-live-recording-design.md`

## Global Constraints

- yt-dlp は `requirements.in` の `yt-dlp>=2026.8.19` のまま(新しい依存は追加しない)。
- 「配信の最初から」の録画では `concurrent_fragment_downloads` を指定しない(既定の 1。並列にすると停止できない)。
- 全ての `YoutubeDL` 呼び出しに `noprogress: True` を付ける(`_base_ydl_opts`)。
- 画面の文言(そのまま使う): 「● LIVE」「配信開始から H:MM:SS」「録画開始位置:」「配信の最初から（H:MM:SS さかのぼる）」「今から」「今から録画します（この配信はさかのぼって保存できません）」「録画開始」「配信中のライブを検出しました」「停止」「録画を停止して保存しますか?」「さかのぼり取得中 37%」「録画中 12:34 · 345.0MB」「保存しています...」「配信終了・保存しています...」「録画できたデータがありません」「配信開始前です。配信が始まってから再度お試しください」。
- 時間の表示は `clip_range.format_clip_time`(`H:MM:SS`、1時間未満は `M:SS`)、サイズは `formats.format_size`(`345.0MB`)を使う。
- ライブの赤は `#d93025`(`theme.LIVE_COLOR`)。
- 外部由来の文字列(タイトル)を表示する `QLabel` は `Qt.TextFormat.PlainText` にする(既存の方針)。
- テストは `python -m unittest discover -s tests` で全件、lint は `python -m ruff check src tests`、型は `python -m mypy`。コマンドは仮想環境を有効にした状態で、リポジトリ直下で実行する(WSL では `.venv/bin/python`)。
- コメント・docstring は既存コードと同じく日本語で、「なぜそうするか」を書く。
- コミットメッセージは日本語の1行目(既存の履歴と同じ形)+空行+ `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## Review Focus

- 停止ボタンを押したのが映像・音声のどちらかのスレッドの区切りの途中でも、両方のスレッドが止まって結合されること(Task 3: 停止後のフックは `downloading` のたびに毎回 `KeyboardInterrupt` を送出するテスト)。
- 停止を押した時点で、さかのぼりがまだ1区切りも取れていない(`.part` しかない)場合に、空のファイルを「完了」にせず失敗として知らせること(Task 3・Task 5 の「録画できたデータがありません」のテスト)。
- 今からの録画中に通信が切れて ffmpeg が終了コード 0 以外で終わっても、それまでの `.ts` を保存すること(Task 5 のテスト)。
- 録画の保存中にもう一度ウィンドウを閉じようとしても、確認を出し直したり二重に待ったりしないこと(Task 9 のテスト)。
- 録画中のタスクが失敗で終わった場合(`live_state_changed(False)` が来ないまま `finished_error`)も、固定領域から外れて通常の一覧へ移ること(Task 8 のテスト)。

---

## ファイル構成

| ファイル | 役割 | 変更 |
|---|---|---|
| `src/live.py` | `LiveInfo`・`LiveMode`、タイトルの日時の除去、さかのぼりの進み具合(`CatchUpTracker`)、進捗の文言。Qt・yt-dlp に依存しない | 新規 |
| `src/live_recorder.py` | 「今から」の録画。ffmpeg のコマンドの組み立て、`-progress` の解析、`q` での停止、`.ts` からの再格納 | 新規 |
| `src/workers.py` | `FormatListWorker` のライブ判定、`DownloadRequest.live_mode`、`DownloadWorker` の録画(両方式) | 変更 |
| `src/theme.py` | `LIVE_COLOR`・`LIVE_PROGRESS_BAR_STYLE` | 変更 |
| `src/widgets.py` | `DownloadTaskWidget` のライブ表示・不定の進捗・停止 | 変更 |
| `src/main_window_ui.py` | LIVE 見出し、録画開始位置の行、一覧の固定領域 | 変更 |
| `src/main_window.py` | ライブ表示の切り替え、録画の開始・固定・停止の確認、終了時の保存待ち | 変更 |
| `pyproject.toml` | ruff の `known-first-party` に `live`・`live_recorder` | 変更 |
| `README.md` | 機能一覧にライブ録画 | 変更 |
| `tests/test_live.py`、`tests/test_live_recorder.py` | 新規モジュールのテスト | 新規 |
| `tests/test_workers.py`、`tests/test_widgets.py`、`tests/test_main_window.py` | 既存のテストに追加 | 変更 |

---

### Task 1: ライブ関連の計算 `src/live.py`

**Files:**
- Create: `src/live.py`
- Create: `tests/test_live.py`
- Modify: `pyproject.toml`(`[tool.ruff.lint.isort]` の `known-first-party`)

**Interfaces:**
- Consumes: `clip_range.format_clip_time(seconds: float) -> str`、`formats.format_size(num_bytes) -> str`
- Produces:
  - `LiveMode = Literal["from_start", "from_now"]`
  - `@dataclass(frozen=True) class LiveInfo: started_at: float | None; can_record_from_start: bool`
  - `strip_live_title_suffix(title: str) -> str`
  - `LIVE_RECORDING_PERCENT: float = -1.0`(`progress` シグナルで「割合なし(録画中)」を表す値)
  - `LIVE_SAVING_TEXT = "保存しています..."`、`LIVE_ENDED_TEXT = "配信終了・保存しています..."`
  - `catch_up_text(percent: float) -> str`、`recording_text(seconds: float, size_bytes: int | None) -> str`
  - `class CatchUpTracker: __init__(self, component_count: int)`、`update(self, format_id: str, fragment_index: int | None, fragment_count: int | None) -> float | None`、`caught_up: bool`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_live.py`:

```python
"""live.py(ライブ録画に関するQt・yt-dlpに依存しない計算)の単体テスト"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from live import (
    CatchUpTracker,
    catch_up_text,
    recording_text,
    strip_live_title_suffix,
)


class StripLiveTitleSuffixTest(unittest.TestCase):
    def test_removes_date_time_appended_by_yt_dlp(self):
        self.assertEqual(strip_live_title_suffix("【逆凸】加入2日目！ 2026-10-02 19:06"), "【逆凸】加入2日目！")

    def test_keeps_title_without_suffix(self):
        self.assertEqual(strip_live_title_suffix("Morning Stream"), "Morning Stream")

    def test_keeps_date_in_the_middle_of_title(self):
        self.assertEqual(strip_live_title_suffix("2026-10-02 19:06 振り返り"), "2026-10-02 19:06 振り返り")


class ProgressTextTest(unittest.TestCase):
    def test_catch_up_text_rounds_percent(self):
        self.assertEqual(catch_up_text(37.4), "さかのぼり取得中 37%")

    def test_recording_text_with_size(self):
        self.assertEqual(recording_text(754, 345 * 1024 * 1024), "録画中 12:34 · 345.0MB")

    def test_recording_text_over_an_hour(self):
        self.assertEqual(recording_text(3600 + 59 * 60 + 30, None), "録画中 1:59:30")

    def test_recording_text_omits_unknown_size(self):
        self.assertEqual(recording_text(5, 0), "録画中 0:05")


class CatchUpTrackerTest(unittest.TestCase):
    def test_reports_zero_until_every_component_has_reported(self):
        tracker = CatchUpTracker(component_count=2)
        self.assertEqual(tracker.update("140", 3600, 3700), 0.0)

    def test_percent_follows_the_slowest_component(self):
        tracker = CatchUpTracker(component_count=2)
        tracker.update("140", 3000, 4000)
        self.assertAlmostEqual(tracker.update("298", 1000, 4000), 25.0)

    def test_caught_up_when_all_components_are_near_the_live_edge(self):
        tracker = CatchUpTracker(component_count=2)
        tracker.update("140", 3998, 4000)
        self.assertIsNone(tracker.update("298", 3997, 4000))
        self.assertTrue(tracker.caught_up)

    def test_one_fast_component_alone_is_not_caught_up(self):
        """音声は映像より速く追いつくため、片方だけで判定しない"""
        tracker = CatchUpTracker(component_count=2)
        tracker.update("298", 100, 4000)
        self.assertIsNotNone(tracker.update("140", 4000, 4000))
        self.assertFalse(tracker.caught_up)

    def test_stays_caught_up_even_if_count_grows(self):
        """追いついた後に配信が進んで区切りの総数が増えても、さかのぼり表示へ戻らない"""
        tracker = CatchUpTracker(component_count=1)
        tracker.update("96", 4000, 4000)
        self.assertIsNone(tracker.update("96", 4001, 4010))

    def test_missing_fragment_info_keeps_previous_percent(self):
        tracker = CatchUpTracker(component_count=1)
        tracker.update("96", 1000, 4000)
        self.assertAlmostEqual(tracker.update("96", None, None), 25.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_live -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'live'`)

- [ ] **Step 3: 実装する**

`src/live.py`:

```python
"""ライブ配信の録画に関する、Qt・yt-dlpに依存しない計算(タイトル・さかのぼりの進み具合・進捗の文言)"""

import re
from dataclasses import dataclass
from typing import Literal

from clip_range import format_clip_time
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


# progressシグナルで「割合がない(録画中)」ことを表す値。DownloadTaskWidgetは進捗バーを不定表示にする
LIVE_RECORDING_PERCENT = -1.0
LIVE_SAVING_TEXT = "保存しています..."
LIVE_ENDED_TEXT = "配信終了・保存しています..."


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
```

`pyproject.toml` の `known-first-party` に `"live", "live_recorder",` を追加する(`"format_engine", "formats",` の後、アルファベット順で `"main_window"` の前):

```toml
known-first-party = [
    "clip_range", "clip_trimmer", "config", "dialogs", "errors", "folder_opener", "format_engine", "formats",
    "live", "live_recorder", "main_window", "main_window_ui", "paths", "storyboard", "theme", "updater", "widgets",
    "workers", "yt_dlp_selection",
]
```

- [ ] **Step 4: 通ることを確認する**

Run: `python -m unittest tests.test_live -v && python -m ruff check src tests && python -m mypy`
Expected: 13件 OK、ruff・mypy ともエラーなし

- [ ] **Step 5: コミット**

```bash
git add src/live.py tests/test_live.py pyproject.toml
git commit -m "ライブ録画のタイトル・さかのぼりの進み具合・進捗の文言を計算する処理を追加する

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 動画情報の取得でライブを判定する

**Files:**
- Modify: `src/workers.py`(`_base_ydl_opts`、`DownloadRequest`、`FormatListWorker`)
- Modify: `src/main_window.py`(`fetch_formats` のシグナル接続、`on_formats_fetched`、`_clear_video_info`、`__init__`)
- Test: `tests/test_workers.py`、`tests/test_main_window.py`

**Interfaces:**
- Consumes: `live.LiveInfo`、`live.LiveMode`、`live.strip_live_title_suffix`
- Produces:
  - `FormatListWorker.finished_ok = pyqtSignal(list, str, bytes, object, object)`(5番目は `LiveInfo | None`)
  - `FormatListWorker.UPCOMING_LIVE_ERROR = "配信開始前です。配信が始まってから再度お試しください"`
  - `DownloadRequest.live_mode: LiveMode | None = None`
  - `MainWindow.live_info: LiveInfo | None`
  - `MainWindow.on_formats_fetched(formats, title, thumbnail_bytes, duration, worker, auto=False, live_info=None)`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_workers.py` の import に `from live import LiveInfo` を追加し、末尾に追加する:

```python
def _live_ydl_factory(normal_info, from_start_info=None, from_start_error=None):
    """live_from_startの有無で結果を変えるYoutubeDLの代わり。生成時のoptsを記録する"""
    created_opts = []

    def factory(opts):
        created_opts.append(opts)
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.__exit__.return_value = False
        if opts.get("live_from_start"):
            if from_start_error is not None:
                ydl.extract_info.side_effect = from_start_error
            else:
                ydl.extract_info.return_value = from_start_info
        else:
            ydl.extract_info.return_value = normal_info
        return ydl

    return factory, created_opts


class FormatListWorkerLiveTest(unittest.TestCase):
    URL = "https://www.youtube.com/live/abc"

    def _run(self, factory):
        results, errors = [], []
        worker = FormatListWorker(self.URL)
        worker.finished_ok.connect(lambda *args: results.append(args))
        worker.finished_error.connect(errors.append)
        with patch("workers.get_ffmpeg_location", return_value=None), \
             patch("workers.yt_dlp.YoutubeDL", side_effect=factory):
            worker.run()
        return results, errors

    def test_normal_video_has_no_live_info_and_is_extracted_once(self):
        factory, created = _live_ydl_factory({"title": "Video", "duration": 60, "formats": []})
        results, _ = self._run(factory)
        self.assertIsNone(results[0][4])
        self.assertEqual(len(created), 1)

    def test_live_that_can_be_recorded_from_start(self):
        normal = {"title": "Stream 2026-10-02 19:06", "live_status": "is_live",
                  "release_timestamp": 1790928042, "formats": []}
        factory, created = _live_ydl_factory(normal, from_start_info={"title": "Stream", "formats": []})
        results, _ = self._run(factory)
        _, title, _, duration, live_info = results[0]
        self.assertEqual(live_info, LiveInfo(started_at=1790928042, can_record_from_start=True))
        self.assertEqual(title, "Stream")
        self.assertIsNone(duration)
        self.assertTrue(created[1]["live_from_start"])

    def test_live_without_from_start_formats(self):
        normal = {"title": "Stream 2026-10-02 19:06", "live_status": "is_live",
                  "release_timestamp": 1790928042, "formats": []}
        error = yt_dlp.utils.ExtractorError("--live-from-start is passed, but there are no formats")
        factory, _ = _live_ydl_factory(normal, from_start_error=error)
        results, errors = self._run(factory)
        self.assertEqual(errors, [])
        _, title, _, _, live_info = results[0]
        self.assertEqual(live_info, LiveInfo(started_at=1790928042, can_record_from_start=False))
        self.assertEqual(title, "Stream")

    def test_upcoming_live_reports_japanese_message(self):
        factory, _ = _live_ydl_factory({"title": "Soon", "live_status": "is_upcoming", "formats": []})
        results, errors = self._run(factory)
        self.assertEqual(results, [])
        self.assertEqual(errors, [FormatListWorker.UPCOMING_LIVE_ERROR])

    def test_yt_dlp_upcoming_error_is_replaced_with_japanese_message(self):
        """配信開始前のライブでは、yt-dlpはinfoを返さずに英語のエラーを送出することが多い"""
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.__exit__.return_value = False
        ydl.extract_info.side_effect = yt_dlp.utils.ExtractorError("This live event will begin in 3 hours.")
        results, errors = self._run(lambda opts: ydl)
        self.assertEqual(errors, [FormatListWorker.UPCOMING_LIVE_ERROR])


class BaseYdlOptsNoProgressTest(unittest.TestCase):
    def test_progress_output_is_disabled(self):
        """quietだけではライブの進捗表示が標準出力に出続けるため、noprogressも付ける"""
        self.assertTrue(_base_ydl_opts()["noprogress"])
```

既存の `FormatListWorkerRunTest` には、`finished_ok` の引数を4つに分けて受け取っている箇所が2つある(`_, _, thumbnail_bytes, _ = results[0]`)。5つになるため、どちらも `_, _, thumbnail_bytes, _, _ = results[0]` に直す。

`tests/test_main_window.py` の import に `from live import LiveInfo` を追加し、`OnFormatsFetchedTest` に追加する:

```python
    def test_live_info_is_kept_until_url_changes(self):
        worker = MagicMock()
        self.window.format_worker = worker
        live_info = LiveInfo(started_at=1790928042, can_record_from_start=True)

        self.window.on_formats_fetched([], "Stream", b"", None, worker, live_info=live_info)
        self.assertEqual(self.window.live_info, live_info)

        self.window.url_edit.setText("https://example.com/watch?v=other")
        self.assertIsNone(self.window.live_info)
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_workers.FormatListWorkerLiveTest tests.test_workers.BaseYdlOptsNoProgressTest tests.test_main_window.OnFormatsFetchedTest -v`
Expected: FAIL(`noprogress` の KeyError、`results[0][4]` の IndexError、`live_info` の TypeError など)

- [ ] **Step 3: 実装する**

`src/workers.py`:

import に追加:

```python
from live import LiveInfo, LiveMode, strip_live_title_suffix
```

`_base_ydl_opts` の docstring と opts を変える:

```python
def _base_ydl_opts(format_sort: list[str] | None = None, ffmpeg_location: str | None = None) -> dict[str, Any]:
    """このアプリの全てのYoutubeDL呼び出しに共通するオプション(出力の抑止・プレイリスト展開の無効化)。
    端末から起動した場合にyt-dlpがエラー文へANSIの色コードを埋め込み、それがそのまま
    ダイアログに表示されてしまうため、色付けも無効にする。
    quietだけではライブの録画中の進捗表示が標準出力に出続けるため、noprogressも付ける"""
    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "color": "no_color",
    }
```

`DownloadRequest` の `clip_end` の後に追加:

```python
    # ライブの録画の開始位置。Noneは通常の動画
    live_mode: LiveMode | None = None
```

`FormatListWorker` を変える(`finished_ok` の宣言、定数、`run`、新しいメソッド2つ):

```python
class FormatListWorker(QThread):
    # 4番目の要素(動画の長さ・秒)と5番目の要素(ライブの情報 LiveInfo)はNoneを取り得るためobject型で宣言する
    finished_ok = pyqtSignal(list, str, bytes, object, object)
    finished_error = pyqtSignal(str)

    UPCOMING_LIVE_ERROR = "配信開始前です。配信が始まってから再度お試しください"
    # 配信開始前のライブに対して、yt-dlpはinfoを返さずにこの文言(英語)を含むエラーを送出することが多い
    _UPCOMING_LIVE_ERROR_MARKER = "live event will begin"
```

(`MAX_THUMBNAIL_CANDIDATES` 以下の既存の定数・`__init__`・`_thumbnail_url_candidates` はそのまま)

```python
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
```

`src/main_window.py`:

import に追加: `from live import LiveInfo`

`__init__` の `self.video_duration: float | None = None` の直後に追加:

```python
        # 表示中の動画が配信中のライブなら、その情報(さかのぼれるか等)。通常の動画ではNone
        self.live_info: LiveInfo | None = None
```

`_clear_video_info` の `self.info_ready = False` の直後に追加:

```python
        self.live_info = None
```

`fetch_formats` の `finished_ok` の接続を変える:

```python
        worker.finished_ok.connect(
            lambda formats, title, thumb, duration, live_info, w=worker: self.on_formats_fetched(
                formats, title, thumb, duration, w, auto, live_info
            )
        )
```

`on_formats_fetched` の引数に `live_info: LiveInfo | None = None` を `auto` の後に追加し、`self.video_duration = duration` の直後に追加:

```python
        self.live_info = live_info
```

- [ ] **Step 4: 通ることを確認する**

Run: `python -m unittest discover -s tests && python -m ruff check src tests && python -m mypy`
Expected: 全件 OK(既存の `FormatListWorkerRunTest` も通る)、ruff・mypy ともエラーなし

- [ ] **Step 5: コミット**

```bash
git add src/workers.py src/main_window.py tests/test_workers.py tests/test_main_window.py
git commit -m "動画情報の取得で配信中のライブと、さかのぼって録画できるかを判定する

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 配信の最初からの録画(`DownloadWorker`)

**Files:**
- Modify: `src/workers.py`(`DownloadWorker`)
- Test: `tests/test_workers.py`

**Interfaces:**
- Consumes: `live.CatchUpTracker`、`live.catch_up_text`、`live.recording_text`、`live.LIVE_RECORDING_PERCENT`、`live.LIVE_SAVING_TEXT`、`live.LIVE_ENDED_TEXT`、`DownloadRequest.live_mode`
- Produces:
  - `DownloadWorker.live_state_changed = pyqtSignal(bool)`(録画中なら True。停止を受け付けた時・配信が終わった時に1回だけ False)
  - `DownloadWorker._start_live_state(probe_info: Format) -> None`、`DownloadWorker._end_live_state() -> None`(Task 5 でも使う)
  - `DownloadWorker.LIVE_NO_DATA_ERROR = "録画できたデータがありません"`
  - ライブでは `cancel()` は「停止して保存」になり、成功の経路(`finished_ok`)で終わる

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_workers.py` に追加する:

```python
class LiveFromStartProgressHookTest(unittest.TestCase):
    def _worker(self, component_count=2, started_at=None):
        worker = make_worker(live_mode="from_start")
        worker._start_live_state({
            "requested_formats": [{"format_id": "298"}, {"format_id": "140"}][:component_count],
            "release_timestamp": started_at,
        })
        return worker

    @staticmethod
    def _hook_info(fmt_id, index, count, downloaded=1000):
        return {
            "status": "downloading",
            "info_dict": {"format_id": fmt_id, "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
            "fragment_index": index,
            "fragment_count": count,
            "downloaded_bytes": downloaded,
        }

    def test_stop_raises_keyboard_interrupt_on_every_downloading_call(self):
        """yt-dlpはKeyboardInterruptをライブの正常な終わりとして扱い、結合まで行う。
        映像・音声の両スレッドを止めるため、1回きりではなく毎回送出する"""
        worker = self._worker()
        worker.cancel()
        for fmt_id in ("298", "140", "298"):
            with self.assertRaises(KeyboardInterrupt):
                worker._progress_hook(self._hook_info(fmt_id, 10, 4000))

    def test_stop_does_not_interrupt_finished_status(self):
        """結合前の後始末(finished)の報告までは止めない"""
        worker = self._worker()
        worker.cancel()
        worker._progress_hook({"status": "finished", "info_dict": {"format_id": "298"}, "filename": "x.mp4"})

    def test_normal_download_cancel_still_raises_download_error(self):
        worker = make_worker()
        worker.cancel()
        with self.assertRaises(yt_dlp.utils.DownloadError):
            worker._progress_hook({"status": "downloading"})

    def test_catch_up_progress_follows_slowest_component(self):
        worker = self._worker()
        events = []
        worker.progress.connect(lambda pct, text: events.append((pct, text)))
        worker._progress_hook(self._hook_info("140", 3000, 4000))
        worker._progress_hook(self._hook_info("298", 1000, 4000))
        self.assertEqual(events[-1], (25.0, "さかのぼり取得中 25%"))

    def test_after_catching_up_shows_recording_time_and_total_size(self):
        worker = self._worker(component_count=1, started_at=1000.0)
        events = []
        worker.progress.connect(lambda pct, text: events.append((pct, text)))
        with patch("workers.time.time", return_value=1000.0 + 754):
            worker._progress_hook(self._hook_info("298", 4000, 4000, downloaded=345 * 1024 * 1024))
        self.assertEqual(events[-1], (-1.0, "録画中 12:34 · 345.0MB"))

    def test_finished_component_ends_live_state_once_with_saving_text(self):
        worker = self._worker()
        states, events = [], []
        worker.live_state_changed.connect(states.append)
        worker.progress.connect(lambda pct, text: events.append(text))
        worker.cancel()
        for fmt_id in ("298", "140"):
            worker._progress_hook({"status": "finished", "info_dict": {"format_id": fmt_id}, "filename": "x"})
        self.assertEqual(states, [False])
        self.assertEqual(events, ["保存しています..."])

    def test_stream_end_shows_ended_text(self):
        worker = self._worker()
        events = []
        worker.progress.connect(lambda pct, text: events.append(text))
        worker._progress_hook({"status": "finished", "info_dict": {"format_id": "298"}, "filename": "x"})
        self.assertEqual(events, ["配信終了・保存しています..."])


class LiveFromStartRunTest(unittest.TestCase):
    def test_download_opts_request_live_from_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, live_mode="from_start")
            worker._file_stem = "Stream"
            worker._create_work_dir()
            self.addCleanup(worker._discard_work_dir)
            opts = worker._build_download_opts("mp4", None)
        self.assertTrue(opts["live_from_start"])
        self.assertNotIn("concurrent_fragment_downloads", opts)

    def test_stopped_recording_is_saved_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, live_mode="from_start")
            oks, states = [], []
            worker.finished_ok.connect(oks.append)
            worker.live_state_changed.connect(states.append)

            def download(urls):
                write_final(worker)
                worker.cancel()  # 録画中に停止ボタンが押された
                return 0

            errors, _ = run_worker(worker, download)

            self.assertEqual(errors, [])
            self.assertEqual(oks, [os.path.join(tmp, "My Video.mp4")])
            self.assertEqual(states, [True, False])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])

    def test_stop_before_any_data_is_reported_as_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, live_mode="from_start")

            def download(urls):
                worker.cancel()  # .partしか無いうちに停止
                return 0

            errors, _ = run_worker(worker, download)
            self.assertEqual(errors, ["録画できたデータがありません"])
            self.assertEqual(os.listdir(tmp), [])

    def test_keyboard_interrupt_escaping_yt_dlp_is_reported_as_error(self):
        """yt-dlpがKeyboardInterruptをライブの終わりとして扱わずにそのまま送出した場合も、
        スレッドが例外で落ちず、失敗として知らせる"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, live_mode="from_start")
            errors, _ = run_worker(worker, KeyboardInterrupt())
            self.assertEqual(errors, ["録画できたデータがありません"])
            self.assertEqual(os.listdir(tmp), [])
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_workers.LiveFromStartProgressHookTest tests.test_workers.LiveFromStartRunTest -v`
Expected: FAIL(`_start_live_state` が無い、`live_from_start` の KeyError など)

- [ ] **Step 3: 実装する**

`src/workers.py` の import に追加:

```python
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
```

`DownloadWorker` のシグナルに追加(`cancelled` の後):

```python
    # ライブの録画中ならTrue。停止を受け付けた時・配信が終わった時にFalseを1回だけ送り、
    # 画面はダウンロード一覧の上部への固定を外す
    live_state_changed = pyqtSignal(bool)

    LIVE_NO_DATA_ERROR = "録画できたデータがありません"
```

`__init__` の末尾に追加:

```python
        # ライブの録画の状態(_start_live_stateで設定する)
        self._catch_up: CatchUpTracker | None = None
        self._live_started_at: float | None = None
        self._live_bytes: dict[str, int] = {}
        self._live_ended = False
```

`cancel` に docstring を付ける:

```python
    def cancel(self) -> None:
        """通常の動画では中止、ライブでは録画の停止(それまでの分を保存する)"""
        self._is_cancelled = True
```

`_progress_hook` を変える:

```python
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
```

`_on_component_downloading` の、フォーマットのログ出力(`self.log.emit(self._describe_selected_format(info))` の if ブロック)の直後に追加:

```python
        if self.request.live_mode is not None:
            self._on_live_downloading(fmt_id or "", hook_info)
            return
```

新しいメソッドを `_on_component_downloading` の後に追加:

```python
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
```

`_on_component_finished` の先頭(`filename = ...` の前)に追加:

```python
        if self.request.live_mode is not None:
            # ライブでは停止・配信終了のどちらでも、各コンポーネントがここを通ってから結合される
            self._end_live_state()
            return
```

`_probe` の `probe_opts["format"] = self.request.format_spec` の直後に追加:

```python
        if self.request.live_mode == "from_start":
            # 実ダウンロードと同じく、さかのぼり用のフォーマット(DASH)から選ぶ
            probe_opts["live_from_start"] = True
```

`_build_download_opts` の `opts.update({...})` の直後に追加:

```python
        if self.request.live_mode == "from_start":
            # 区切りの並列取得(concurrent_fragment_downloads)は指定しない。並列にすると
            # 進捗フックからのKeyboardInterruptで止まらなくなり、取得も速くならなかった
            opts["live_from_start"] = True
```

`_run` を変える(try ブロック内の download 部分以降と、except 節):

```python
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
```

(`except Exception as e:` 節の残りは既存のまま。ライブで停止後に例外が出た場合は「キャンセル」ではなく失敗として知らせるため、条件に `and self.request.live_mode is None` を加える)

`_finish_success` の `if not delivered:` を変える:

```python
        if not delivered:
            if self.request.live_mode is not None:
                raise RuntimeError(self.LIVE_NO_DATA_ERROR)
            raise RuntimeError("ダウンロードしたファイルが見つかりませんでした")
```

- [ ] **Step 4: 通ることを確認する**

Run: `python -m unittest discover -s tests && python -m ruff check src tests && python -m mypy`
Expected: 全件 OK、ruff・mypy ともエラーなし

- [ ] **Step 5: コミット**

```bash
git add src/workers.py tests/test_workers.py
git commit -m "配信の最初からライブを録画し、停止したらそれまでの分を保存する

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 今からの録画 `src/live_recorder.py`

**Files:**
- Create: `src/live_recorder.py`
- Create: `tests/test_live_recorder.py`

**Interfaces:**
- Consumes: `clip_trimmer.FFmpegRunner(ffmpeg_location).executable`(ffmpeg の実行ファイルの場所の解決)、`config.CONFIG.mp3_quality`(`"192"`)、`paths.log_debug`、`yt_dlp.utils.Popen`
- Produces:
  - `@dataclass(frozen=True) class LiveInput: url: str; http_headers: dict[str, str]`
  - `live_inputs(probe_info: Format) -> list[LiveInput]`
  - `build_record_command(ffmpeg: str, inputs: Sequence[LiveInput], output_path: str) -> list[str]`
  - `@dataclass class RecordingProgress: seconds: float = 0.0; size_bytes: int = 0`
  - `apply_progress_line(progress: RecordingProgress, line: str) -> bool`
  - `class LiveRecorder: __init__(self, ffmpeg_location, inputs, output_path, on_progress: Callable[[RecordingProgress], None], should_stop: Callable[[], bool])`、`record(self) -> int`(ffmpeg の終了コード)、`STOP_TIMEOUT_SECONDS = 30`
  - `recording_extension(extract_audio_codec: str | None) -> str`(`"mp3"` / `"m4a"` / `"mp4"`)
  - `build_remux_command(ffmpeg: str, ts_path: str, output_path: str, extract_audio_codec: str | None) -> list[str]`
  - `remux_recording(ffmpeg_location: str | None, ts_path: str, output_path: str, extract_audio_codec: str | None) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_live_recorder.py`:

```python
"""live_recorder.py(今からのライブ録画をffmpegで行う処理)の単体テスト。

ffmpegの代わりに、同じ形の-progress出力を書いて標準入力の"q"で終わるPythonスクリプトを
実際に子プロセスとして動かし、停止の流れ(q → 終了、応答がなければ強制終了)を確かめる"""

import os
import sys
import textwrap
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import live_recorder
from live_recorder import (
    LiveInput,
    LiveRecorder,
    RecordingProgress,
    apply_progress_line,
    build_record_command,
    build_remux_command,
    live_inputs,
    recording_extension,
)

# -progress pipe:1 と同じ形で進捗を書き、標準入力に"q"が来たら終了コード0で終わる偽のffmpeg
FAKE_FFMPEG_OBEYS_Q = textwrap.dedent("""
    import sys, threading, time
    def report():
        n = 0
        while True:
            n += 1
            print(f"total_size={n * 1000}", flush=True)
            print(f"out_time_us={n * 1000000}", flush=True)
            print("progress=continue", flush=True)
            time.sleep(0.05)
    threading.Thread(target=report, daemon=True).start()
    while sys.stdin.read(1) != "q":
        pass
    print("progress=end", flush=True)
""")

# 標準入力を無視して止まらない偽のffmpeg(強制終了の確認用)
FAKE_FFMPEG_IGNORES_Q = textwrap.dedent("""
    import time
    print("progress=continue", flush=True)
    time.sleep(60)
""")

# 配信が終わったときのように、進捗を数回書いて自分から終わる偽のffmpeg
FAKE_FFMPEG_STREAM_ENDS = textwrap.dedent("""
    for n in range(1, 4):
        print(f"total_size={n * 1000}", flush=True)
        print(f"out_time_us={n * 2000000}", flush=True)
        print("progress=continue", flush=True)
    print("progress=end", flush=True)
""")


def _recorder(script, should_stop, progress_events):
    recorder = LiveRecorder(
        None, [LiveInput("https://example.com/v.m3u8", {})], "unused.ts",
        on_progress=lambda p: progress_events.append((p.seconds, p.size_bytes)),
        should_stop=should_stop,
    )
    recorder._executable = sys.executable
    return recorder, patch.object(live_recorder, "build_record_command",
                                  return_value=[sys.executable, "-c", script])


class LiveInputsTest(unittest.TestCase):
    def test_separate_video_and_audio(self):
        info = {"requested_formats": [
            {"url": "https://v.m3u8", "http_headers": {"User-Agent": "UA"}},
            {"url": "https://a.m3u8"},
        ]}
        self.assertEqual(live_inputs(info), [
            LiveInput("https://v.m3u8", {"User-Agent": "UA"}), LiveInput("https://a.m3u8", {}),
        ])

    def test_single_muxed_format(self):
        self.assertEqual(live_inputs({"url": "https://av.m3u8"}), [LiveInput("https://av.m3u8", {})])


class BuildRecordCommandTest(unittest.TestCase):
    def test_two_inputs_with_headers_are_mapped_and_copied_to_mpegts(self):
        cmd = build_record_command("ffmpeg", [
            LiveInput("https://v.m3u8", {"User-Agent": "UA", "Referer": "https://youtube.com"}),
            LiveInput("https://a.m3u8", {}),
        ], "/work/rec.ts")
        self.assertEqual(cmd, [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y",
            "-headers", "User-Agent: UA\r\nReferer: https://youtube.com\r\n", "-i", "https://v.m3u8",
            "-i", "https://a.m3u8",
            "-map", "0", "-map", "1", "-c", "copy", "-f", "mpegts", "/work/rec.ts",
        ])


class ApplyProgressLineTest(unittest.TestCase):
    def test_reads_size_and_time_and_detects_block_end(self):
        progress = RecordingProgress()
        self.assertFalse(apply_progress_line(progress, "total_size=2097152\n"))
        self.assertFalse(apply_progress_line(progress, "out_time_us=12500000\n"))
        self.assertTrue(apply_progress_line(progress, "progress=continue\n"))
        self.assertEqual((progress.seconds, progress.size_bytes), (12.5, 2097152))

    def test_ignores_not_available_values(self):
        progress = RecordingProgress(seconds=3.0, size_bytes=10)
        apply_progress_line(progress, "out_time_us=N/A\n")
        apply_progress_line(progress, "total_size=N/A\n")
        self.assertEqual((progress.seconds, progress.size_bytes), (3.0, 10))


class LiveRecorderRecordTest(unittest.TestCase):
    def test_stop_sends_q_and_returns_exit_code(self):
        events = []
        started = time.monotonic()
        recorder, command_patch = _recorder(FAKE_FFMPEG_OBEYS_Q, lambda: len(events) >= 2, events)
        with command_patch:
            returncode = recorder.record()
        self.assertEqual(returncode, 0)
        self.assertGreaterEqual(len(events), 2)
        self.assertLess(time.monotonic() - started, 10)

    def test_ffmpeg_ignoring_q_is_killed_after_timeout(self):
        events = []
        recorder, command_patch = _recorder(FAKE_FFMPEG_IGNORES_Q, lambda: True, events)
        with command_patch, patch.object(LiveRecorder, "STOP_TIMEOUT_SECONDS", 0.5):
            started = time.monotonic()
            returncode = recorder.record()
        self.assertNotEqual(returncode, 0)
        self.assertLess(time.monotonic() - started, 10)

    def test_stream_end_finishes_without_stop(self):
        events = []
        recorder, command_patch = _recorder(FAKE_FFMPEG_STREAM_ENDS, lambda: False, events)
        with command_patch:
            returncode = recorder.record()
        self.assertEqual(returncode, 0)
        self.assertEqual(events[-1], (6.0, 3000))


class RemuxTest(unittest.TestCase):
    def test_extension_by_audio_codec(self):
        self.assertEqual(recording_extension(None), "mp4")
        self.assertEqual(recording_extension("best"), "m4a")
        self.assertEqual(recording_extension("mp3"), "mp3")

    def test_video_is_copied_to_mp4(self):
        self.assertEqual(build_remux_command("ffmpeg", "r.ts", "T.mp4", None), [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "r.ts",
            "-map", "0", "-c", "copy", "-movflags", "+faststart", "T.mp4",
        ])

    def test_audio_only_keeps_audio_without_reencoding(self):
        self.assertEqual(build_remux_command("ffmpeg", "r.ts", "T.m4a", "best"), [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "r.ts",
            "-map", "0:a", "-c", "copy", "-movflags", "+faststart", "T.m4a",
        ])

    def test_mp3_is_encoded_with_configured_bitrate(self):
        self.assertEqual(build_remux_command("ffmpeg", "r.ts", "T.mp3", "mp3"), [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "r.ts",
            "-map", "0:a", "-c:a", "libmp3lame", "-b:a", "192k", "T.mp3",
        ])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_live_recorder -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'live_recorder'`)

- [ ] **Step 3: 実装する**

`src/live_recorder.py`:

```python
"""「今から」のライブ録画(設計: docs/superpowers/specs/2026-10-02-youtube-live-recording-design.md)。

yt-dlpはライブのHLSをffmpegに渡して終わるまで待つだけで、進捗も途中での停止もできないため、
yt-dlpからは録画するURLだけを受け取り、ffmpegをここで直接動かす。録画中は、途中で途切れても
再生できるmpegts(.ts)に書き、終わってからmp4等へ入れ替える"""

import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from yt_dlp.utils import Popen

from clip_trimmer import FFmpegRunner
from config import CONFIG
from formats import Format
from paths import log_debug


@dataclass(frozen=True)
class LiveInput:
    """ffmpegに入力する1本の配信(YouTubeのライブは映像と音声が別々のことが多い)"""

    url: str
    http_headers: dict[str, str] = field(default_factory=dict)


def live_inputs(probe_info: Format) -> list[LiveInput]:
    """yt-dlpが選んだフォーマットから、ffmpegに入力する配信の一覧を作る"""
    formats = probe_info.get("requested_formats") or [probe_info]
    return [LiveInput(f["url"], dict(f.get("http_headers") or {})) for f in formats]


def build_record_command(ffmpeg: str, inputs: Sequence[LiveInput], output_path: str) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y"]
    for item in inputs:
        if item.http_headers:
            # YouTubeはyt-dlpが抽出時に使ったUser-Agent等と揃っていないと配信を返さないことがある
            cmd += ["-headers", "".join(f"{key}: {value}\r\n" for key, value in item.http_headers.items())]
        cmd += ["-i", item.url]
    for index in range(len(inputs)):
        cmd += ["-map", str(index)]
    cmd += ["-c", "copy", "-f", "mpegts", output_path]
    return cmd


@dataclass
class RecordingProgress:
    seconds: float = 0.0
    size_bytes: int = 0


def apply_progress_line(progress: RecordingProgress, line: str) -> bool:
    """ffmpegの-progress出力の1行を反映する。1回分の報告の区切り(progress=...)の行ならTrue。
    開始直後は値が"N/A"のことがあるため、数値でない値は無視して直前の値を保つ"""
    key, _, value = line.strip().partition("=")
    if key == "total_size" and value.isdigit():
        progress.size_bytes = int(value)
    elif key == "out_time_us" and value.isdigit():
        progress.seconds = int(value) / 1_000_000
    return key == "progress"


class LiveRecorder:
    """ffmpegで配信を録画する。配信が終わるか、should_stopがTrueを返すまで続ける"""

    # 標準入力に"q"を書いてから、ffmpegが書き終えて終了するのを待つ上限。
    # 通常は数秒で終わる(検証では約2秒)。超えたら強制終了する(.tsはそれまでの分が再生できる)
    STOP_TIMEOUT_SECONDS: float = 30
    _POLL_SECONDS = 0.2

    def __init__(
        self,
        ffmpeg_location: str | None,
        inputs: Sequence[LiveInput],
        output_path: str,
        on_progress: Callable[[RecordingProgress], None],
        should_stop: Callable[[], bool],
    ) -> None:
        self._executable = FFmpegRunner(ffmpeg_location).executable
        self._inputs = list(inputs)
        self._output_path = output_path
        self._on_progress = on_progress
        self._should_stop = should_stop
        self._stderr_tail: list[str] = []

    def record(self) -> int:
        """録画が終わるまで待ち、ffmpegの終了コードを返す"""
        if self._executable is None:
            raise FileNotFoundError("ffmpegが見つかりません")
        cmd = build_record_command(self._executable, self._inputs, self._output_path)
        # yt-dlpのPopenは、Windowsでコンソール窓を出さない設定とPyInstaller環境向けの環境変数の補正を行う
        with Popen(
            cmd, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as proc:
            # 標準エラーを読まずにいると、パイプが詰まってffmpegが止まるため、別のスレッドで読み続ける
            stderr_thread = threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True)
            stop_thread = threading.Thread(target=self._watch_stop, args=(proc,), daemon=True)
            stderr_thread.start()
            stop_thread.start()
            progress = RecordingProgress()
            assert proc.stdout is not None
            for line in proc.stdout:
                if apply_progress_line(progress, line):
                    self._on_progress(progress)
            returncode = proc.wait()
            stop_thread.join()
            stderr_thread.join()
        if returncode != 0:
            log_debug(f"LiveRecorder: ffmpegが終了コード{returncode}で終了 ({' / '.join(self._stderr_tail)})")
        return returncode

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            self._stderr_tail = [*self._stderr_tail[-4:], line.strip()]

    def _watch_stop(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None:
            if self._should_stop():
                self._stop(proc)
                return
            time.sleep(self._POLL_SECONDS)

    def _stop(self, proc: subprocess.Popen) -> None:
        """"q"でffmpegに書き終えて終わるよう頼む。強制終了すると最後の区切りが壊れることがあるため、
        応答がない場合に限る"""
        assert proc.stdin is not None
        try:
            proc.stdin.write("q")
            proc.stdin.flush()
        except OSError:
            # 既に終わり際でパイプが閉じている
            return
        try:
            proc.wait(timeout=self.STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            log_debug(f"LiveRecorder: ffmpegが{self.STOP_TIMEOUT_SECONDS}秒以内に終了しないため強制終了します")
            proc.kill()


def recording_extension(extract_audio_codec: str | None) -> str:
    """録画を入れ替える先の拡張子。音声のみの形式では音声だけを取り出す"""
    if extract_audio_codec == "mp3":
        return "mp3"
    if extract_audio_codec is not None:
        # YouTubeのライブの音声はAACのため、再エンコードせずm4aに入れられる
        return "m4a"
    return "mp4"


def build_remux_command(ffmpeg: str, ts_path: str, output_path: str, extract_audio_codec: str | None) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", ts_path]
    if extract_audio_codec == "mp3":
        cmd += ["-map", "0:a", "-c:a", "libmp3lame", "-b:a", f"{CONFIG.mp3_quality}k"]
    elif extract_audio_codec is not None:
        cmd += ["-map", "0:a", "-c", "copy", "-movflags", "+faststart"]
    else:
        cmd += ["-map", "0", "-c", "copy", "-movflags", "+faststart"]
    cmd.append(output_path)
    return cmd


def remux_recording(ffmpeg_location: str | None, ts_path: str, output_path: str, extract_audio_codec: str | None) -> None:
    """録画した.tsを、選ばれた形式のファイルへ入れ替える(映像・音声とも再エンコードしない。mp3のみ変換)"""
    executable = FFmpegRunner(ffmpeg_location).executable
    if executable is None:
        raise FileNotFoundError("ffmpegが見つかりません")
    cmd = build_remux_command(executable, ts_path, output_path, extract_audio_codec)
    _, stderr, returncode = Popen.run(
        cmd, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if returncode != 0:
        lines = (stderr or "").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"ffmpegが終了コード{returncode}で失敗しました")
```

注: `CONFIG.mp3_quality` は `"192"` のような kbps の数字(既存の `extract_audio_postprocessor` と同じ値)。

- [ ] **Step 4: 通ることを確認する**

Run: `python -m unittest tests.test_live_recorder -v && python -m ruff check src tests && python -m mypy`
Expected: 12件 OK、ruff・mypy ともエラーなし

- [ ] **Step 5: コミット**

```bash
git add src/live_recorder.py tests/test_live_recorder.py
git commit -m "ffmpegでライブを今から録画し、qで停止して保存する処理を追加する

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 今からの録画を `DownloadWorker` から使う

**Files:**
- Modify: `src/workers.py`(`DownloadWorker._run`、新しいメソッド)
- Test: `tests/test_workers.py`

**Interfaces:**
- Consumes: Task 3 の `_start_live_state` / `_end_live_state` / `LIVE_NO_DATA_ERROR`、Task 4 の `LiveRecorder`・`live_inputs`・`recording_extension`・`remux_recording`・`RecordingProgress`
- Produces: `live_mode="from_now"` の `DownloadRequest` で、録画 → 再格納 → 保存先への移動 → `finished_ok`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_workers.py` に追加する:

```python
class FakeLiveRecorder:
    """LiveRecorderの代わり。生成時に指定された.tsへ書き、指定の終了コードを返す"""

    data = b"ts-data"
    returncode = 0
    created: list = []

    def __init__(self, ffmpeg_location, inputs, output_path, on_progress, should_stop):
        self.output_path = output_path
        self.on_progress = on_progress
        FakeLiveRecorder.created.append(self)

    def record(self):
        from live_recorder import RecordingProgress
        if self.data:
            with open(self.output_path, "wb") as f:
                f.write(self.data)
        self.on_progress(RecordingProgress(seconds=754, size_bytes=345 * 1024 * 1024))
        return self.returncode


def fake_remux(ffmpeg_location, ts_path, output_path, extract_audio_codec):
    with open(ts_path, "rb") as src, open(output_path, "wb") as dst:
        dst.write(src.read())


class LiveFromNowRunTest(unittest.TestCase):
    def setUp(self):
        FakeLiveRecorder.data = b"ts-data"
        FakeLiveRecorder.returncode = 0
        FakeLiveRecorder.created = []

    def _run(self, tmp, **request):
        worker = make_worker(out_dir=tmp, live_mode="from_now", **request)
        oks, states, progress = [], [], []
        worker.finished_ok.connect(oks.append)
        worker.live_state_changed.connect(states.append)
        worker.progress.connect(lambda pct, text: progress.append((pct, text)))
        # run_workerのprobe_infoには配信のURLが無いため、ffmpegへの入力の組み立ても差し替える
        with patch("workers.LiveRecorder", FakeLiveRecorder), \
             patch("workers.live_inputs", return_value=[]), \
             patch("workers.remux_recording", side_effect=fake_remux) as remux_mock:
            errors, logs = run_worker(worker, RuntimeError("yt-dlpのダウンロードは使わない"), write_part=False)
        return oks, errors, logs, states, progress, remux_mock

    def test_recording_is_remuxed_and_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            oks, errors, _, states, progress, _ = self._run(tmp)
            self.assertEqual(errors, [])
            self.assertEqual(oks, [os.path.join(tmp, "My Video.mp4")])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])
            self.assertEqual(states, [True, False])
            self.assertIn((-1.0, "録画中 12:34 · 345.0MB"), progress)
            self.assertEqual(progress[-1], (-1.0, "配信終了・保存しています..."))

    def test_audio_only_format_is_saved_as_m4a(self):
        with tempfile.TemporaryDirectory() as tmp:
            postprocessors = [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}]
            oks, _, _, _, _, remux_mock = self._run(tmp, postprocessors=postprocessors)
            self.assertEqual(oks, [os.path.join(tmp, "My Video.m4a")])
            self.assertEqual(remux_mock.call_args.args[3], "best")

    def test_ffmpeg_failure_still_saves_what_was_recorded(self):
        """通信断等でffmpegが途中で止まっても、録画できた分には価値があるため保存する"""
        FakeLiveRecorder.returncode = 1
        with tempfile.TemporaryDirectory() as tmp:
            oks, errors, logs, _, _, _ = self._run(tmp)
            self.assertEqual(errors, [])
            self.assertEqual(len(oks), 1)
            self.assertTrue(any("録画が途中で止まりました" in msg for msg in logs))

    def test_nothing_recorded_is_reported_as_error(self):
        FakeLiveRecorder.data = b""
        with tempfile.TemporaryDirectory() as tmp:
            oks, errors, _, _, _, _ = self._run(tmp)
            self.assertEqual(oks, [])
            self.assertEqual(errors, ["録画できたデータがありません"])
            self.assertEqual(os.listdir(tmp), [])

    def test_stop_request_is_passed_to_recorder(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, live_mode="from_now")
            captured = {}

            class CapturingRecorder(FakeLiveRecorder):
                def __init__(self, ffmpeg_location, inputs, output_path, on_progress, should_stop):
                    super().__init__(ffmpeg_location, inputs, output_path, on_progress, should_stop)
                    captured["should_stop"] = should_stop

            with patch("workers.LiveRecorder", CapturingRecorder), \
                 patch("workers.live_inputs", return_value=[]), \
                 patch("workers.remux_recording", side_effect=fake_remux):
                run_worker(worker, RuntimeError("unused"), write_part=False)
            self.assertFalse(captured["should_stop"]())
            worker.cancel()
            self.assertTrue(captured["should_stop"]())
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_workers.LiveFromNowRunTest -v`
Expected: FAIL(`workers` に `LiveRecorder` が無い)

- [ ] **Step 3: 実装する**

`src/workers.py` の import に追加:

```python
from live_recorder import LiveRecorder, RecordingProgress, live_inputs, recording_extension, remux_recording
```

`_run` の try ブロックの、`download_opts = ...` から download の `with` ブロックまでを、次のように「今から」で分ける:

```python
            if self.request.live_mode == "from_now":
                self._record_live_from_now(probe_info, ffmpeg_location)
            else:
                download_opts = self._build_download_opts(expected_ext, ffmpeg_location)
                if self.request.live_mode is not None:
                    self._start_live_state(probe_info)
                with yt_dlp.YoutubeDL(download_opts) as ydl:
                    self._register_format_exclusion(ydl)
                    ydl.download([self.request.url])
```

(その後の `if self.request.live_mode is not None: ... _finish_success` は Task 3 のまま。今からの録画もそこで保存先へ移す)

新しいメソッドを `_build_download_opts` の後に追加:

```python
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
```

注: `_end_live_state` は `_is_cancelled` を見て「保存しています...」と「配信終了・保存しています...」を切り替える(Task 3)。`returncode != 0` かつ停止していない場合は配信終了ではなく途切れだが、表示は「配信終了・保存しています...」のままでよい(ログに理由が出る)。

- [ ] **Step 4: 通ることを確認する**

Run: `python -m unittest discover -s tests && python -m ruff check src tests && python -m mypy`
Expected: 全件 OK、ruff・mypy ともエラーなし

- [ ] **Step 5: コミット**

```bash
git add src/workers.py tests/test_workers.py
git commit -m "今からのライブ録画をダウンロードと同じ流れで保存する

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: ダウンロード一覧の1件のライブ表示(`DownloadTaskWidget`)

**Files:**
- Modify: `src/theme.py`、`src/widgets.py`(`DownloadTaskWidget`)
- Test: `tests/test_widgets.py`

**Interfaces:**
- Consumes: `live.LIVE_SAVING_TEXT`
- Produces:
  - `theme.LIVE_COLOR = "#d93025"`、`theme.LIVE_PROGRESS_BAR_STYLE`
  - `DownloadTaskWidget.stop_requested = pyqtSignal()`
  - `DownloadTaskWidget.set_live(live: bool) -> None`、`is_live: bool`(property)、`live_badge: QLabel`
  - `set_progress(percent, text)` は `percent < 0` で不定表示
  - `set_cancelling()` はライブでは「保存しています...」

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_widgets.py` に追加する:

```python
class DownloadTaskLiveTest(unittest.TestCase):
    def setUp(self):
        self.task = widgets.DownloadTaskWidget("title")
        self.addCleanup(self.task.deleteLater)

    def test_live_shows_badge_and_stop_button(self):
        self.task.set_live(True)
        self.assertFalse(self.task.live_badge.isHidden())
        self.assertEqual(self.task.live_badge.text(), "● LIVE")
        self.assertEqual(self.task.action_btn.text(), "停止")

    def test_normal_task_has_no_badge(self):
        self.assertTrue(self.task.live_badge.isHidden())
        self.assertEqual(self.task.action_btn.text(), "キャンセル")

    def test_stop_button_requests_stop_instead_of_cancel(self):
        self.task.set_live(True)
        stops, cancels = [], []
        self.task.stop_requested.connect(lambda: stops.append(True))
        self.task.cancel_requested.connect(lambda: cancels.append(True))
        self.task.action_btn.click()
        self.assertEqual((stops, cancels), ([True], []))

    def test_negative_percent_makes_bar_indeterminate(self):
        self.task.set_progress(-1.0, "録画中 0:05")
        self.assertEqual(self.task.progress_bar.maximum(), 0)
        self.assertEqual(self.task.status_label.text(), "録画中 0:05")

    def test_percent_after_indeterminate_restores_range(self):
        self.task.set_progress(-1.0, "録画中 0:05")
        self.task.set_progress(37.0, "さかのぼり取得中 37%")
        self.assertEqual((self.task.progress_bar.maximum(), self.task.progress_bar.value()), (100, 37))

    def test_stopping_live_shows_saving(self):
        self.task.set_live(True)
        self.task.set_cancelling()
        self.assertEqual(self.task.status_label.text(), "保存しています...")
        self.assertFalse(self.task.action_btn.isEnabled())

    def test_finished_after_indeterminate_shows_full_bar(self):
        self.task.set_live(True)
        self.task.set_progress(-1.0, "保存しています...")
        self.task.mark_finished("完了", saved_path="/out/x.mp4")
        self.assertEqual((self.task.progress_bar.maximum(), self.task.progress_bar.value()), (100, 100))
        self.assertEqual(self.task.action_btn.text(), "閉じる")
        self.assertFalse(self.task.live_badge.isHidden())

    def test_failed_after_indeterminate_stops_animation(self):
        self.task.set_progress(-1.0, "録画中 0:05")
        self.task.mark_finished("失敗しました")
        self.assertEqual(self.task.progress_bar.maximum(), 100)
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_widgets.DownloadTaskLiveTest -v`
Expected: FAIL(`live_badge` が無い)

- [ ] **Step 3: 実装する**

`src/theme.py` の末尾に追加:

```python
# 配信中のライブを表す赤(YouTubeのLIVE表示に近い色)。LIVEバッジと録画中の進捗バーに使う
LIVE_COLOR = "#d93025"
# 録画中の進捗バー。::chunkだけに色を付けるとスタイルシート描画に切り替わって溝が消えるため、溝も合わせて描く
LIVE_PROGRESS_BAR_STYLE = f"""
QProgressBar {{ border: none; border-radius: 3px; background-color: {PLACEHOLDER_BACKGROUND}; }}
QProgressBar::chunk {{ border-radius: 3px; background-color: {LIVE_COLOR}; }}
"""
```

`src/widgets.py` の theme の import に `LIVE_COLOR`、`LIVE_PROGRESS_BAR_STYLE` を加え、`from live import LIVE_SAVING_TEXT` を追加する。

`DownloadTaskWidget`:

docstring の末尾に1文追加: 「ライブの録画中(set_live)は赤いLIVEバッジを出し、ボタンはキャンセルではなく停止(それまでの分を保存)として働く」

シグナルに追加(`cancel_requested` の後):

```python
    # ライブの録画の停止(それまでの分を保存する)。確認は受け取った側で行う
    stop_requested = pyqtSignal()
```

`__init__` の `self._saved_path: str | None = None` の後に `self._live = False` を追加。footer の `footer.addWidget(self.spinner)` の直後に追加:

```python
        self.live_badge = QLabel("● LIVE")
        self.live_badge.setStyleSheet(f"color: {LIVE_COLOR}; font-weight: bold;")
        self.live_badge.setVisible(False)
        footer.addWidget(self.live_badge)
```

メソッドを追加・変更:

```python
    @property
    def is_live(self) -> bool:
        return self._live

    def set_live(self, live: bool) -> None:
        """ライブの録画の表示(赤いLIVEバッジ・赤い進捗バー・「停止」ボタン)にする。
        録画が終わって保存した後も、何の録画だったか分かるようバッジと色はそのまま残す"""
        self._live = live
        self.live_badge.setVisible(live)
        self.progress_bar.setStyleSheet(LIVE_PROGRESS_BAR_STYLE if live else "")
        if not self._finished:
            self.action_btn.setText("停止" if live else "キャンセル")

    def set_progress(self, percent: float, text: str) -> None:
        """percentが負の値なら、割合が無い(ライブの録画中・保存中)ことを表し、進捗バーを動き続ける表示にする"""
        if percent < 0:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(percent))
        self.status_label.setText(text)

    def set_cancelling(self) -> None:
        self.action_btn.setEnabled(False)
        self.status_label.setText(LIVE_SAVING_TEXT if self._live else "キャンセル中...")
```

`mark_finished` の `self.spinner.stop()` の直後に追加:

```python
        # 録画中の動き続ける表示のまま終わらないよう、割合の表示に戻す
        self.progress_bar.setRange(0, 100)
```

`_on_action_clicked` を変える:

```python
    def _on_action_clicked(self) -> None:
        if self._finished:
            self.dismiss_requested.emit()
        elif self._live:
            self.stop_requested.emit()
        else:
            self.cancel_requested.emit()
```

- [ ] **Step 4: 通ることを確認する**

Run: `python -m unittest discover -s tests && python -m ruff check src tests && python -m mypy`
Expected: 全件 OK、ruff・mypy ともエラーなし

- [ ] **Step 5: コミット**

```bash
git add src/theme.py src/widgets.py tests/test_widgets.py
git commit -m "ダウンロード一覧にライブの録画中の表示と停止ボタンを追加する

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: メイン画面のライブ表示と録画開始位置

**Files:**
- Modify: `src/main_window_ui.py`、`src/main_window.py`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: `live.LiveInfo`、`live.LiveMode`、`clip_range.format_clip_time`、`theme.LIVE_COLOR`、Task 2 の `MainWindow.live_info` と `on_formats_fetched(..., live_info=...)`
- Produces:
  - UI: `live_header: QWidget`、`live_badge_label: QLabel`、`live_elapsed_label: QLabel`、`live_start_container: QWidget`、`live_from_start_radio: QRadioButton`、`live_from_now_radio: QRadioButton`、`live_unavailable_label: QLabel`、定数 `DOWNLOAD_BUTTON_TEXT = "ダウンロード開始"`、`RECORD_BUTTON_TEXT = "録画開始"`
  - `MainWindow._show_live_info(live_info: LiveInfo | None) -> None`
  - `MainWindow._selected_live_mode() -> LiveMode | None`
  - `_build_download_request` が `live_mode` を設定する

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_main_window.py` の import に `import time` と `from main_window_ui import DOWNLOAD_BUTTON_TEXT, RECORD_BUTTON_TEXT` を追加し、追加する:

```python
class LiveFormTest(MainWindowTestCase):
    def _fetch_live(self, can_record_from_start=True, started_ago=7170):
        self.window.url_edit.setText("https://www.youtube.com/live/abc")
        worker = MagicMock()
        self.window.format_worker = worker
        live_info = LiveInfo(started_at=time.time() - started_ago, can_record_from_start=can_record_from_start)
        self.window.on_formats_fetched([make_video(), make_audio()], "Stream", b"", None, worker, live_info=live_info)

    def test_live_shows_badge_elapsed_time_and_record_button(self):
        self._fetch_live()
        self.assertTrue(self.window.live_header.isVisible())
        self.assertEqual(self.window.live_badge_label.text(), "● LIVE")
        self.assertEqual(self.window.live_elapsed_label.text(), "配信開始から 1:59:30")
        self.assertEqual(self.window.download_btn.text(), RECORD_BUTTON_TEXT)
        self.assertEqual(self.window.status_label.text(), "配信中のライブを検出しました")

    def test_live_that_can_go_back_offers_both_positions_defaulting_to_start(self):
        self._fetch_live()
        self.assertTrue(self.window.live_start_container.isVisible())
        self.assertTrue(self.window.live_from_start_radio.isChecked())
        self.assertEqual(self.window.live_from_start_radio.text(), "配信の最初から（1:59:30 さかのぼる）")
        self.assertTrue(self.window.live_from_now_radio.isVisible())
        self.assertFalse(self.window.live_unavailable_label.isVisible())
        self.assertEqual(self.window._selected_live_mode(), "from_start")

    def test_choosing_now(self):
        self._fetch_live()
        self.window.live_from_now_radio.setChecked(True)
        self.assertEqual(self.window._selected_live_mode(), "from_now")

    def test_live_that_cannot_go_back_shows_note_instead_of_choices(self):
        self._fetch_live(can_record_from_start=False)
        self.assertFalse(self.window.live_from_start_radio.isVisible())
        self.assertFalse(self.window.live_from_now_radio.isVisible())
        self.assertTrue(self.window.live_unavailable_label.isVisible())
        self.assertEqual(self.window._selected_live_mode(), "from_now")

    def test_options_not_usable_for_live_are_hidden_and_closed(self):
        self.window.detail_toggle_btn.setChecked(True)
        self._fetch_live()
        self.assertFalse(self.window.manual_toggle_btn.isVisible())
        self.assertFalse(self.window.detail_toggle_btn.isVisible())
        self.assertFalse(self.window.detail_container.isVisible())

    def test_elapsed_time_keeps_updating(self):
        self._fetch_live(started_ago=59)
        self.assertTrue(self.window._live_elapsed_timer.isActive())
        with patch.object(main_window_module.time, "time", return_value=self.window.live_info.started_at + 61):
            self.window._live_elapsed_timer.timeout.emit()
        self.assertEqual(self.window.live_elapsed_label.text(), "配信開始から 1:01")

    def test_unknown_start_time_shows_badge_only(self):
        self.window.url_edit.setText("https://www.youtube.com/live/abc")
        worker = MagicMock()
        self.window.format_worker = worker
        self.window.on_formats_fetched([], "Stream", b"", None, worker,
                                       live_info=LiveInfo(started_at=None, can_record_from_start=True))
        self.assertEqual(self.window.live_elapsed_label.text(), "")
        self.assertEqual(self.window.live_from_start_radio.text(), "配信の最初から")

    def test_changing_to_normal_video_restores_form(self):
        self._fetch_live()
        self.window.url_edit.setText("https://example.com/watch?v=x")
        self.assertFalse(self.window.live_header.isVisible())
        self.assertFalse(self.window.live_start_container.isVisible())
        self.assertTrue(self.window.manual_toggle_btn.isVisible())
        self.assertTrue(self.window.detail_toggle_btn.isVisible())
        self.assertEqual(self.window.download_btn.text(), DOWNLOAD_BUTTON_TEXT)
        self.assertFalse(self.window._live_elapsed_timer.isActive())

    def test_live_title_and_badge_fit_in_preview(self):
        """LIVE見出しの分だけタイトルの高さを詰め、サムネイルの高さからはみ出さない"""
        self._fetch_live()
        header = self.window.live_header.maximumHeight()
        self.assertLessEqual(header + self.window.title_label.maximumHeight(), self.window.thumbnail_label.height())

    def test_download_request_carries_live_mode(self):
        self._fetch_live()
        self.window.out_edit.setText(OUT_DIR)
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"):
            request = self.window._build_download_request()
        self.assertEqual(request.live_mode, "from_start")
        self.assertIsNone(request.clip_start)
```

注: `_build_download_request` は「動画 (最高画質 mp4)」で `confirm_high_resolution_download` を通る。`make_video()` は 1080p のため確認ダイアログは出ない。

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_main_window.LiveFormTest -v`
Expected: FAIL(`DOWNLOAD_BUTTON_TEXT` の ImportError)

- [ ] **Step 3: 実装する**

`src/main_window_ui.py`:

import に `QRadioButton` を加え、theme の import に `LIVE_COLOR` を加える。`APP_TITLE` の後に定数を追加:

```python
DOWNLOAD_BUTTON_TEXT = "ダウンロード開始"
# 配信中のライブを表示している間のダウンロードボタンの文言
RECORD_BUTTON_TEXT = "録画開始"
```

クラス属性の型注釈の並び(`download_panel: QWidget` 等の近く)に追加:

```python
    live_header: QWidget
    live_badge_label: QLabel
    live_elapsed_label: QLabel
    live_start_container: QWidget
    live_from_start_radio: QRadioButton
    live_from_now_radio: QRadioButton
    live_unavailable_label: QLabel
```

動画プレビューの、`self.title_label = QLabel("", preview_container)` から `preview_row.addWidget(self.title_label, stretch=1)` までを、次のように置き換える(タイトルの上に LIVE 見出しを置く縦並び):

```python
        # タイトルの上に、配信中のライブのときだけLIVE見出し(バッジ・配信開始からの経過時間)を出す。
        # 上下の伸縮で、見出しの有無にかかわらずタイトルを縦方向の中央に置く
        text_column = QWidget(preview_container)
        text_layout = QVBoxLayout(text_column)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        text_layout.addStretch(1)
        self.live_header = QWidget()
        live_header_layout = QHBoxLayout(self.live_header)
        live_header_layout.setContentsMargins(0, 0, 0, 0)
        live_header_layout.setSpacing(8)
        self.live_badge_label = QLabel("● LIVE")
        self.live_badge_label.setStyleSheet(f"color: {LIVE_COLOR}; font-weight: bold;")
        live_header_layout.addWidget(self.live_badge_label)
        self.live_elapsed_label = QLabel("")
        self.live_elapsed_label.setTextFormat(Qt.TextFormat.PlainText)
        self.live_elapsed_label.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
        live_header_layout.addWidget(self.live_elapsed_label)
        live_header_layout.addStretch(1)
        self.live_header.setFixedHeight(self.live_badge_label.sizeHint().height())
        self.live_header.setVisible(False)
        text_layout.addWidget(self.live_header)
        # 動画タイトルは外部由来の文字列のため、"<b>"等がHTMLとして描画されないようにする
        # (他の内容が変わるラベルも同様。dialogs参照)
        self.title_label = QLabel("")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumHeight(THUMBNAIL_SIZE.height())
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        text_layout.addWidget(self.title_label)
        text_layout.addStretch(1)
        preview_row.addWidget(text_column, stretch=1)
```

`layout.addWidget(self.auto_format_note_label)` の直後に追加:

```python
        # --- 録画開始位置(配信中のライブのときだけ) ---
        # 選択肢が2つだけのため、コンボではなく両方が常に見えるラジオボタンにする
        self.live_start_container = QWidget()
        live_start_layout = QHBoxLayout(self.live_start_container)
        live_start_layout.setContentsMargins(0, 0, 0, 0)
        live_start_layout.addWidget(QLabel("録画開始位置:"))
        self.live_from_start_radio = QRadioButton("配信の最初から")
        self.live_from_start_radio.setChecked(True)
        live_start_layout.addWidget(self.live_from_start_radio)
        self.live_from_now_radio = QRadioButton("今から")
        live_start_layout.addWidget(self.live_from_now_radio)
        # さかのぼれない配信では、選べない選択肢を並べる代わりにこの一文だけを出す
        self.live_unavailable_label = QLabel("今から録画します（この配信はさかのぼって保存できません）")
        self.live_unavailable_label.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
        live_start_layout.addWidget(self.live_unavailable_label)
        live_start_layout.addStretch(1)
        self.live_start_container.setVisible(False)
        layout.addWidget(self.live_start_container)
```

`self.download_btn = QPushButton("ダウンロード開始")` を `self.download_btn = QPushButton(DOWNLOAD_BUTTON_TEXT)` に変える。

`src/main_window.py`:

import: `from clip_range import (...)` は `format_clip_time` を既に含む。`from live import LiveInfo, LiveMode`、`main_window_ui` の import に `DOWNLOAD_BUTTON_TEXT, RECORD_BUTTON_TEXT, THUMBNAIL_SIZE` を加える。

`__init__` の `_info_fetch_timer` の設定の後に追加:

```python
        # ライブの「配信開始から」の経過時間を1秒ごとに更新する(ライブを表示している間だけ動かす)
        self._live_elapsed_timer = QTimer(self)
        self._live_elapsed_timer.setInterval(1000)
        self._live_elapsed_timer.timeout.connect(self._update_live_elapsed)
```

`_clear_video_info` の `self.live_info = None`(Task 2 で追加)を `self._show_live_info(None)` に置き換える。

`on_formats_fetched` の `self.live_info = live_info`(Task 2 で追加)を削除し、`self.status_label.setText(f"動画{video_count}件・音声{audio_count}件を検出しました")` を次に置き換える:

```python
        self._show_live_info(live_info)
        if live_info is not None:
            self.status_label.setText("配信中のライブを検出しました")
        else:
            self.status_label.setText(f"動画{video_count}件・音声{audio_count}件を検出しました")
```

新しいメソッドを `_update_clip_slider_range` の前に追加:

```python
    def _show_live_info(self, live_info: LiveInfo | None) -> None:
        """配信中のライブかどうかに合わせて、LIVE見出し・録画開始位置の行・ボタンの文言を切り替える。

        手動設定(フォーマットIDがさかのぼり用と今から用で異なる)と切り抜き範囲(長さが決まらない)は
        ライブでは使えないため、無効表示ではなく隠す。通常の動画に戻ったら元に戻す"""
        was_live = self.live_info is not None
        self.live_info = live_info
        is_live = live_info is not None
        if is_live:
            self.manual_toggle_btn.setChecked(False)
            self.detail_toggle_btn.setChecked(False)
        self.manual_toggle_btn.setVisible(not is_live)
        self.detail_toggle_btn.setVisible(not is_live)
        self.live_header.setVisible(is_live)
        self.live_start_container.setVisible(is_live)
        can_go_back = live_info is not None and live_info.can_record_from_start
        self.live_from_start_radio.setVisible(can_go_back)
        self.live_from_now_radio.setVisible(can_go_back)
        self.live_unavailable_label.setVisible(is_live and not can_go_back)
        self.live_from_start_radio.setChecked(True)
        self.download_btn.setText(RECORD_BUTTON_TEXT if is_live else DOWNLOAD_BUTTON_TEXT)
        # LIVE見出しの分だけタイトルを詰め、サムネイルの高さに収める
        title_height = THUMBNAIL_SIZE.height()
        if is_live:
            title_height -= self.live_header.maximumHeight() + 2
        self.title_label.setMaximumHeight(title_height)
        if is_live:
            self._update_live_elapsed()
            self._live_elapsed_timer.start()
        else:
            self._live_elapsed_timer.stop()
        if is_live != was_live:
            # URLを打ち替えるたびにウィンドウの高さを合わせ直さないよう、ライブと通常の動画が切り替わる時だけにする
            self._sync_window_height()

    def _update_live_elapsed(self) -> None:
        started_at = self.live_info.started_at if self.live_info is not None else None
        if started_at is None:
            self.live_elapsed_label.setText("")
            self.live_from_start_radio.setText("配信の最初から")
            return
        elapsed = format_clip_time(max(0.0, time.time() - started_at))
        self.live_elapsed_label.setText(f"配信開始から {elapsed}")
        self.live_from_start_radio.setText(f"配信の最初から（{elapsed} さかのぼる）")

    def _selected_live_mode(self) -> LiveMode | None:
        if self.live_info is None:
            return None
        if self.live_info.can_record_from_start and self.live_from_start_radio.isChecked():
            return "from_start"
        return "from_now"
```

`_build_download_request` の `DownloadRequest(...)` に `live_mode=self._selected_live_mode(),` を追加する(`clip_end=clip_end,` の後)。

- [ ] **Step 4: 通ることを確認する**

Run: `python -m unittest discover -s tests && python -m ruff check src tests && python -m mypy`
Expected: 全件 OK(既存の `WindowHeightTest.test_widgets_do_not_overlap_on_startup` も通る)、ruff・mypy ともエラーなし

- [ ] **Step 5: コミット**

```bash
git add src/main_window_ui.py src/main_window.py tests/test_main_window.py
git commit -m "配信中のライブにLIVE表示と録画開始位置の選択を出す

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 録画中のタスクを一覧の上部に固定し、停止を確認する

**Files:**
- Modify: `src/main_window_ui.py`(`_build_download_panel`)、`src/main_window.py`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: Task 3 の `DownloadWorker.live_state_changed`、Task 6 の `DownloadTaskWidget.set_live` / `stop_requested` / `is_live`、Task 7 の `_build_download_request`(`live_mode` 付き)
- Produces:
  - UI: `pinned_list_container: QWidget`、`pinned_list_layout: QVBoxLayout`
  - `MainWindow._add_task_widget(task, pinned=False)`、`MainWindow._unpin_task_widget(task)`、`MainWindow.request_stop_recording(worker)`
  - `MainWindow._is_recording(worker) -> bool`(staticmethod。Task 9 で使う)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_main_window.py` に追加する:

```python
class LiveTaskPinningTest(MainWindowTestCase):
    def _start_live_task(self, live_mode_now=False):
        self.window.url_edit.setText(f"https://www.youtube.com/live/{id(self)}{live_mode_now}")
        self.window.out_edit.setText(OUT_DIR)
        fetch_worker = MagicMock()
        self.window.format_worker = fetch_worker
        self.window.on_formats_fetched([make_video(), make_audio()], "Stream", b"", None, fetch_worker,
                                       live_info=LiveInfo(started_at=None, can_record_from_start=True))
        if live_mode_now:
            self.window.live_from_now_radio.setChecked(True)
        worker = MagicMock()
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(main_window_module.os, "makedirs"), \
             patch.object(main_window_module, "DownloadWorker", return_value=worker) as worker_cls:
            self.window.start_download()
        request = worker_cls.call_args.args[0]
        worker.request = request
        return worker, self.window._download_tasks[worker]

    def _start_normal_task(self):
        self.window.url_edit.setText("https://example.com/watch?v=normal")
        self.window.out_edit.setText(OUT_DIR)
        self.window.title_label.setText("normal")
        worker = MagicMock()
        worker.request.live_mode = None
        with patch.object(main_window_module, "get_ffmpeg_location", return_value="C:/ffmpeg"), \
             patch.object(main_window_module.os, "makedirs"), \
             patch.object(main_window_module, "DownloadWorker", return_value=worker):
            self.window.start_download()
        return worker, self.window._download_tasks[worker]

    def test_recording_task_is_pinned_above_the_scrolling_list(self):
        _, task = self._start_live_task()
        self.assertGreaterEqual(self.window.pinned_list_layout.indexOf(task), 0)
        self.assertTrue(self.window.pinned_list_container.isVisible())
        self.assertTrue(task.is_live)

    def test_normal_task_started_later_does_not_push_recording_down(self):
        _, live_task = self._start_live_task()
        _, normal_task = self._start_normal_task()
        self.assertGreaterEqual(self.window.pinned_list_layout.indexOf(live_task), 0)
        self.assertEqual(self.window.download_list_layout.indexOf(normal_task), 0)

    def test_live_end_moves_task_to_top_of_normal_list(self):
        worker, task = self._start_live_task()
        self._start_normal_task()
        live_state_slot = worker.live_state_changed.connect.call_args.args[0]
        live_state_slot(False)
        self.assertEqual(self.window.pinned_list_layout.indexOf(task), -1)
        self.assertEqual(self.window.download_list_layout.indexOf(task), 0)
        self.assertFalse(self.window.pinned_list_container.isVisible())

    def test_failed_recording_is_unpinned_too(self):
        worker, task = self._start_live_task()
        with patch.object(dialogs, "critical"):
            self.window.on_download_error(worker, "録画できたデータがありません")
        self.assertEqual(self.window.pinned_list_layout.indexOf(task), -1)
        self.assertEqual(self.window.download_list_layout.indexOf(task), 0)

    def test_dismissing_last_task_hides_panel(self):
        worker, task = self._start_live_task()
        self.window.on_download_finished_ok(worker, SAVED_PATH)
        self.window._remove_task_widget(task)
        self.assertFalse(self.window.download_panel.isVisible())

    def test_stop_asks_before_stopping(self):
        worker, task = self._start_live_task()
        with patch.object(dialogs, "question", return_value=QMessageBox.StandardButton.No) as question_mock:
            task.stop_requested.emit()
        self.assertIn("録画を停止して保存しますか?", question_mock.call_args.args[2])
        worker.cancel.assert_not_called()

        with patch.object(dialogs, "question", return_value=QMessageBox.StandardButton.Yes):
            task.stop_requested.emit()
        worker.cancel.assert_called_once()
        self.assertEqual(task.status_label.text(), "保存しています...")

    def test_request_carries_selected_live_mode(self):
        worker, _ = self._start_live_task(live_mode_now=True)
        self.assertEqual(worker.request.live_mode, "from_now")
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_main_window.LiveTaskPinningTest -v`
Expected: FAIL(`pinned_list_layout` が無い)

- [ ] **Step 3: 実装する**

`src/main_window_ui.py`:

型注釈に `pinned_list_container: QWidget` と `pinned_list_layout: QVBoxLayout` を追加する。`_build_download_panel` の `panel_layout.addWidget(QLabel("ダウンロード"))` の直後に追加:

```python
        # ライブの録画中のタスクは、スクロール領域の外のここに置く。通常のダウンロードが何件あっても
        # スクロールで見えなくならないよう、一覧の上部に固定する(録画が終わったら下の一覧へ移す)
        self.pinned_list_container = QWidget()
        self.pinned_list_layout = QVBoxLayout(self.pinned_list_container)
        self.pinned_list_layout.setContentsMargins(0, 0, 0, 0)
        self.pinned_list_layout.setSpacing(6)
        self.pinned_list_container.setVisible(False)
        panel_layout.addWidget(self.pinned_list_container)
```

`src/main_window.py`:

`start_download` の、`task = DownloadTaskWidget(...)` から `task.reveal_requested.connect(self._reveal_saved_file)` までを変える:

```python
        task = DownloadTaskWidget(title, self.thumbnail_label.pixmap())
        is_recording = request.live_mode is not None
        task.set_live(is_recording)
        self._download_tasks[worker] = task
        self._add_task_widget(task, pinned=is_recording)

        worker.progress.connect(task.set_progress)
        # 複数のダウンロードのログが混ざるため、どの動画のものか分かるように添える
        worker.log.connect(lambda msg, t=title: self.append_log(f"[{t}] {msg}"))
        worker.finished_ok.connect(lambda saved_path, w=worker: self.on_download_finished_ok(w, saved_path))
        worker.finished_error.connect(lambda message, w=worker: self.on_download_error(w, message))
        worker.cancelled.connect(lambda w=worker: self.on_download_cancelled(w))
        worker.live_state_changed.connect(lambda live, t=task: self._on_live_state_changed(t, live))
        task.cancel_requested.connect(lambda w=worker: self.cancel_download(w))
        task.stop_requested.connect(lambda w=worker: self.request_stop_recording(w))
        task.dismiss_requested.connect(lambda t=task: self._remove_task_widget(t))
        task.reveal_requested.connect(self._reveal_saved_file)
```

`_is_downloading_url` の後に追加:

```python
    @staticmethod
    def _is_recording(worker: DownloadWorker) -> bool:
        return worker.request.live_mode is not None
```

`_add_task_widget` と `_remove_task_widget` を置き換え、`_unpin_task_widget` と `_on_live_state_changed` を追加する:

```python
    def _add_task_widget(self, task: DownloadTaskWidget, pinned: bool = False) -> None:
        # 新しいものほど上に表示する。録画中のものはスクロールしない上部の固定領域に置く
        if pinned:
            self.pinned_list_layout.insertWidget(0, task)
            self.pinned_list_container.setVisible(True)
        else:
            self.download_list_layout.insertWidget(0, task)
        self._set_download_panel_visible(True)

    def _unpin_task_widget(self, task: DownloadTaskWidget) -> None:
        """録画が終わったタスクを固定領域から外し、通常の一覧の先頭へ移す(固定されていなければ何もしない)"""
        if self.pinned_list_layout.indexOf(task) < 0:
            return
        self.pinned_list_layout.removeWidget(task)
        self.pinned_list_container.setVisible(self.pinned_list_layout.count() > 0)
        self.download_list_layout.insertWidget(0, task)

    def _remove_task_widget(self, task: DownloadTaskWidget) -> None:
        self._unpin_task_widget(task)
        self.download_list_layout.removeWidget(task)
        task.deleteLater()
        # 末尾の伸縮用スペーサーだけが残ったら一覧を隠す
        if self.download_list_layout.count() <= 1:
            self._set_download_panel_visible(False)

    def _on_live_state_changed(self, task: DownloadTaskWidget, live: bool) -> None:
        if not live:
            self._unpin_task_widget(task)
```

`cancel_download` の後に追加:

```python
    def request_stop_recording(self, worker: DownloadWorker) -> None:
        """今からの録画は止めると取り直せないため、押し間違いでないか確認してから止める"""
        reply = dialogs.question(
            self,
            "録画の停止",
            "録画を停止して保存しますか?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.cancel_download(worker)
```

`_release_download_worker` を変え、終わったタスクは必ず固定を外す(`live_state_changed(False)` が来ないまま失敗で終わった場合に備える):

```python
    def _release_download_worker(self, worker: DownloadWorker) -> DownloadTaskWidget | None:
        """終わったダウンロードワーカーを手放し、その表示を返す。完了の通知はrun()が
        戻る直前に届くため、参照を手放す前に終了を待つ(走行中のQThreadが破棄されるとプロセスごと落ちる)。
        録画の表示は、終わり方によらず上部の固定から外す"""
        worker.wait()
        task = self._download_tasks.pop(worker, None)
        if task is not None:
            self._unpin_task_widget(task)
        return task
```

- [ ] **Step 4: 通ることを確認する**

Run: `python -m unittest discover -s tests && python -m ruff check src tests && python -m mypy`
Expected: 全件 OK、ruff・mypy ともエラーなし

注: 既存の `_start_task` 等で `DownloadWorker` を `MagicMock` にしているテストでは、`worker.live_state_changed.connect` も `MagicMock` で受けられるため変更は要らない。

- [ ] **Step 5: コミット**

```bash
git add src/main_window_ui.py src/main_window.py tests/test_main_window.py
git commit -m "録画中のタスクをダウンロード一覧の上部に固定し、停止の前に確認する

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: 録画中にアプリを閉じたら、保存を終えてから終了する

**Files:**
- Modify: `src/main_window.py`(`__init__`、`closeEvent`、完了・失敗・キャンセルのハンドラ)
- Test: `tests/test_main_window.py`(`CloseEventTest` の `_running_worker` と新しいテスト)

**Interfaces:**
- Consumes: Task 8 の `_is_recording`、`cancel_download`
- Produces: `MainWindow._close_confirmed: bool`、`MainWindow._saving_dialog: QProgressDialog | None`、`MainWindow._close_after_recordings() -> None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_main_window.py` の `CloseEventTest._running_worker` を、通常のダウンロードであることを明示するよう変える:

```python
    @staticmethod
    def _running_worker(live_mode=None):
        worker = MagicMock()
        worker.isRunning.return_value = True
        worker.wait.return_value = True
        worker.request.live_mode = live_mode
        return worker
```

同じく、`ProgressAndFinishHandlersTest._start_task`(`worker.request.out_dir = OUT_DIR` を設定している helper)にも `worker.request.live_mode = None` を追加する。`MagicMock` の属性は `None` ではないため、設定しないと録画として扱われ、終了時に保存待ちへ入ってしまう。

`CloseEventTest` に追加する:

```python
    def test_recording_is_stopped_and_close_waits_for_saving(self):
        recording = self._running_worker(live_mode="from_now")
        self.window._download_tasks[recording] = DownloadTaskWidget("stream")
        event = self._event()

        with patch.object(dialogs, "question", return_value=QMessageBox.StandardButton.Yes) as question_mock:
            self.window.closeEvent(event)

        self.assertIn("録画中の1件を停止・保存", question_mock.call_args.args[2])
        recording.cancel.assert_called_once()
        # 保存(結合・再格納)は数分かかることがあるため、ここでは待たずに終了をいったん取り消す
        recording.wait.assert_not_called()
        event.ignore.assert_called_once()
        self.assertIsNotNone(self.window._saving_dialog)

        recording.isRunning.return_value = False
        with patch.object(MainWindow, "close") as close_mock:
            self.window.on_download_finished_ok(recording, SAVED_PATH)
        close_mock.assert_called_once()
        self.assertIsNone(self.window._saving_dialog)

    def test_prompt_mentions_both_recordings_and_downloads(self):
        recording = self._running_worker(live_mode="from_start")
        download = self._running_worker()
        self.window._download_tasks[recording] = DownloadTaskWidget("stream")
        self.window._download_tasks[download] = DownloadTaskWidget("video")

        with patch.object(dialogs, "question", return_value=QMessageBox.StandardButton.No) as question_mock:
            self.window.closeEvent(self._event())

        self.assertEqual(
            question_mock.call_args.args[2],
            "録画中の1件を停止・保存し、ダウンロード中の1件を中止して終了しますか?",
        )

    def test_second_close_after_saving_does_not_ask_again(self):
        recording = self._running_worker(live_mode="from_now")
        self.window._download_tasks[recording] = DownloadTaskWidget("stream")
        with patch.object(dialogs, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.closeEvent(self._event())

        recording.isRunning.return_value = False
        with patch.object(MainWindow, "close"):
            self.window._close_after_recordings()  # 保存が終わり、改めて閉じる
        event = self._event()
        with patch.object(dialogs, "question") as question_mock:
            self.window.closeEvent(event)
        question_mock.assert_not_called()
        event.accept.assert_called_once()

    def test_closing_again_while_saving_is_ignored_quietly(self):
        recording = self._running_worker(live_mode="from_now")
        self.window._download_tasks[recording] = DownloadTaskWidget("stream")
        with patch.object(dialogs, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.closeEvent(self._event())

        event = self._event()
        with patch.object(dialogs, "question") as question_mock:
            self.window.closeEvent(event)
        question_mock.assert_not_called()
        event.ignore.assert_called_once()
        recording.cancel.assert_called_once()

    def test_recording_error_while_closing_does_not_block_with_dialog(self):
        recording = self._running_worker(live_mode="from_now")
        self.window._download_tasks[recording] = DownloadTaskWidget("stream")
        with patch.object(dialogs, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.closeEvent(self._event())

        recording.isRunning.return_value = False
        with patch.object(dialogs, "critical") as critical_mock, patch.object(MainWindow, "close") as close_mock:
            self.window.on_download_error(recording, "録画できたデータがありません")
        critical_mock.assert_not_called()
        close_mock.assert_called_once()
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_main_window.CloseEventTest -v`
Expected: 新しい5件が FAIL(`_saving_dialog` が無い、確認文が異なる)、既存のテストは OK

- [ ] **Step 3: 実装する**

`src/main_window.py` の `__init__` の `self._update_progress_dialog: QProgressDialog | None = None` の後に追加:

```python
        # 終了の確認で「はい」を選んだ後か(録画の保存を待ってから閉じ直すときに、確認を出し直さないため)
        self._close_confirmed = False
        # 終了時に録画の保存を待っている間に出す「保存しています」ダイアログ
        self._saving_dialog: QProgressDialog | None = None
```

`closeEvent` の冒頭(`assert event is not None` の後)から `for download in running_downloads: self.cancel_download(download)` までを次に置き換える:

```python
        if self._saving_dialog is not None:
            # 録画の保存を待っている間に、もう一度閉じようとした。保存が終われば自動で閉じる
            event.ignore()
            return

        running_downloads = self._running_downloads()
        if running_downloads and not self._close_confirmed:
            reply = dialogs.question(
                self,
                "ダウンロード中",
                self._close_confirmation_text(running_downloads),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                if self._pending_update_path is not None:
                    self._pending_update_path = None
                    if self._update_progress_dialog is not None:
                        self._update_progress_dialog.close()
                        self._update_progress_dialog.deleteLater()
                        self._update_progress_dialog = None
                    dialogs.information(
                        self, "アップデート", "アップデートを中止しました。次回起動時に改めて確認します。"
                    )
                event.ignore()
                return
            self._close_confirmed = True
        for download in running_downloads:
            self.cancel_download(download)

        if any(self._is_recording(w) for w in running_downloads):
            # 録画の保存(結合・再格納)は長い録画では数分かかることがあり、待ち時間に上限を設けると
            # 録画を失う。UIスレッドで待つと「保存しています」の表示も描画されないため、終了はいったん
            # 取り消し、録画が全部終わったところで改めて閉じる(_close_after_recordings)
            self._show_saving_dialog()
            event.ignore()
            return
```

(以降の「cancel()は進捗フック経由でしか効かず…」からの既存の処理はそのまま)

新しいメソッドを `closeEvent` の後に追加:

```python
    def _close_confirmation_text(self, running_downloads: list[DownloadWorker]) -> str:
        recordings = sum(1 for w in running_downloads if self._is_recording(w))
        downloads = len(running_downloads) - recordings
        parts = []
        if recordings:
            parts.append(f"録画中の{recordings}件を停止・保存し")
        if downloads:
            parts.append(f"ダウンロード中の{downloads}件を中止し")
        return "、".join(parts) + "て終了しますか?"

    def _show_saving_dialog(self) -> None:
        dialog = QProgressDialog("録画を保存しています。保存が終わると自動的に終了します...", None, 0, 0, self)
        dialog.setWindowTitle("終了")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.show()
        self._saving_dialog = dialog

    def _close_after_recordings(self) -> None:
        """終了の待ちの間に録画が全部終わったら、改めてウィンドウを閉じる"""
        if self._saving_dialog is None:
            return
        if any(self._is_recording(w) for w in self._running_downloads()):
            return
        self._saving_dialog.close()
        self._saving_dialog.deleteLater()
        self._saving_dialog = None
        self.close()
```

注: 確認文は「…中止し**て**終了しますか?」になるよう、`parts` の最後の「し」に「て終了しますか?」を続けている。録画だけなら「録画中の1件を停止・保存して終了しますか?」、両方なら「録画中の1件を停止・保存し、ダウンロード中の1件を中止して終了しますか?」、通常のみなら「ダウンロード中の2件を中止して終了しますか?」。既存のテストで確認文の全文を比べているものがあれば、この新しい文に合わせて更新する。

`on_download_finished_ok`・`on_download_error`・`on_download_cancelled` の各末尾に `self._close_after_recordings()` を追加する。`on_download_error` のダイアログは、終了の待ちの間は出さない(モーダルで終了が止まるため。理由はログに残っている):

```python
    def on_download_error(self, worker: DownloadWorker, message: str) -> None:
        task = self._release_download_worker(worker)
        if task is not None:
            task.mark_finished("失敗しました")
        if not self._close_confirmed:
            # 並列に走っている中のどれが失敗したのか分かるよう、タイトルを添える
            title = task.title_label.text() if task is not None else worker.request.url
            dialogs.critical(self, "ダウンロード失敗", f"{title}\n\n{message}")
        self._close_after_recordings()
```

`_running_downloads` は終わったワーカーを `_download_tasks` から外す前に呼ばれることがあるため、`_close_after_recordings` は `_release_download_worker` の後(ワーカーが一覧から外れた後)に呼ぶこと。

- [ ] **Step 4: 通ることを確認する**

Run: `python -m unittest discover -s tests && python -m ruff check src tests && python -m mypy`
Expected: 全件 OK、ruff・mypy ともエラーなし

- [ ] **Step 5: コミット**

```bash
git add src/main_window.py tests/test_main_window.py
git commit -m "録画中にアプリを閉じたら、停止して保存を終えてから終了する

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: README と実際の配信での確認

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1〜9 の全て

- [ ] **Step 1: README の機能一覧に追加する**

`README.md` の「## 機能」の「- ダウンロードのキャンセル(1件ごと)」の後に追加:

```markdown
- YouTube ライブ配信の録画
  - 配信の最初からさかのぼって(さかのぼれる配信の場合)、または今から録画
  - 「停止」またはアプリの終了で、それまでの分を保存(配信が終わった場合も自動で保存)
  - 録画中はダウンロード一覧の上部に固定して表示
```

- [ ] **Step 2: 全体の確認**

Run: `python -m unittest discover -s tests && python -m ruff check src tests && python -m mypy`
Expected: 全件 OK、ruff・mypy ともエラーなし

- [ ] **Step 3: 実際の配信で確認する(手動)**

アプリを起動し(`python src/main.py`)、配信中の YouTube ライブ(例: 確認時点で配信中のもの)で次を確かめる。各ファイルは ffprobe で長さとストリームを確認する(`ffprobe -v error -show_entries format=duration:stream=codec_name,height -of compact <file>`)。

1. URL を入れると「● LIVE」「配信開始から H:MM:SS」(1秒ごとに進む)、「録画開始位置」(配信の最初からが選択済み)、「録画開始」ボタンが出て、手動設定・詳細設定が隠れる。
2. 配信の最初から: 録画開始 → 一覧の上部に固定され、「さかのぼり取得中 N%」が進む → 30秒ほどで「停止」→ 確認で「はい」→「保存しています...」→「完了」になり、通常の一覧の先頭へ移る。保存されたファイルが再生でき、長さが「取得できた分」になっている。
3. 今から: 録画開始 → 「録画中 0:12 · 2.0MB」のように進み、バーが赤く動き続ける → 停止 → 保存された mp4 が再生できる。
4. 「音声のみ (mp3)」で今から録画 → 停止 → mp3 が再生できる。
5. 録画中に通常の動画を何件かダウンロードし、一覧がスクロールしても録画のタスクが上部に見え続ける。
6. 今からの録画中にアプリを閉じる →「録画中の1件を停止・保存して終了しますか?」→ はい →「録画を保存しています...」が出て、保存後に自動で終了し、ファイルが保存先にある。
7. 通常の動画の URL に戻すと、表示が元に戻る(LIVE・録画開始位置が消え、手動設定・詳細設定が出る、ボタンが「ダウンロード開始」)。
8. Windows では、今からの録画の停止で ffmpeg のコンソール窓が出ないこと、停止後に作業フォルダ(`.ytdl-working-…`)が残らないことも確認する。

問題があれば、該当する Task に戻って直し、テストを追加する。

- [ ] **Step 4: コミット**

```bash
git add README.md
git commit -m "READMEにライブ配信の録画を追加する

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
