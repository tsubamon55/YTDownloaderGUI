# yt-dlp 非公開 ctx 契約への依存の解消 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `yt_dlp_selection.py` の `_build_ctx`(yt-dlp 非公開の ctx 形の写し)をなくし、フォーマット選択と非推奨フォーマットの除外を yt-dlp の通常経路と公式の拡張点(`pre_process` ポストプロセッサ)だけで行う。

**Architecture:** プレビュー用の `select_formats` は、最小の info dict を `YoutubeDL.process_ie_result(download=False)` に渡して選択させる。ダウンロード時の除外は、`info["formats"]` を絞り込む `ExcludeFormatsPP` を `when="pre_process"` で `YoutubeDL` に登録して行う。`DownloadWorker` は `format` に常に文字列の format_spec を渡し、自動設定のときだけ除外を登録する。

**Tech Stack:** Python 3.13 / PyQt6 / yt-dlp 2026.8.19 / unittest / ruff / mypy

**Spec:** `docs/superpowers/specs/2026-09-30-drop-private-format-ctx-design.md`

## Global Constraints

- 自動設定・手動設定とも、プレビューと実ダウンロードで選ばれるフォーマットが今と同じであること。
- ダウンロード中のログ表示を変えない(`ExcludeFormats` の後処理開始・完了をログに出さない)。
- `format_engine.py`、`main_window.py`、`FormatListWorker`、`formats.py` は変更しない。
- 実行時の依存を増やさない。`requirements.txt`(ロックファイル)の固定バージョンは変えない。
- コメントと docstring は日本語。既存の説明コメントを失わない。
- Python の実行は `.venv/bin/python`。テストはリポジトリ直下で `.venv/bin/python -m unittest discover -s tests`(pytest は未導入)。個別実行は `.venv/bin/python -m unittest tests.test_yt_dlp_selection.<Class>.<test> -v`。
- 各タスクの最後に全テスト・`.venv/bin/python -m ruff check src tests`・`.venv/bin/python -m mypy` が通ること。
- 作業ツリーには作業前からの未コミット変更(`.gitignore`、`README.md`、`requirements*.txt`、`requirements*.in`、`src/updater.py`、`src/yt_dlp_selection.py` の docstring 2行、`tests/test_updater.py`、`.github/`)がある。コミットには各タスクで触ったファイルだけを `git add <path>` で個別に追加する。`git add -A` / `git add .` は使わない。`src/yt_dlp_selection.py` の作業前の変更は docstring の2行だけで、Task 3 で docstring ごと書き換えるので、そのまま含めてよい。
- git の user 設定がないため、コミットは `git -c user.name=tsubamon55 -c user.email=tsubamon55@gmail.com commit ...` で行い、メッセージ末尾に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` を付ける。

## Review Focus

- 自動設定で、format_spec に一致する候補がすべて非推奨として除外される → 今と同じく yt-dlp のエラー(Requested format is not available)になり、アプリのエラー表示に流れる。→ Task 1 のテストで、除外後に候補がないと `ExtractorError`/`DownloadError` になることを固定する。
- 音声のみの選択で、m4a コンテナなのに中身が Opus のような不一致がある → 除外され、正しい m4a(AAC)が選ばれる。→ Task 1 のテストで固定する。
- プレビューで単体フォーマットが選ばれる場合 → 結果に `requested_formats` が付かない(付くとサイズや解像度の計算を誤る)。→ Task 2 のテストで固定する。
- `select_formats` に渡した `formats` が yt-dlp に書き換えられる → MainWindow が保持する `available_formats` が壊れる。→ Task 2 のテストで、入力が変わらないことを固定する。
- 除外の前処理がログに「後処理開始: ExcludeFormats」と出る → ユーザーに見慣れないログが出る。→ Task 3 のテストで固定する。

---

### Task 1: 除外用の pre_process ポストプロセッサを追加

**Files:**
- Modify: `src/yt_dlp_selection.py`(末尾に追加。既存の関数はこのタスクでは残す)
- Test: `tests/test_yt_dlp_selection.py`

**Interfaces:**
- Produces:
  - `class ExcludeFormatsPP(PostProcessor)`: `__init__(self, exclude: Callable[[dict], bool])`、`run(self, info: dict) -> tuple[list, dict]`
  - `EXCLUDE_FORMATS_PP_KEY: str`(値は `"ExcludeFormats"`)
  - `add_format_exclusion(ydl: yt_dlp.YoutubeDL, exclude: Callable[[dict], bool]) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_yt_dlp_selection.py` の import を次のように変える(`import yt_dlp` を追加し、新しい名前を import する)。

```python
import yt_dlp

from yt_dlp_selection import (
    EXCLUDE_FORMATS_PP_KEY,
    ExcludeFormatsPP,
    add_format_exclusion,
    make_filtering_format_selector,
    select_formats,
)
```

`make_audio` の定義の後に、実際の yt-dlp に渡すための最小 info を作るヘルパーを追加する。

```python
def make_info(formats):
    """process_ie_resultに渡せる最小限の動画情報(extractorは候補が無いときのエラー組み立てに必須)"""
    return {"id": "x", "title": "x", "extractor": "generic", "extractor_key": "Generic", "formats": formats}
```

`if __name__ == "__main__":` の前に次のテストクラスを追加する。

```python
class ExcludeFormatsPPTest(unittest.TestCase):
    def test_removes_matching_formats(self):
        pp = ExcludeFormatsPP(lambda f: f["format_id"] == "137")
        files, info = pp.run({"formats": [
            make_video("137", "mp4", "avc1.640028", height=1080),
            make_audio("140", "m4a", "mp4a.40.2"),
        ]})
        self.assertEqual(files, [])
        self.assertEqual([f["format_id"] for f in info["formats"]], ["140"])

    def test_info_without_formats_is_returned_unchanged(self):
        pp = ExcludeFormatsPP(lambda f: True)
        files, info = pp.run({"id": "x"})
        self.assertEqual(files, [])
        self.assertEqual(info, {"id": "x"})

    def test_pp_key_matches_constant(self):
        self.assertEqual(EXCLUDE_FORMATS_PP_KEY, "ExcludeFormats")


class AddFormatExclusionTest(unittest.TestCase):
    """実際のyt-dlpで、pre_processで除いたフォーマットが選択候補から外れることを確認する(ネットワークなし)"""

    def setUp(self):
        self.formats = [
            make_video("137", "mp4", "avc1.640028", height=1080),
            # YouTubeが高解像度で出す「mp4だが中身はVP9」のフォーマット
            make_video("616", "mp4", "vp09.00.50.08", height=2160),
            make_audio("140", "m4a", "mp4a.40.2"),
        ]

    def _select(self, formats, format_spec, exclude=None, extra_opts=None):
        opts = {"quiet": True, "no_warnings": True, "format": format_spec, **(extra_opts or {})}
        with yt_dlp.YoutubeDL(opts) as ydl:
            if exclude is not None:
                add_format_exclusion(ydl, exclude)
            return ydl.process_ie_result(make_info(formats), download=False)

    def test_without_exclusion_highest_resolution_is_selected(self):
        """対照: 除外しなければ2160pが選ばれる(下のテストが意味を持つことの確認)"""
        self.assertEqual(self._select(self.formats, "bv*+ba/b")["format_id"], "616+140")

    def test_excluded_format_is_not_selected(self):
        selected = self._select(self.formats, "bv*+ba/b", exclude=lambda f: f["format_id"] == "616")
        self.assertEqual(selected["format_id"], "137+140")

    def test_audio_with_mismatched_codec_is_excluded(self):
        formats = [
            make_audio("140", "m4a", "mp4a.40.2", abr=128),
            make_audio("999", "m4a", "opus", abr=160),
        ]
        selected = self._select(formats, "ba[ext=m4a]", exclude=lambda f: f["format_id"] == "999")
        self.assertEqual(selected["format_id"], "140")

    def test_error_when_all_candidates_are_excluded(self):
        with self.assertRaises((yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError)):
            self._select(self.formats, "bv*+ba/b", exclude=lambda f: True)

    def test_hook_reports_pp_key(self):
        seen = []
        self._select(
            self.formats, "bv*+ba/b", exclude=lambda f: False,
            extra_opts={"postprocessor_hooks": [lambda d: seen.append(d["postprocessor"])]},
        )
        self.assertEqual(set(seen), {EXCLUDE_FORMATS_PP_KEY})
```

このテストは `make_video` / `make_audio` に `url` がないと動かない(yt-dlp が `url` のないフォーマットを捨てる)。両ヘルパーの返す dict に、`"protocol": "https",` の次の行として `"url": f"https://example.com/{format_id}",` を追加する。

- [ ] **Step 2: テストが失敗することを確認する**

Run: `.venv/bin/python -m unittest tests.test_yt_dlp_selection -v`
Expected: `ImportError: cannot import name 'EXCLUDE_FORMATS_PP_KEY'` で失敗する。

- [ ] **Step 3: 実装する**

`src/yt_dlp_selection.py` の既存の `import yt_dlp` の次の行に追加する。

```python
from yt_dlp.postprocessor import PostProcessor
```

ファイル末尾に追加する。

```python
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
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `.venv/bin/python -m unittest tests.test_yt_dlp_selection -v`
Expected: 全件 PASS(既存の `SelectFormatsTest` / `MakeFilteringFormatSelectorTest` も通る)。

- [ ] **Step 5: 全体の確認とコミット**

Run: `.venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check src tests && .venv/bin/python -m mypy`
Expected: すべて OK。

```bash
git add src/yt_dlp_selection.py tests/test_yt_dlp_selection.py
git -c user.name=tsubamon55 -c user.email=tsubamon55@gmail.com commit -m "フォーマット選択前に候補を除外するpre_processポストプロセッサを追加

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: select_formats を process_ie_result による選択に置き換える

**Files:**
- Modify: `src/yt_dlp_selection.py`(`select_formats`)
- Test: `tests/test_yt_dlp_selection.py`、`tests/test_format_engine.py`(フィクスチャのみ)

**Interfaces:**
- Consumes: なし
- Produces: `select_formats(formats: list[dict], format_spec: str, format_sort: list | None = None) -> list[dict]`(シグネチャは今と同じ。選択結果1件のリスト、候補がなければ `[]`)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_format_engine.py` の `make_video` / `make_audio` の返す dict に、`"protocol": protocol,` の次の行として `"url": f"https://example.com/{format_id}",` を追加する(実装を置き換えると `url` のないフォーマットは候補から捨てられるため)。

`tests/test_yt_dlp_selection.py` の先頭の import に `import copy` を追加し、`SelectFormatsTest` に次のテストを追加する。

```python
    def test_no_matching_format_returns_empty_list(self):
        self.assertEqual(select_formats(self.formats, "bv*[ext=avi]", None), [])

    def test_single_format_has_no_requested_formats(self):
        """単体フォーマットの選択結果にrequested_formatsが付くと、サイズ・解像度の計算を誤る"""
        selected = select_formats(self.formats, "137", None)
        self.assertNotIn("requested_formats", selected[0])

    def test_does_not_modify_input_formats(self):
        before = copy.deepcopy(self.formats)
        select_formats(self.formats, "137+140", None)
        self.assertEqual(self.formats, before)

    def test_format_sort_is_applied(self):
        """同じ解像度ならformat_sortで指定したコーデック(avc)が優先される"""
        selected = select_formats(self.formats, "bv*", ["res", "codec:avc:m4a"])
        self.assertEqual(selected[0]["format_id"], "137")
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `.venv/bin/python -m unittest tests.test_yt_dlp_selection.SelectFormatsTest -v`
Expected: `test_no_matching_format_returns_empty_list` が失敗する(現在の実装はマッチしない候補で例外を出すか、別の結果を返す)。`test_does_not_modify_input_formats` も、現在の `sort_formats` が入力の dict に値を書き込むため失敗する。どれも失敗しない場合は、失敗しなかったテストがこの計画の意図どおりかを確認してから次へ進む(新しい実装の仕様を固定するテストとして残す)。

- [ ] **Step 3: 実装する**

`src/yt_dlp_selection.py` の import に `import copy` を追加し、`select_formats` を次に置き換える。

```python
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
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `.venv/bin/python -m unittest tests.test_yt_dlp_selection tests.test_format_engine -v`
Expected: 全件 PASS。`test_format_engine` はフィクスチャの `url` 以外を変えずに通ること(通らない場合、選択結果が今と変わっているので、アサーションを変えずに原因を調べる)。

- [ ] **Step 5: 全体の確認とコミット**

Run: `.venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check src tests && .venv/bin/python -m mypy`
Expected: すべて OK。`tests/test_main_window.py` が落ちた場合は、そのフィクスチャにも `url` がないことが原因かを確認し、同じく `url` の追加だけで直す。

```bash
git add src/yt_dlp_selection.py tests/test_yt_dlp_selection.py tests/test_format_engine.py
git -c user.name=tsubamon55 -c user.email=tsubamon55@gmail.com commit -m "プレビューのフォーマット選択をprocess_ie_result経由にする

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(`tests/test_main_window.py` を直した場合はそれも `git add` する。)

---

### Task 3: DownloadWorker を pre_process 除外に切り替え、ctx 依存のコードを削除する

**Files:**
- Modify: `src/workers.py`(import、`_postprocessor_hook`、`_build_format_selector` の削除、`run`、`_probe`、`_build_download_opts`)
- Modify: `src/yt_dlp_selection.py`(`_build_ctx`・`make_filtering_format_selector` の削除、モジュール docstring の書き換え)
- Test: `tests/test_workers.py`、`tests/test_yt_dlp_selection.py`

**Interfaces:**
- Consumes: `add_format_exclusion`、`EXCLUDE_FORMATS_PP_KEY`(Task 1)
- Produces: `DownloadWorker._register_format_exclusion(self, ydl) -> None`、`DownloadWorker._probe(self) -> Format`、`DownloadWorker._build_download_opts(self, expected_ext: str | None, ffmpeg_location: str | None) -> dict`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_workers.py` の import に次を追加する。

```python
from formats import is_codec_container_mismatch
from yt_dlp_selection import EXCLUDE_FORMATS_PP_KEY
```

`BuildFormatSelectorTest` クラスを丸ごと削除し、同じ位置に次を追加する。

```python
class FormatExclusionRegistrationTest(unittest.TestCase):
    """自動設定ではprobe用・ダウンロード用の両方のYoutubeDLに非推奨フォーマットの除外を登録し、
    手動設定では登録しない。formatには常に文字列のformat_specを渡す"""

    def _run(self, exclude_mismatched):
        created = []

        def factory(opts):
            ydl = MagicMock()
            ydl.__enter__.return_value = ydl
            ydl.__exit__.return_value = False
            ydl.extract_info.return_value = {"title": "My Video", "ext": "mp4"}
            created.append((opts, ydl))
            return ydl

        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, format_spec="bv*+ba/b", exclude_mismatched=exclude_mismatched)
            with patch("workers.get_ffmpeg_location", return_value=None), \
                 patch("workers.yt_dlp.YoutubeDL", side_effect=factory), \
                 patch("workers.add_format_exclusion") as add_mock:
                worker.run()
        return created, add_mock

    def test_auto_mode_registers_exclusion_on_probe_and_download(self):
        created, add_mock = self._run(exclude_mismatched=True)
        self.assertEqual(len(created), 2)
        self.assertEqual(
            [c.args for c in add_mock.call_args_list],
            [(ydl, is_codec_container_mismatch) for _, ydl in created],
        )

    def test_manual_mode_does_not_register_exclusion(self):
        _, add_mock = self._run(exclude_mismatched=False)
        add_mock.assert_not_called()

    def test_format_option_is_plain_spec_string(self):
        for exclude_mismatched in (True, False):
            created, _ = self._run(exclude_mismatched=exclude_mismatched)
            self.assertEqual([opts["format"] for opts, _ in created], ["bv*+ba/b", "bv*+ba/b"])
```

`PostprocessorHookTest` クラスに次を追加する。

```python
    def test_ignores_format_exclusion_preprocessor(self):
        """フォーマット除外の前処理はユーザーから見た後処理ではないため、ログに出さない"""
        worker = make_worker()
        logs = []
        worker.log.connect(logs.append)
        worker._postprocessor_hook({"status": "started", "postprocessor": EXCLUDE_FORMATS_PP_KEY, "info_dict": {}})
        worker._postprocessor_hook({"status": "finished", "postprocessor": EXCLUDE_FORMATS_PP_KEY, "info_dict": {}})
        self.assertEqual(logs, [])
        self.assertEqual(worker._active_postprocessors, {})
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `.venv/bin/python -m unittest tests.test_workers -v`
Expected: `FormatExclusionRegistrationTest` は `workers` に `add_format_exclusion` がないため patch の段階で `AttributeError` になる。`test_ignores_format_exclusion_preprocessor` は「後処理開始: ExcludeFormats」がログに出て失敗する。

- [ ] **Step 3: workers.py を実装する**

import を置き換える。

```python
from yt_dlp_selection import EXCLUDE_FORMATS_PP_KEY, add_format_exclusion
```

`_postprocessor_hook` の `name = ...` の次の行に追加する。

```python
        if name == EXCLUDE_FORMATS_PP_KEY:
            # フォーマット選択前の候補除外(add_format_exclusion)はユーザーから見た後処理ではないため表示しない
            return
```

`_build_format_selector` を削除し、同じ位置に次を追加する(docstring は元の説明を引き継ぐ)。

```python
    def _register_format_exclusion(self, ydl: yt_dlp.YoutubeDL) -> None:
        """自動設定ではコンテナ/コーデックが一致しない非推奨フォーマットを、format_specの
        解決より前に候補から完全に除外する。手動設定でユーザーが明示的にIDを
        指定した場合はexclude_mismatched=Falseとなり、そのまま尊重する。"""
        if self.request.exclude_mismatched:
            add_format_exclusion(ydl, is_codec_container_mismatch)
```

`run()` の該当部分を次のように変える。

```python
            self._log_request()
            probe_info = self._probe()
            expected_ext = self._prepare_output_name(probe_info)

            download_opts = self._build_download_opts(expected_ext, ffmpeg_location)
            with yt_dlp.YoutubeDL(download_opts) as ydl:
                self._register_format_exclusion(ydl)
                ydl.download([self.request.url])
```

`_probe` を次のように変える。

```python
    def _probe(self) -> Format:
        """実ダウンロードの前に情報だけを取得し、保存ファイル名・拡張子・進捗の重み付けに使う"""
        probe_opts = _base_ydl_opts(self.request.format_sort)
        probe_opts["format"] = self.request.format_spec
        with yt_dlp.YoutubeDL(probe_opts) as probe_ydl:
            self._register_format_exclusion(probe_ydl)
            return probe_ydl.extract_info(self.request.url, download=False)
```

`_build_download_opts` のシグネチャから `format_selector` を外し、`"format": format_selector,` を `"format": self.request.format_spec,` に変える。

```python
    def _build_download_opts(self, expected_ext: str | None, ffmpeg_location: str | None) -> dict:
```

- [ ] **Step 4: yt_dlp_selection.py の ctx 依存コードを削除する**

`_build_ctx` と `make_filtering_format_selector` を削除する。`tests/test_yt_dlp_selection.py` から `MakeFilteringFormatSelectorTest` クラスと、import の `make_filtering_format_selector` を削除する。

`src/yt_dlp_selection.py` のモジュール docstring を次に置き換える。

```python
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
```

`tests/test_yt_dlp_selection.py` のモジュール docstring を次に置き換える。

```python
"""yt_dlp_selection.py の回帰テスト。

format_engine.pyとworkers.pyが共有する、yt-dlpによるフォーマット選択と
pre_processでの候補除外が期待どおりに働くことを、実際のyt-dlpで(ネットワークなしで)検証する。
yt-dlpの更新で挙動が変わった場合、真っ先にこのテストが落ちることを意図している。
"""
```

`RunErrorHandlingTest`、`CancelDuringPostprocessKeepsFinalFileTest` などの既存テストのモックは、`extract_info` と `download` の流れが変わらないためそのまま使える。変更しない。

- [ ] **Step 5: テストが通ることを確認する**

Run: `.venv/bin/python -m unittest tests.test_workers tests.test_yt_dlp_selection -v`
Expected: 全件 PASS。

Run: `grep -rn "_build_ctx\|make_filtering_format_selector\|_build_format_selector" src tests`
Expected: 出力なし。

- [ ] **Step 6: 全体の確認とコミット**

Run: `.venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check src tests && .venv/bin/python -m mypy`
Expected: すべて OK。

```bash
git add src/workers.py src/yt_dlp_selection.py tests/test_workers.py tests/test_yt_dlp_selection.py
git -c user.name=tsubamon55 -c user.email=tsubamon55@gmail.com commit -m "ダウンロード時の非推奨フォーマット除外をpre_processに移し、非公開ctxへの依存を削除

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: requirements.in の yt-dlp 固定を緩める

**Files:**
- Modify: `requirements.in`(作業前からの未コミット・未追跡ファイル)

- [ ] **Step 1: 固定と注記を書き換える**

`requirements.in` の次の6行

```
#
# yt-dlpはsrc/yt_dlp_selection.pyでYoutubeDLの非公開の内部契約(フォーマット選択ctxの形)に
# 依存しているため、無条件に最新版を取得しないようバージョンを固定している。
# 更新する場合は、バージョンを上げてロックファイルを再生成してから必ず `python -m unittest discover -s tests` を実行し、
# tests/test_yt_dlp_selection.py が通ることを確認した上でコミットすること。
yt-dlp==2026.8.19
```

を次に置き換える。

```
#
# yt-dlpはフォーマット選択の挙動に依存しているため、ロックファイルを再生成して版が上がったら
# `python -m unittest discover -s tests` を実行し、tests/test_yt_dlp_selection.py が通ることを確認すること。
yt-dlp>=2026.8.19
```

- [ ] **Step 2: 確認する**

Run: `grep -n "yt-dlp" requirements.in requirements.txt`
Expected: `requirements.in` は `yt-dlp>=2026.8.19`、`requirements.txt` は `yt-dlp==2026.8.19` のまま。

- [ ] **Step 3: コミットしない**

`requirements.in` は作業前から未追跡で、ユーザーの別作業(ロックファイル化)の一部。このタスクでは編集だけ行い、コミットに含めるかどうかは完了報告でユーザーに確認する。

---

### Task 5: 実際の YouTube でダウンロードを確認する(ユーザー確認の上で実施)

**Files:** なし(確認のみ)

- [ ] **Step 1: ユーザーに確認する**

ネットワークを使い、実際に動画をダウンロードするため、実行前にユーザーの了承を得る。対象 URL もユーザーに確認する。

- [ ] **Step 2: 自動設定・手動設定で1本ずつダウンロードする**

スクラッチパッドに次のスクリプトを置き、`.venv/bin/python` で実行する(QThread の `run()` を同期的に呼ぶ)。

```python
import os, sys, tempfile
sys.path.insert(0, "src")
from workers import DownloadRequest, DownloadWorker
from formats import FORMAT_OPTIONS, FormatKey

url = sys.argv[1]
option = next(o for o in FORMAT_OPTIONS if o.key is FormatKey.VIDEO_BEST_MP4)
for label, request in [
    ("auto", dict(format_spec=option.spec, format_sort=option.sort, exclude_mismatched=True)),
    ("manual", dict(format_spec="18", exclude_mismatched=False)),
]:
    out = tempfile.mkdtemp()
    worker = DownloadWorker(DownloadRequest(url=url, out_dir=out, **request))
    worker.log.connect(lambda m, l=label: print(f"[{l}] {m}"))
    worker.finished_error.connect(lambda m, l=label: print(f"[{l}] ERROR {m}"))
    worker.run()
    print(label, os.listdir(out))
```

Expected: 両方とも「完了しました」が出てファイルができる。自動設定のログの「使用フォーマット」が H.264/AAC であること。「後処理開始: ExcludeFormats」が出ないこと。
