# クリーンコード・リファクタリング実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `src/` の重複を無くし、長すぎる関数を分け、名前と型をはっきりさせる。アプリの動作は一切変えない。

**Architecture:** 動作を変えないリファクタリングを12タスクに分けて行う。依存の少ない共通ヘルパー（formats / paths）から始め、次にモジュール分割（errors / clip_trimmer / folder_opener）、ワーカー、フォーマット定義、MainWindow、ウィジェットの順に進める。最後に型付けとツール（ruff / mypy）で仕上げる。各タスクの最後に既存テスト一式が通ることを確認してからコミットする。

**Tech Stack:** Python 3.13 / PyQt6 / yt-dlp 2026.8.19（固定）/ unittest（pytest でも実行可）

**Spec:** 本計画はチャット上で行ったコードレビューの指摘一覧を実装するもの。仕様にあたる指摘は下の「背景（レビュー指摘の要約）」に転記した。

## 背景（レビュー指摘の要約）

- **I-1** 表示ラベル `"動画 (最高画質 mp4)"` 等が処理の判定キーを兼ねている。`FORMAT_OPTIONS` の値に本物のフォーマット指定と目印用の文字列（`"audio_m4a"`）が混ざっている → Task 7
- **I-2** `DownloadWorker.start_time`（切り抜き開始秒）と `_start_time`（処理開始時刻）が紛らわしい。コンストラクタ引数が8個 → Task 5
- **I-3** 映像/音声の有無の判定が4か所で重複 → Task 1
- **I-4** filesize / コーデック名の先頭部分 / 同梱ファイル探索 / ログ書き込み / 一時ファイル削除 / ydl_opts / ffprobe ストリーム判定 / コンポーネント番号 / 列描画 / 動画情報のリセット / URL判定 / 開閉ボタン、の重複 → Task 1, 2, 4, 5, 8, 10
- **I-5** 長い関数（`start_download`、`DownloadWorker.run`、`_trim_clip_locally`、`on_formats_fetched`） → Task 4, 5, 8
- **I-6** `workers.py` に役割が混在。エクスプローラー操作が画面クラスにある → Task 3, 4, 9
- **型** 型引数のない list/dict、文字列の組の戻り値、`"low"`/`"high"` の文字列、`has_fallback` の冗長、キャンセルを文言の文字列比較で判定、ruff/mypy 未導入 → Task 6, 7, 8, 10, 11, 12
- **Minor** 命名（`worker`、`info1`/`info2`、`d`、`_probe_video_streams`、import の別名）、`_format_eta` の流用、アクセント色の直書き、誤字「ウィジェント」、Ui_MainWindow の docstring → Task 4, 5, 8, 10

## Global Constraints

- **動作を変えない。** 画面に出る文言・ダイアログ・ボタンの状態遷移・yt-dlp/ffmpeg に渡す引数（順序を含む）を変えない。例外として次の2つだけ変えてよい：(a) DownloadWorker のログ「切り抜き範囲: …」の時刻表記（`01:00` → `1:00`）、(b) crash.log の未処理例外エントリの見出し行。
- Python 3.13。実行時の依存を増やさない（`requirements.txt` は変更禁止）。開発用の依存は `requirements-dev.txt` に ruff / mypy だけ追加してよい。
- `yt_dlp_selection.py` の `_build_ctx` は yt-dlp 内部の実装をそのまま写した意図的なコードなので、Task 1 の `has_video` 等に置き換えない。
- コメントと docstring は日本語。コードを移動するときは既存の説明コメントを失わない（関数の docstring へ移すのは可）。
- テストの実行コマンド（リポジトリ直下で）：`python -m unittest discover -s tests`。すべてのタスクの終わりで全件 OK であること。個別に実行するときは `python -m pytest tests/<file>::<Class>::<test> -q`。
- コミットメッセージは既存の履歴に合わせる：日本語の1行要約＋空行＋本文、末尾に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。1タスク＝1コミット。
- 作業場所は worktree のブランチ `refactor/clean-code`（Task 0 で作る）。`master` へ直接コミットしない。

## Review Focus

動作を変えないリファクタリングなので、「今は通っているが移動や分割で壊れやすい箇所」を挙げる。

1. **切り抜きの ffmpeg 引数の順序**（`-ss` の入力側と出力側の分担、`-map -0:N`、`-c:v:0`、`-t`）。1要素でも入れ替わると音ズレや空出力になる → Task 4 で既存の完全一致テストを全件移し、`_seek_options` の単体テストも追加する。
2. **後処理中のキャンセル** では完成済みファイルを残し、`.part` だけ消す → Task 5 の `run()` 分割後もこの経路を通すテストを追加する。
3. **アップデートのダウンロードをキャンセルしたとき** はエラーダイアログを出さない → Task 6 で文字列比較をシグナルに置き換え、テストで確認する。
4. **高解像度確認ダイアログを Esc / × で閉じた場合**（`clickedButton()` が None）はダウンロードしない。旧実装は 1080p 候補が無いとき `"1080p", None` を返し、`format_spec=None` でダウンロードが走る潜在バグがあった → Task 8 でテストを追加して固定する。
5. **config.json の辞書型設定の上書き** は、型注釈を `dict[str, list[str]]` にしても受け付ける（`expected_type is dict` の判定が壊れやすい）→ Task 11 でテストを追加する。

---

### Task 0: 作業ブランチと基準の確認

**Files:** なし

- [ ] **Step 1: worktree を作る**（superpowers:using-git-worktrees に従う）

```bash
git worktree add ../youtube-downloader-refactor -b refactor/clean-code
cd ../youtube-downloader-refactor
```

- [ ] **Step 2: 基準のテスト結果を記録する**

Run: `python -m unittest discover -s tests`
Expected: `Ran 411 tests` / `OK`

---

### Task 1: フォーマット判定ヘルパーを formats.py に集約する

**Files:**
- Modify: `src/formats.py`（`_codec_prefix` を公開、`Format`・`has_video`・`has_audio`・`format_filesize` を追加）
- Modify: `src/main_window.py:285-298`、`src/workers.py:295-315, 319-329, 703`、`src/format_engine.py:104-110`
- Test: `tests/test_formats.py`

**Interfaces:**
- Produces:
  - `Format = dict[str, Any]`（yt-dlp のフォーマット/情報辞書の型の別名）
  - `has_video(fmt: Format) -> bool`、`has_audio(fmt: Format) -> bool`
  - `format_filesize(fmt: Format) -> int | None`
  - `codec_prefix(codec: str | None) -> str`（旧 `_codec_prefix`。`None`/`"none"` なら `""`）

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_formats.py` の末尾に追加し、import に `codec_prefix, format_filesize, has_audio, has_video` を加える）

```python
class FormatPredicatesTest(unittest.TestCase):
    def test_has_video_true_for_real_codec(self):
        self.assertTrue(has_video({"vcodec": "avc1.640028"}))

    def test_has_video_false_for_none_missing_and_null(self):
        for fmt in ({"vcodec": "none"}, {}, {"vcodec": None}):
            self.assertFalse(has_video(fmt))

    def test_has_audio_true_for_real_codec(self):
        self.assertTrue(has_audio({"acodec": "mp4a.40.2"}))

    def test_has_audio_false_for_none_missing_and_null(self):
        for fmt in ({"acodec": "none"}, {}, {"acodec": None}):
            self.assertFalse(has_audio(fmt))


class FormatFilesizeTest(unittest.TestCase):
    def test_prefers_exact_filesize(self):
        self.assertEqual(format_filesize({"filesize": 100, "filesize_approx": 90}), 100)

    def test_falls_back_to_approx(self):
        self.assertEqual(format_filesize({"filesize": None, "filesize_approx": 90}), 90)

    def test_none_when_unknown(self):
        self.assertIsNone(format_filesize({}))
        self.assertIsNone(format_filesize({"filesize": 0, "filesize_approx": 0}))


class CodecPrefixTest(unittest.TestCase):
    def test_takes_lowercased_head_before_dot(self):
        self.assertEqual(codec_prefix("avc1.640028"), "avc1")
        self.assertEqual(codec_prefix("VP09.00.40.08"), "vp09")

    def test_empty_for_none_or_missing(self):
        self.assertEqual(codec_prefix("none"), "")
        self.assertEqual(codec_prefix(None), "")
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_formats.py -q`
Expected: FAIL（`ImportError: cannot import name 'codec_prefix'`）

- [ ] **Step 3: formats.py に実装する**

`from PyQt6.QtCore import Qt` の上に `from typing import Any` を追加する。`FORMAT_OPTIONS` の直前に次を追加する：

```python
# yt-dlpが返すフォーマット/動画情報の辞書。キーは動的なためdictのまま扱う
Format = dict[str, Any]


def has_video(fmt: Format) -> bool:
    """映像ストリームを含むか(vcodecが未設定・"none"なら含まない)"""
    vcodec = fmt.get("vcodec")
    return bool(vcodec and vcodec != "none")


def has_audio(fmt: Format) -> bool:
    """音声ストリームを含むか(acodecが未設定・"none"なら含まない)"""
    acodec = fmt.get("acodec")
    return bool(acodec and acodec != "none")


def format_filesize(fmt: Format) -> int | None:
    """確定サイズ(filesize)、無ければ推定サイズ(filesize_approx)。どちらも不明ならNone"""
    return fmt.get("filesize") or fmt.get("filesize_approx") or None
```

`_codec_prefix` を `codec_prefix` に改名する（定義と、`_codec_label`・`is_codec_container_mismatch` 内の呼び出し3か所）。

`format_columns` の冒頭を次のように変える：

```python
def format_columns(fmt: Format) -> list[str]:
    format_id = fmt.get("format_id", "?")
    ext = fmt.get("ext", "?")
    video = has_video(fmt)
    audio = has_audio(fmt)

    if video and audio:
        kind = "動画+音声"
    elif video:
        kind = "動画のみ"
    elif audio:
        kind = "音声のみ"
    else:
        kind = "不明"

    quality_text = ""
    fps_text = ""
    if video:
        resolution = fmt.get("resolution") or (
            f"{fmt.get('width')}x{fmt.get('height')}" if fmt.get("height") else None
        )
        quality_text = resolution or ""
        fps = fmt.get("fps")
        if fps:
            fps_display = int(fps) if float(fps).is_integer() else fps
            fps_text = f"{fps_display}fps"
    elif audio:
        abr = fmt.get("abr")
        quality_text = f"{abr:.0f}kbps" if abr else ""

    size = format_size(format_filesize(fmt))
    note = fmt.get("format_note") or ""
    if is_codec_container_mismatch(fmt):
        note = f"⚠非推奨 {note}".strip()

    return [f"[{format_id}]", ext, kind, quality_text, fps_text, format_codec(fmt), format_protocol(fmt), size, note]
```

- [ ] **Step 4: 呼び出し側を置き換える**

`src/main_window.py`：import に `has_audio, has_video` を追加し、`on_formats_fetched` のループ（286-298行）を次にする：

```python
        for fmt in sorted_formats:
            if has_video(fmt):
                combo = self.video_format_combo
                video_count += 1
            elif has_audio(fmt):
                combo = self.audio_format_combo
                audio_count += 1
            else:
                continue
```

`src/workers.py`：import を `from formats import codec_prefix, format_filesize, format_size, has_audio, has_video, is_codec_container_mismatch, protocol_rank` にする。`_describe_selected_format` は次のようにする（表示用の `vcodec`/`acodec` 文字列は残す）：

```python
    @staticmethod
    def _describe_selected_format(info: dict) -> str:
        vcodec = info.get("vcodec") or "none"
        acodec = info.get("acodec") or "none"
        video = has_video(info)
        audio = has_audio(info)
        kind = "映像+音声" if video and audio else ("映像" if video else "音声")

        parts = [f"使用フォーマット: [{info.get('format_id')}] {kind} ({info.get('ext')})"]
        if video:
            width, height = info.get("width"), info.get("height")
            resolution = info.get("resolution") or (f"{width}x{height}" if width and height else "不明")
            fps = info.get("fps")
            parts.append(f"解像度:{resolution}" + (f" {fps}fps" if fps else ""))
            parts.append(f"映像コーデック:{vcodec}")
        if audio:
            abr = info.get("abr")
            parts.append(f"音声コーデック:{acodec}" + (f" 約{round(abr)}kbps" if abr else ""))

        size = format_filesize(info)
        if size:
            parts.append(f"サイズ:{format_size(size)}")

        return " / ".join(parts)
```

`_init_component_weights` の `sizes = ...` を `sizes = [format_filesize(c) or 0 for c in components]` にする。`_trim_clip_locally` の `codec_prefix = (vcodec or "").split(".")[0].lower()` と次の行を次にする（ローカル変数名が関数名とぶつかるため改名する）：

```python
            video_encoder = self._CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX.get(codec_prefix(vcodec))
```

`src/format_engine.py`：import に `format_filesize` を追加し、`estimate_selection_size` のループ内を `size = format_filesize(part)` にする。

- [ ] **Step 5: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（420件 = 411 + 追加9件）

- [ ] **Step 6: コミット**

```bash
git add src/formats.py src/main_window.py src/workers.py src/format_engine.py tests/test_formats.py
git commit -m "映像/音声判定・ファイルサイズ・コーデック名の処理をformats.pyに集約" -m "4か所に散っていたvcodec/acodecの判定、filesize/filesize_approxの取り出し、コーデックIDの先頭部分の取り出しを共通関数にまとめる。動作の変更なし。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 同梱ファイル探索・ログ書き込み・静かなファイル削除を paths.py に集約する

**Files:**
- Modify: `src/paths.py`、`src/config.py:48-63`、`src/main.py:15-53`、`src/updater.py:174-179`、`src/workers.py:283-292, 568-579, 722-735`
- Test: `tests/test_paths.py`、`tests/test_config.py:25-46`

**Interfaces:**
- Produces:
  - `append_log_entry(text: str, log_path: str | None = None) -> bool`（crash.log に `[YYYY-mm-dd HH:MM:SS] text` を1件追記する。書けたら True）
  - `remove_file_quietly(path: str, context: str) -> bool`（ファイルがあれば削除する。失敗しても例外は投げず log_debug に記録する。削除したら True）
  - `find_bundled_file(filename: str) -> str | None`（既存。config もこれを使う）

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_paths.py` に追加。import に `append_log_entry, remove_file_quietly` を加える）

```python
class AppendLogEntryTest(unittest.TestCase):
    def test_appends_timestamped_line_and_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "nested", "crash.log")
            self.assertTrue(append_log_entry("hello", log_path))
            with open(log_path, encoding="utf-8") as f:
                content = f.read()
            self.assertRegex(content, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] hello\n$")

    def test_returns_false_when_directory_cannot_be_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocking_file = os.path.join(tmp, "blocker")
            open(blocking_file, "w").close()
            self.assertFalse(append_log_entry("x", os.path.join(blocking_file, "crash.log")))


class RemoveFileQuietlyTest(unittest.TestCase):
    def test_removes_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.tmp")
            open(path, "w").close()
            self.assertTrue(remove_file_quietly(path, "test"))
            self.assertFalse(os.path.exists(path))

    def test_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(remove_file_quietly(os.path.join(tmp, "none.tmp"), "test"))

    def test_failure_is_logged_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.tmp")
            open(path, "w").close()
            with patch("paths.os.remove", side_effect=PermissionError("locked")), \
                 patch("paths.log_debug") as log_mock:
                self.assertFalse(remove_file_quietly(path, "ctx"))
            self.assertIn("ctx", log_mock.call_args[0][0])
```

（`tests/test_paths.py` に `tempfile` / `patch` の import が無ければ追加する。）

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_paths.py -q`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: paths.py を実装する**

`log_debug` を次に置き換え、その直後に `remove_file_quietly` を追加する：

```python
def append_log_entry(text: str, log_path: str | None = None) -> bool:
    """crash.logへタイムスタンプ付きで1件追記する。書き込めた場合True。
    書き込み失敗はアプリの動作に影響させないため例外は投げない"""
    log_path = log_path or get_log_file_path()
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        return False
    return True


def log_debug(message: str) -> None:
    """crash.logと同じファイルに、ユーザーには見せず処理を続行させた例外の情報を記録する。
    (except Exceptionで握りつぶすだけだと、後から不具合の原因を追跡できなくなるため)"""
    append_log_entry(message)


def remove_file_quietly(path: str, context: str) -> bool:
    """pathのファイルがあれば削除する。削除できた場合True。
    後片付け目的の削除が失敗しても本来の処理を止めないよう、例外は投げずに
    context(呼び出し元の名前)付きでlog_debugへ記録するだけにする"""
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError as e:
        log_debug(f"{context}: {path} の削除に失敗 ({e!r})")
        return False
    return True
```

同梱ファイルの探索先を1つの関数にまとめ、`find_bundled_file` と `get_ffmpeg_location` の候補リストをそれで置き換える：

```python
def _bundle_search_dirs() -> list[str]:
    """同梱ファイルの探索先。PyInstallerのonedirビルドは、同梱ファイルをexeと同階層に
    置く配置と`_internal`配下(_MEIPASS)にまとめる配置のどちらにもなり得るため両方を見る
    (spec側の設定だけに依存していると、specを再生成した拍子に読み込めなくなるため)"""
    dirs = [get_base_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    return dirs


def find_bundled_file(filename: str) -> str | None:
    """実行ファイル/プロジェクト直下、またはPyInstallerの一時展開先に同梱された
    ファイルを探す(どこにも無ければNone)"""
    for base in _bundle_search_dirs():
        path = os.path.join(base, filename)
        if os.path.isfile(path):
            return path
    return None
```

`get_ffmpeg_location` の `candidates = ...` 〜 `for base in candidates:` を `for base in _bundle_search_dirs():` にする。

- [ ] **Step 4: 呼び出し側を置き換える**

`src/config.py`：`import sys` を削除し、`from paths import find_bundled_file, get_base_dir, log_debug` にする。`_config_file_path` の本体を次にする：

```python
def _config_file_path() -> str | None:
    """config.jsonの実体パスを返す(どこにも無ければNone)"""
    return find_bundled_file(CONFIG_FILE_NAME)
```

`tests/test_config.py` の `ConfigFilePathTest` 3テストで、`patch.object(config, "get_base_dir", return_value=base_dir)` を `patch.object(paths, "get_base_dir", return_value=base_dir)` にし、ファイル先頭の import に `import paths` を追加する。

`src/main.py`：`from datetime import datetime` と、`handle_exception` 内の `os.makedirs` 〜 `except OSError` のブロックを削除する。import を `from paths import append_log_entry, find_bundled_file, get_log_file_path` にし、ログ書き込み部分を次にする：

```python
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(message)

        # 書き込みに失敗してもアプリは継続させるが、案内の文言は実際の結果に合わせる。
        # 残っていないログの場所を案内すると、調査の際に誤った手がかりを与えてしまう
        logged = append_log_entry(f"未処理の例外\n{message}", log_path)
```

（`os` はまだ `APP_ICON_PATH` で使うので import は残す。）

`src/updater.py`：import に `remove_file_quietly` を加え、`_cleanup_partial_file` の本体を `remove_file_quietly(self.dest_path, "UpdateDownloadWorker")` の1行にする。

`src/workers.py`：import に `remove_file_quietly` を加える。
- `_cleanup_leftover_files` の `try: os.remove(path) ... except OSError ...` を次にする：
  ```python
            if remove_file_quietly(path, "_cleanup_leftover_files"):
                self.log.emit(f"未完了ファイルを削除しました: {name}")
  ```
- `_reattach_thumbnails` の finally 節の中身を、既存のコメントを残したまま `remove_file_quietly(merged_path, "_reattach_thumbnails")` にする。
- `_trim_clip_locally` の except 節の `if os.path.isfile(trimmed_path): try/except` を `remove_file_quietly(trimmed_path, "_trim_clip_locally")` に、finally 節のループ本体を `remove_file_quietly(path, "_trim_clip_locally")` にする。

- [ ] **Step 5: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（425件 = 前タスク + 追加5件）。`tests/test_main.py` の「ログ失敗時の文言」テストも通ること。

- [ ] **Step 6: コミット**

```bash
git add src/paths.py src/config.py src/main.py src/updater.py src/workers.py tests/test_paths.py tests/test_config.py
git commit -m "同梱ファイル探索・ログ追記・後片付け用のファイル削除をpaths.pyに集約" -m "3か所で重複していたget_base_dir/_MEIPASSの探索、main.pyとlog_debugで重複していたcrash.logへの追記、5か所で重複していた「あれば削除し失敗はログに残す」処理を共通化する。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: ネットワークエラー判定を errors.py に切り出す

**Files:**
- Create: `src/errors.py`
- Modify: `src/workers.py:1-77`
- Create: `tests/test_errors.py`（`tests/test_workers.py` の `DescribeErrorTest` と `IsNetworkErrorTest` を移す）

**Interfaces:**
- Produces: `errors.is_network_error(exc: BaseException) -> bool`、`errors.describe_error(exc: Exception, action: str) -> str`（中身は変えずに移動）

- [ ] **Step 1: テストを先に移す**

`tests/test_errors.py` を作る。`tests/test_workers.py` から `class DescribeErrorTest` と `class IsNetworkErrorTest`（1212行〜`RunErrorHandlingTest` の直前まで）を切り取り、そのまま貼る。先頭は次：

```python
"""errors.py のネットワークエラー判定とユーザー向けメッセージ組み立ての単体テスト"""

import http.client
import os
import socket
import ssl
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from yt_dlp.networking.exceptions import TransportError

from errors import describe_error, is_network_error
```

`tests/test_workers.py` の import を `from workers import DownloadWorker, FormatListWorker, StoryboardFragmentWorker` にする。移したクラスでしか使っていなかった import（`http.client`、`ssl`、`TransportError` 等）は、`python -m pyflakes` が無くても目視で `grep -n "http.client\|ssl\.\|TransportError" tests/test_workers.py` が0件なら削除する。

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_errors.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'errors'`）

- [ ] **Step 3: errors.py を作る**

`src/workers.py` の 24〜77行（`_NETWORK_ERROR_TYPES` のコメントから `describe_error` の終わりまで）をコメントごと `src/errors.py` へ移す。ファイルの先頭は次：

```python
"""例外がネットワーク切断に由来するかの判定と、ユーザー向けエラーメッセージの組み立て"""

import http.client
import socket
import ssl
import urllib.error

from yt_dlp.networking.exceptions import TransportError
```

`src/workers.py` からは移した部分と、使わなくなった import（`http.client`、`socket`、`ssl`、`urllib.error`、`TransportError`）を削除し、`from errors import describe_error` を追加する（`urllib.request` は残す）。

- [ ] **Step 4: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（425件。移動だけなので件数は変わらない）

- [ ] **Step 5: コミット**

```bash
git add src/errors.py src/workers.py tests/test_errors.py tests/test_workers.py
git commit -m "ネットワークエラー判定をworkers.pyからerrors.pyへ分離" -m "is_network_error/describe_errorはQThreadと無関係な純粋関数のため独立したモジュールにする。中身の変更なし。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: ffmpeg による切り抜き処理を clip_trimmer.py に切り出して分割する

**Files:**
- Create: `src/clip_trimmer.py`
- Modify: `src/workers.py:397-735`（切り抜き関連の static メソッド群と `_trim_clip_locally` を削除して委譲する）
- Create: `tests/test_clip_trimmer.py`（`tests/test_workers.py` の 551〜1186行のクラスを移して書き換える）

**Interfaces:**
- Consumes: `formats.codec_prefix`、`paths.log_debug`、`paths.remove_file_quietly`（Task 1, 2）
- Produces（`clip_trimmer` モジュールの関数）:
  - `trim_clip(filepath: str | None, clip_start: float | None, clip_end: float | None, log: Callable[[str], None]) -> None`
  - `probe_metadata(ffpp, filepath: str) -> dict`（旧 `_probe_video_streams`）
  - `main_video_stream_index(metadata: dict) -> int | None`（旧 `_main_video_stream_absolute_index`）
  - `detect_vcodec(metadata: dict) -> str | None`
  - `attached_pic_indices(metadata: dict) -> list[int]`（旧 `_attached_pic_absolute_indices`）
  - `extract_attached_pics(ffpp, filepath: str, metadata: dict, absolute_indices: list[int]) -> list[str]`
  - `reattach_thumbnails(ffpp, video_path: str, video_stream_count: int, thumbnail_paths: list[str]) -> None`
  - `nearest_keyframe_at_or_before(ffpp, filepath: str, stream_index: int, target: float) -> float`
  - `_seek_options(ffpp, filepath: str, metadata: dict, clip_start: float | None) -> tuple[list[str], list[str]]`（入力側の -ss と出力側の -ss）
  - `_trim_output_options(metadata: dict, pic_indices: list[int], clip_start: float | None, clip_end: float | None) -> list[str]`（出力側の -ss を除く出力オプション）
  - `DownloadWorker._trim_clip_locally()` は残し、`trim_clip(self._final_filepath, self.start_time, self.end_time, self.log.emit)` を呼ぶだけにする（Task 5 で引数名を改名する）

- [ ] **Step 1: テストを移して書き換える**

`tests/test_clip_trimmer.py` を作り、`tests/test_workers.py` の `DetectVcodecTest`〜`TrimClipLocallyTest`（551行〜`RunRegistersFfmpegLocationTest` の直前）を切り取って貼る。先頭は次：

```python
"""clip_trimmer.py(ダウンロード済みファイルのffmpegによる切り抜き)の単体テスト"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import clip_trimmer
from clip_trimmer import trim_clip
```

貼ったテストに次の置き換えを機械的に行う：

| 置き換え前 | 置き換え後 |
|---|---|
| `DownloadWorker._detect_vcodec(` | `clip_trimmer.detect_vcodec(` |
| `DownloadWorker._probe_video_streams(` | `clip_trimmer.probe_metadata(` |
| `DownloadWorker._main_video_stream_absolute_index(` | `clip_trimmer.main_video_stream_index(` |
| `DownloadWorker._attached_pic_absolute_indices(` | `clip_trimmer.attached_pic_indices(` |
| `worker._extract_attached_pics(` | `clip_trimmer.extract_attached_pics(`（直前の `worker = make_worker()` 行は削除） |
| `DownloadWorker._reattach_thumbnails(` | `clip_trimmer.reattach_thumbnails(` |
| `DownloadWorker._nearest_keyframe_at_or_before(` | `clip_trimmer.nearest_keyframe_at_or_before(` |
| `patch("workers.FFmpegPostProcessor"` | `patch("clip_trimmer.FFmpegPostProcessor"` |
| `patch.object(DownloadWorker, "_nearest_keyframe_at_or_before", return_value=X)` | `patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=X)` |
| docstring 中の `_probe_video_streams` / `_detect_vcodec` 等の旧名 | 新しい関数名 |

`TrimClipLocallyTest`（クラス名は `TrimClipTest` に変える）の各テストでは、ワーカーを作って `_trim_clip_locally()` を呼んでいる部分を `trim_clip` の直接呼び出しに書き換える。例：

```python
# 置き換え前
            worker = make_worker(start_time=12.0, end_time=22.0)
            worker._final_filepath = final_path
            ...
                worker._trim_clip_locally()
# 置き換え後
            ...
                trim_clip(final_path, 12.0, 22.0, lambda msg: None)
```

該当箇所は `grep -n "_trim_clip_locally\|make_worker" tests/test_clip_trimmer.py` で洗い出し、0件になるまで直す。特殊な3テストは次のようにする：
- `test_noop_when_final_filepath_missing`：`trim_clip(None, None, 20.0, lambda msg: None)`
- `test_noop_when_final_file_does_not_exist`：`trim_clip("C:/out/does_not_exist.mp4", None, 20.0, lambda msg: None)`
- `test_ffmpeg_failure_does_not_raise_and_keeps_full_video`：`logs = []` を残し、`worker.log.connect(...)` の行を削除して `trim_clip(final_path, 10.0, 30.0, logs.append)` にする。

続けて `_seek_options` の単体テストを `tests/test_clip_trimmer.py` の末尾に追加する（Review Focus 1）：

```python
class SeekOptionsTest(unittest.TestCase):
    def test_no_start_means_no_seek(self):
        ffpp = MagicMock()
        for start in (None, 0.0):
            self.assertEqual(clip_trimmer._seek_options(ffpp, "C:/v.mp4", {"streams": []}, start), ([], []))
        ffpp.get_metadata_object.assert_not_called()

    def test_audio_only_seeks_on_input_side_without_remainder(self):
        ffpp = MagicMock()
        metadata = {"streams": [{"codec_type": "audio", "codec_name": "aac"}]}
        self.assertEqual(
            clip_trimmer._seek_options(ffpp, "C:/v.m4a", metadata, 12.0),
            (["-ss", "12.0"], []),
        )

    def test_video_splits_into_keyframe_and_remainder(self):
        metadata = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
        with patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=10.0):
            self.assertEqual(
                clip_trimmer._seek_options(MagicMock(), "C:/v.mp4", metadata, 12.0),
                (["-ss", "10.0"], ["-ss", "2.0"]),
            )

    def test_exact_keyframe_has_no_remainder(self):
        metadata = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
        with patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=12.0):
            self.assertEqual(
                clip_trimmer._seek_options(MagicMock(), "C:/v.mp4", metadata, 12.0),
                (["-ss", "12.0"], []),
            )
```

`tests/test_workers.py` には委譲を確かめるテストを追加する（`RunRegistersFfmpegLocationTest` の直前）：

```python
class TrimClipLocallyDelegatesTest(unittest.TestCase):
    def test_passes_final_file_and_clip_range_to_trim_clip(self):
        worker = make_worker(start_time=10.0, end_time=30.0)
        worker._final_filepath = "C:/out/My Video.mp4"
        with patch("workers.trim_clip") as trim_mock:
            worker._trim_clip_locally()
        trim_mock.assert_called_once_with("C:/out/My Video.mp4", 10.0, 30.0, worker.log.emit)
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_clip_trimmer.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'clip_trimmer'`）

- [ ] **Step 3: clip_trimmer.py を作る**

`src/workers.py` の `_trim_clip_locally` の docstring 1〜3段落目をモジュールの docstring に移す。`_probe_video_streams` 〜 `_nearest_keyframe_at_or_before` は、docstring とコメントを保ったまま、`self` / `@staticmethod` を外したモジュール関数として移す（関数名は Interfaces の表どおりに改名し、log_debug の接頭辞も新しい名前にする）。ストリームの判定は次の2つの述語にまとめる：

```python
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


def main_video_stream_index(metadata: dict) -> int | None:
    """(旧_main_video_stream_absolute_indexのdocstringをそのまま)"""
    for i, stream in enumerate(metadata.get("streams", [])):
        if _is_main_video(stream):
            return i
    return None


def detect_vcodec(metadata: dict) -> str | None:
    """(旧_detect_vcodecのdocstringをそのまま)"""
    index = main_video_stream_index(metadata)
    return None if index is None else metadata["streams"][index].get("codec_name")


def attached_pic_indices(metadata: dict) -> list[int]:
    """(旧_attached_pic_absolute_indicesのdocstringをそのまま)"""
    return [i for i, stream in enumerate(metadata.get("streams", [])) if _is_attached_pic(stream)]
```

（`(旧…のdocstringをそのまま)` の部分には、`workers.py` の該当メソッドの docstring 本文をそのまま貼る。）

`_trim_clip_locally` 本体の長いコメント2つ（660-671行、689-698行）は、それぞれ次の2関数の docstring に移す：

```python
def _seek_options(ffpp, filepath: str, metadata: dict, clip_start: float | None) -> tuple[list[str], list[str]]:
    """切り抜き開始位置へのシーク指定を (入力側の-ss, 出力側の正確シーク用-ss) で返す。

    (旧660-671行のコメントをここへ)
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

    (旧689-698行のコメントをここへ)
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
```

`src/workers.py` の変更：
- `THUMBNAIL_EMBEDDABLE_EXTS` は残す。`_CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX`、`_ATTACHED_PIC_EXT_BY_CODEC`、`_probe_video_streams`〜`_nearest_keyframe_at_or_before` を削除する。
- `from clip_trimmer import trim_clip` を追加し、import から使わなくなった `codec_prefix` を外す。`FFmpegPostProcessor` の import は `run()` で使うので残す。
- `_trim_clip_locally` を次にする：

```python
    def _trim_clip_locally(self) -> None:
        """ダウンロード済みの最終ファイルを切り抜き範囲で切り出す(詳細はclip_trimmer参照)"""
        trim_clip(self._final_filepath, self.start_time, self.end_time, self.log.emit)
```

- [ ] **Step 4: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（430件 = 前タスク + SeekOptions 4件 + 委譲 1件）。`python -m pytest tests/test_clip_trimmer.py -q` の件数が、移す前の `test_workers.py` の該当クラスの件数＋4 と一致すること。

- [ ] **Step 5: コミット**

```bash
git add src/clip_trimmer.py src/workers.py tests/test_clip_trimmer.py tests/test_workers.py
git commit -m "ffmpegによる切り抜き処理をclip_trimmer.pyへ分離し、シーク指定と出力オプションの組み立てを分割" -m "115行あった_trim_clip_locallyを、Qtに依存しないtrim_clipと、_seek_options/_trim_output_optionsに分ける。3回繰り返していたffprobeストリームの判定は_is_attached_pic/_is_main_videoにまとめた。ffmpegに渡す引数は変更なし。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: DownloadWorker を DownloadRequest で受け取り、run() と進捗フックを分割する

**Files:**
- Modify: `src/workers.py`（`DownloadWorker` 全体、`FormatListWorker.run`）
- Modify: `src/main_window.py:791-795`
- Test: `tests/test_workers.py`、`tests/test_main_window.py:645-658`

**Interfaces:**
- Consumes: `clip_trimmer.trim_clip`（Task 4）
- Produces:
  - `workers.DownloadRequest`（frozen dataclass）：`url: str`、`out_dir: str`、`format_spec: str`、`postprocessors: list[dict] = []`、`format_sort: list[str] | None = None`、`exclude_mismatched: bool = False`、`clip_start: float | None = None`、`clip_end: float | None = None`、プロパティ `has_clip: bool`
  - `DownloadWorker(request: DownloadRequest)`、属性 `worker.request`
  - `DownloadWorker._started_at: float | None`（旧 `_start_time`）
  - `workers._base_ydl_opts(format_sort: list[str] | None = None, ffmpeg_location: str | None = None) -> dict[str, Any]`

- [ ] **Step 1: テストを新しいインターフェースに合わせる（失敗させる）**

`tests/test_workers.py`：
- import を `from workers import DownloadRequest, DownloadWorker, FormatListWorker, StoryboardFragmentWorker, _base_ydl_opts` にする。
- `make_worker` を次にする：

```python
def make_worker(**kwargs):
    defaults = dict(url="https://example.com/watch?v=x", out_dir="C:/out", format_spec="b")
    defaults.update(kwargs)
    return DownloadWorker(DownloadRequest(**defaults))
```

- ファイル全体で `start_time=` → `clip_start=`、`end_time=` → `clip_end=`（`make_worker(` の引数）、`worker._start_time` → `worker._started_at` に置き換える。
- `TrimClipLocallyDelegatesTest` の `make_worker(start_time=10.0, end_time=30.0)` も `clip_start=10.0, clip_end=30.0` にする。
- 末尾に次を追加する：

```python
class DownloadRequestTest(unittest.TestCase):
    def test_has_clip(self):
        base = dict(url="u", out_dir="o", format_spec="b")
        self.assertFalse(DownloadRequest(**base).has_clip)
        self.assertTrue(DownloadRequest(**base, clip_start=1.0).has_clip)
        self.assertTrue(DownloadRequest(**base, clip_end=2.0).has_clip)


class BaseYdlOptsTest(unittest.TestCase):
    def test_minimal(self):
        self.assertEqual(_base_ydl_opts(), {"quiet": True, "no_warnings": True, "noplaylist": True})

    def test_optional_keys(self):
        opts = _base_ydl_opts(["res"], "C:/ffmpeg")
        self.assertEqual(opts["format_sort"], ["res"])
        self.assertEqual(opts["ffmpeg_location"], "C:/ffmpeg")


class CancelDuringPostprocessKeepsFinalFileTest(unittest.TestCase):
    """Review Focus 2: download()が例外なく戻った後にキャンセル済みだった場合
    (後処理中のキャンセル)、完成済みファイルは残し.partだけを消す"""

    def test_keeps_final_and_removes_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            final_path = os.path.join(tmp, "My Video.mp4")

            def fake_download(urls):
                open(final_path, "w").close()
                open(os.path.join(tmp, "My Video.f137.mp4.part"), "w").close()
                worker._final_filepath = final_path
                worker.cancel()

            def factory(opts):
                ydl = MagicMock()
                ydl.__enter__.return_value = ydl
                ydl.__exit__.return_value = False
                ydl.extract_info.return_value = {"title": "My Video", "ext": "mp4"}
                ydl.download.side_effect = fake_download
                return ydl

            errors = []
            worker.finished_error.connect(errors.append)
            with patch("workers.get_ffmpeg_location", return_value=None), \
                 patch("workers.yt_dlp.YoutubeDL", side_effect=factory):
                worker.run()

            self.assertEqual(errors, ["キャンセルされました"])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])
```

`tests/test_main_window.py` の `test_clip_range_is_passed_to_worker`（656-658行）を次にする：

```python
        request = worker_cls.call_args.args[0]
        self.assertEqual(request.clip_start, 60.0)
        self.assertEqual(request.clip_end, 120.0)
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_workers.py -q`
Expected: FAIL（`ImportError: cannot import name 'DownloadRequest'`）

- [ ] **Step 3: workers.py を実装する**

import に `from dataclasses import dataclass, field`、`from typing import Any`、`from clip_range import clip_range_label, format_clip_time` を追加する（`clip_range_label` は既存）。`FormatListWorker` の前に次を追加する：

```python
def _base_ydl_opts(format_sort: list[str] | None = None, ffmpeg_location: str | None = None) -> dict[str, Any]:
    """このアプリの全てのYoutubeDL呼び出しに共通するオプション(出力の抑止・プレイリスト展開の無効化)"""
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if format_sort:
        opts["format_sort"] = format_sort
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
    return opts


@dataclass(frozen=True)
class DownloadRequest:
    """DownloadWorkerに渡す、ダウンロード1件分の指定"""

    url: str
    out_dir: str
    format_spec: str
    postprocessors: list[dict] = field(default_factory=list)
    format_sort: list[str] | None = None
    # 自動設定ではコンテナ/コーデック不一致の非推奨フォーマットを候補から外す
    # (手動設定でユーザーが明示的に選んだIDはそのまま尊重するためFalse)
    exclude_mismatched: bool = False
    # 切り抜き範囲(秒)。Noneは「先頭から」「末尾まで」
    clip_start: float | None = None
    clip_end: float | None = None

    @property
    def has_clip(self) -> bool:
        return self.clip_start is not None or self.clip_end is not None
```

`FormatListWorker.run` の `ydl_opts` 組み立て（129-137行）を次にする：

```python
            ydl_opts = _base_ydl_opts(ffmpeg_location=get_ffmpeg_location())
            ydl_opts["skip_download"] = True
```

`DownloadWorker.__init__` を次にする（`self.url` 等の属性は削除する）：

```python
    def __init__(self, request: DownloadRequest):
        super().__init__()
        self.request = request
        self._is_cancelled = False
        self._logged_format_ids: set[str] = set()
        self._final_filepath: str | None = None
        self._started_at: float | None = None
        self._active_postprocessors: dict[str, int] = {}
        self._unique_title: str | None = None
        self._preexisting_names: set[str] = set()
        self._component_ids: list[str | None] = []
        self._component_weights: list[float] = [1.0]
        self._completed_weight: float = 0.0
        self._current_component_index: int = 0
```

クラス内の参照を置き換える：`self.out_dir` → `self.request.out_dir`、`self.postprocessors` → `self.request.postprocessors`、`self.format_spec` → `self.request.format_spec`、`self.exclude_mismatched` → `self.request.exclude_mismatched`、`self.start_time` → `self.request.clip_start`、`self.end_time` → `self.request.clip_end`、`self.url` → `self.request.url`、`self._start_time` → `self._started_at`。置き換え後に `grep -n "self\.\(url\|out_dir\|format_spec\|postprocessors\|format_sort\|exclude_mismatched\|start_time\|end_time\|_start_time\)\b" src/workers.py` が0件であること。

`_progress_hook` を分割する（引数名 `d` を `hook_info` にする）：

```python
    def _component_index(self, fmt_id: str | None) -> int:
        """進捗フックが報告しているのが何番目のコンポーネント(映像/音声)かを求める"""
        if fmt_id in self._component_ids:
            return self._component_ids.index(fmt_id)
        return self._current_component_index

    def _progress_hook(self, hook_info: dict) -> None:
        if self._is_cancelled:
            raise yt_dlp.utils.DownloadError("ユーザーによりキャンセルされました")

        status = hook_info.get("status")
        if status == "downloading":
            self._on_component_downloading(hook_info)
        elif status == "finished":
            self._on_component_finished(hook_info)

    def _on_component_downloading(self, hook_info: dict) -> None:
        info = hook_info.get("info_dict") or {}
        fmt_id = info.get("format_id")
        if fmt_id and fmt_id not in self._logged_format_ids:
            self._logged_format_ids.add(fmt_id)
            self.log.emit(self._describe_selected_format(info))

        component_index = self._component_index(fmt_id)
        component_weight = (
            self._component_weights[component_index]
            if component_index < len(self._component_weights)
            else 0.0
        )

        total = hook_info.get("total_bytes") or hook_info.get("total_bytes_estimate")
        downloaded = hook_info.get("downloaded_bytes", 0)
        component_percent = downloaded / total if total else 0.0
        percent = min((self._completed_weight + component_weight * component_percent) * 100, 100.0)

        # コンポーネント切り替え時に残り時間表示が乱高下しないよう、
        # 全体の経過時間と進捗率から残り時間を推定する
        elapsed = time.monotonic() - self._started_at if self._started_at else 0.0
        eta = self._format_eta(elapsed * (100 - percent) / percent) if percent > 0 else "--:--"
        speed = hook_info.get("_speed_str", "").strip()
        self.progress.emit(percent, f"{percent:.1f}% 速度:{speed} 残り:{eta}")

    def _on_component_finished(self, hook_info: dict) -> None:
        filename = hook_info.get("filename")
        if filename:
            self.log.emit(f"コンポーネントのダウンロード完了: {os.path.basename(filename)}")
        info = hook_info.get("info_dict") or {}
        component_index = self._component_index(info.get("format_id"))
        if component_index < len(self._component_weights):
            self._completed_weight += self._component_weights[component_index]
        self._current_component_index = component_index + 1

        if self._current_component_index >= len(self._component_weights):
            self.progress.emit(100.0, "ダウンロード完了、後処理中...")
        else:
            self.progress.emit(min(self._completed_weight * 100, 100.0), "次のコンポーネントを準備中...")
```

`_postprocessor_hook(self, d)` の引数名も `hook_info` にする。

`run()` を分割する：

```python
    def run(self):
        self._started_at = time.monotonic()
        try:
            ffmpeg_location = get_ffmpeg_location()
            if ffmpeg_location:
                # yt-dlpは一部の内部チェック(例: クリップ区間指定時のffmpeg利用可否判定)で
                # ydl_optsのffmpeg_locationを見ずFFmpegPostProcessor()を無引数生成するため、
                # そちらが参照するcontextvarにも明示的に設定しておく
                FFmpegPostProcessor._ffmpeg_location.set(ffmpeg_location)

            self._log_request()
            format_selector = self._build_format_selector()
            probe_info = self._probe(format_selector)
            expected_ext = self._prepare_output_name(probe_info)

            download_opts = self._build_download_opts(format_selector, expected_ext, ffmpeg_location)
            with yt_dlp.YoutubeDL(download_opts) as ydl:
                ydl.download([self.request.url])

            if self._is_cancelled:
                self._finish_cancelled_after_download()
            else:
                self._finish_success()
        except Exception as e:
            # キャンセル・ネットワーク切断・その他の失敗いずれの場合も、保存先に
            # 中途半端な.part等のファイルが残らないよう必ず削除する。ただし本編の
            # ダウンロード/マージ自体は完了しており、後続の後処理だけが失敗した
            # ケースでは、完成済みファイルは残す
            self._cleanup_leftover_files(preserve_final=True)
            message = str(e) if self._is_cancelled else describe_error(e, "ダウンロード")
            self.log.emit(f"エラー: {message}")
            self.finished_error.emit(message)

    def _log_request(self) -> None:
        self.log.emit(f"開始: {self.request.url}")
        if self.request.has_clip:
            start = self.request.clip_start
            end = self.request.clip_end
            start_text = format_clip_time(start) if start is not None else "先頭"
            end_text = format_clip_time(end) if end is not None else "末尾"
            self.log.emit(f"切り抜き範囲: {start_text} 〜 {end_text}")

    def _probe(self, format_selector) -> dict:
        """実ダウンロードの前に情報だけを取得し、保存ファイル名・拡張子・進捗の重み付けに使う"""
        probe_opts = _base_ydl_opts(self.request.format_sort)
        probe_opts["format"] = format_selector
        with yt_dlp.YoutubeDL(probe_opts) as probe_ydl:
            return probe_ydl.extract_info(self.request.url, download=False)

    def _prepare_output_name(self, probe_info: dict) -> str | None:
        """保存ファイル名(拡張子除く)を確定し、失敗時の後片付けの準備をする。最終拡張子の見込みを返す"""
        expected_ext = self._expected_ext(probe_info)
        self._unique_title = self._resolve_unique_title(self._build_title(probe_info), expected_ext)
        self.log.emit(f"保存ファイル名(拡張子除く): {self._unique_title}")
        self._snapshot_preexisting_files()
        self._init_component_weights(probe_info)
        return expected_ext

    def _build_download_opts(self, format_selector, expected_ext: str | None, ffmpeg_location: str | None) -> dict:
        opts = _base_ydl_opts(self.request.format_sort, ffmpeg_location)
        opts.update({
            "outtmpl": os.path.join(self.request.out_dir, f"{self._unique_title}.%(ext)s"),
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "format": format_selector,
            "writethumbnail": True,
            "postprocessors": list(self.request.postprocessors),
        })
        if expected_ext in self.THUMBNAIL_EMBEDDABLE_EXTS:
            opts["postprocessors"].append({"key": "EmbedThumbnail"})
        else:
            opts["writethumbnail"] = False
        return opts

    def _finish_cancelled_after_download(self) -> None:
        # download()が例外を投げずに戻ってきた=本編のダウンロードも後処理も
        # 完了している。後処理中にキャンセルを押した場合がこれにあたるため、
        # 完成済みの最終ファイルは削除せずに残す(未完成の中間ファイルのみ削除)
        self._cleanup_leftover_files(preserve_final=True)
        if self._final_filepath and os.path.isfile(self._final_filepath):
            self.log.emit(f"完成済みのファイルは残しました: {self._final_filepath}")
        self.finished_error.emit("キャンセルされました")

    def _finish_success(self) -> None:
        if self.request.has_clip:
            self._trim_clip_locally()
        elapsed = time.monotonic() - self._started_at
        if self._final_filepath and os.path.isfile(self._final_filepath):
            size = format_size(os.path.getsize(self._final_filepath))
            self.log.emit(f"保存先: {self._final_filepath} ({size})")
        self.log.emit(f"所要時間: {elapsed:.1f}秒")
        self.log.emit("完了しました")
        self.finished_ok.emit()
```

`_trim_clip_locally` の呼び出しを `trim_clip(self._final_filepath, self.request.clip_start, self.request.clip_end, self.log.emit)` にする。

- [ ] **Step 4: main_window.py の呼び出しを置き換える**

import を `from workers import DownloadRequest, DownloadWorker, FormatListWorker, StoryboardFragmentWorker` にし、791-795行を次にする：

```python
        self.worker = DownloadWorker(DownloadRequest(
            url=url,
            out_dir=out_dir,
            format_spec=format_spec,
            postprocessors=postprocessors,
            format_sort=format_sort,
            exclude_mismatched=not self.manual_toggle_btn.isChecked(),
            clip_start=clip_start,
            clip_end=clip_end,
        ))
```

- [ ] **Step 5: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（434件 = 前タスク + 追加4件）

- [ ] **Step 6: コミット**

```bash
git add src/workers.py src/main_window.py tests/test_workers.py tests/test_main_window.py
git commit -m "DownloadWorkerの引数をDownloadRequestにまとめ、run()と進捗フックを分割" -m "切り抜き開始秒(start_time)と処理開始時刻(_start_time)の紛らわしい命名をclip_start/_started_atに改める。4か所で重複していたydl_optsの共通部分は_base_ydl_optsに、進捗フックで2回書いていたコンポーネント番号の求め方は_component_indexにまとめた。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: アップデートのキャンセルを文言比較ではなくシグナルで伝える

**Files:**
- Modify: `src/updater.py:126-172`、`src/main_window.py:586-647`
- Test: `tests/test_updater.py:243-262`、`tests/test_main_window.py:1107-1117`

**Interfaces:**
- Produces: `UpdateDownloadWorker.cancelled = pyqtSignal()`、`MainWindow.on_update_download_cancelled(worker: UpdateDownloadWorker, progress: QProgressDialog) -> None`

- [ ] **Step 1: テストを書き換える（失敗させる）**

`tests/test_updater.py` の `test_cancel_during_download_stops_and_cleans_up` の結果集計部分を次にする：

```python
            results = {"ok": [], "error": [], "cancelled": []}
            worker.finished_ok.connect(lambda p: results["ok"].append(p))
            worker.finished_error.connect(lambda m: results["error"].append(m))
            worker.cancelled.connect(lambda: results["cancelled"].append(True))

            with patch("updater.urllib.request.urlopen", return_value=resp):
                worker.run()

            self.assertEqual(results["cancelled"], [True])
            self.assertEqual(results["error"], [])
            self.assertFalse(os.path.isfile(dest))
```

`tests/test_main_window.py` の `test_cancellation_closes_progress_without_error_message` を次にする（Review Focus 3）：

```python
    def test_cancellation_closes_progress_without_error_message(self):
        worker = MagicMock()
        progress = MagicMock()
        self.window.update_download_worker = worker

        with patch.object(QMessageBox, "critical") as critical_mock:
            self.window.on_update_download_cancelled(worker, progress)

        progress.close.assert_called_once()
        critical_mock.assert_not_called()
        self.assertIsNone(self.window.update_download_worker)

    def test_stale_cancellation_is_ignored(self):
        self.window.update_download_worker = MagicMock()
        progress = MagicMock()
        self.window.on_update_download_cancelled(MagicMock(), progress)
        progress.close.assert_not_called()
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_updater.py tests/test_main_window.py -q -k "cancel"`
Expected: FAIL（`AttributeError: ... 'cancelled'` / `'on_update_download_cancelled'`）

- [ ] **Step 3: 実装する**

`src/updater.py` の `UpdateDownloadWorker`：

```python
    progress = pyqtSignal(float)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)
    # ユーザーによるキャンセル。エラーとは区別し、呼び出し側がエラー表示を出さずに済むようにする
    cancelled = pyqtSignal()
```

`run()` の `except UpdateCancelledError:` 節を次にする：

```python
        except UpdateCancelledError:
            self._cleanup_partial_file()
            self.cancelled.emit()
```

`src/main_window.py` の `_start_update_download` で `finished_error` の接続の後に追加する：

```python
        worker.cancelled.connect(
            lambda w=worker, p=progress: self.on_update_download_cancelled(w, p)
        )
```

`on_update_download_error` の末尾を次にし、その下にメソッドを追加する：

```python
        self.update_download_worker = None
        progress.close()
        QMessageBox.critical(self, "アップデートのダウンロードに失敗しました", message)

    def on_update_download_cancelled(self, worker: UpdateDownloadWorker, progress: QProgressDialog) -> None:
        if worker is not self.update_download_worker:
            return
        self.update_download_worker = None
        progress.close()
```

- [ ] **Step 4: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（435件 = 前タスク + 追加1件）

- [ ] **Step 5: コミット**

```bash
git add src/updater.py src/main_window.py tests/test_updater.py tests/test_main_window.py
git commit -m "アップデートのキャンセルを専用シグナルで通知し、エラー文言の文字列比較をやめる" -m "main_windowが「キャンセルされました」という文言でキャンセルを判定しており、updater側の文言を変えると黙って壊れる結合になっていたため。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 自動設定の形式を FormatOption で表し、表示ラベルと処理の判定を分ける

**Files:**
- Modify: `src/formats.py:5-58`、`src/format_engine.py`、`src/main_window_ui.py:127-133`、`src/main_window.py:761-768`、`src/storyboard.py:12`
- Test: `tests/test_formats.py`、`tests/test_format_engine.py`、`tests/test_main_window_ui.py:19, 38-39`

**Interfaces:**
- Produces（formats.py）:
  - `class FormatKey(Enum)`：`VIDEO_BEST_MP4`、`VIDEO_BEST`、`AUDIO_BEST_M4A`、`AUDIO_BEST`、`AUDIO_MP3`
  - `@dataclass(frozen=True) class FormatOption`：`key: FormatKey`、`label: str`、`tooltip: str`、`spec: str`、`sort: list[str] | None = None`、`extract_audio_codec: str | None = None`、`confirm_high_resolution: bool = False`
  - `FORMAT_OPTIONS: tuple[FormatOption, ...]`（並び順は現在のコンボと同じ）
  - `find_format_option(label: str) -> FormatOption | None`、`format_option(key: FormatKey) -> FormatOption`
  - 削除：`FORMAT_OPTION_TOOLTIPS`、`HIGH_RESOLUTION_CHECK_LABELS`
- Produces（format_engine.py）:
  - `class FormatSelection(NamedTuple)`：`spec: str`、`postprocessors: list[dict]`、`sort: list[str] | None`（タプルとして分解できるので既存の呼び出し側はそのまま動く）
  - `extract_audio_postprocessor(codec: str) -> dict`
  - `resolve_format_spec(...) -> FormatSelection`（ラベルが未知なら `ValueError`）
  - `HighResolutionPlan` は frozen にし、`has_fallback` をプロパティにする
  - 削除：`MP3_POSTPROCESSOR`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_formats.py` に追加する（import に `FORMAT_OPTIONS, FormatKey, find_format_option, format_option` を加える）：

```python
class FormatOptionsTest(unittest.TestCase):
    def test_labels_and_order_unchanged(self):
        self.assertEqual(
            [o.label for o in FORMAT_OPTIONS],
            ["動画 (最高画質 mp4)", "動画 (最高画質)", "音声のみ (最高音質 m4a)", "音声のみ (最高音質)", "音声のみ (mp3)"],
        )

    def test_every_option_has_tooltip(self):
        self.assertTrue(all(o.tooltip for o in FORMAT_OPTIONS))

    def test_lookup_by_label_and_key(self):
        option = find_format_option("動画 (最高画質)")
        self.assertIs(option.key, FormatKey.VIDEO_BEST)
        self.assertIs(format_option(FormatKey.VIDEO_BEST), option)
        self.assertIsNone(find_format_option("存在しない"))

    def test_only_video_options_confirm_high_resolution(self):
        self.assertEqual(
            {o.key for o in FORMAT_OPTIONS if o.confirm_high_resolution},
            {FormatKey.VIDEO_BEST_MP4, FormatKey.VIDEO_BEST},
        )
```

`tests/test_format_engine.py` に追加する（import に `FormatSelection, extract_audio_postprocessor` を加える）：

```python
class ResolveFormatSpecTypeTest(unittest.TestCase):
    def test_returns_named_selection(self):
        selection = resolve_format_spec(False, None, None, False, "音声のみ (mp3)")
        self.assertIsInstance(selection, FormatSelection)
        self.assertEqual(selection.spec, "ba/b")
        self.assertEqual(selection.postprocessors, [extract_audio_postprocessor("mp3")])

    def test_unknown_label_raises_value_error(self):
        with self.assertRaises(ValueError):
            resolve_format_spec(False, None, None, False, "存在しない形式")


class ExtractAudioPostprocessorTest(unittest.TestCase):
    def test_mp3_includes_quality(self):
        pp = extract_audio_postprocessor("mp3")
        self.assertEqual(pp["key"], "FFmpegExtractAudio")
        self.assertEqual(pp["preferredcodec"], "mp3")
        self.assertIn("preferredquality", pp)

    def test_best_has_no_quality(self):
        self.assertEqual(
            extract_audio_postprocessor("best"), {"key": "FFmpegExtractAudio", "preferredcodec": "best"}
        )
```

`tests/test_main_window_ui.py`：38-39行を次にする：

```python
        self.assertEqual(self.window.format_combo.count(), len(FORMAT_OPTIONS))
        self.assertEqual(self.window.format_combo.itemText(0), FORMAT_OPTIONS[0].label)
```

さらに、ツールチップが設定されていることのテストを追加する：

```python
    def test_format_combo_items_have_tooltips(self):
        for i, option in enumerate(FORMAT_OPTIONS):
            self.assertEqual(
                self.window.format_combo.itemData(i, Qt.ItemDataRole.ToolTipRole), option.tooltip
            )
```

（`Qt` の import が無ければ `from PyQt6.QtCore import Qt` を追加する。）

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_formats.py tests/test_format_engine.py tests/test_main_window_ui.py -q`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: formats.py を実装する**

`from dataclasses import dataclass` と `from enum import Enum` を追加し、`FORMAT_OPTIONS`〜`FORMAT_OPTION_TOOLTIPS`（5-28行）と `HIGH_RESOLUTION_CHECK_LABELS`（57-58行）を削除する。`BEST_QUALITY_COMPATIBLE_SORT` / `BEST_AUDIO_COMPATIBLE_SORT` はコメントごと `FORMAT_OPTIONS` より前へ移し、次を置く：

```python
class FormatKey(Enum):
    """自動設定の「形式」の識別子。処理の分岐はラベルではなくこれで行う"""

    VIDEO_BEST_MP4 = "video_best_mp4"
    VIDEO_BEST = "video_best"
    AUDIO_BEST_M4A = "audio_best_m4a"
    AUDIO_BEST = "audio_best"
    AUDIO_MP3 = "audio_mp3"


@dataclass(frozen=True)
class FormatOption:
    """自動設定の「形式」コンボの1項目。labelは画面表示専用で、処理の判定にはkeyを使う"""

    key: FormatKey
    label: str
    # 「互換重視」であることをラベルに詰め込まず、ホバー時のツールチップで補足する
    tooltip: str
    spec: str
    sort: list[str] | None = None
    # 音声トラックだけを取り出す後処理(FFmpegExtractAudio)の変換先。Noneなら後処理なし
    extract_audio_codec: str | None = None
    # 最高画質が1080pを超える場合に、ダウンロード前に確認ダイアログを出すか
    confirm_high_resolution: bool = False


FORMAT_OPTIONS: tuple[FormatOption, ...] = (
    FormatOption(
        key=FormatKey.VIDEO_BEST_MP4,
        label="動画 (最高画質 mp4)",
        tooltip="【推奨】 互換性重視でmp4に限定します。動画によっては本来の最高画質(webm/av1等)より画質が下がる場合があります。",
        # ext=mp4/m4aだけではコーデックまでは保証されない(高解像度ではYouTubeがH.264を提供せず、
        # VP9がmp4コンテナのHLSバリアントとして出てくることがある)ため、
        # H.264(avc1)・AAC(mp4a)であることも明示的に条件にする
        spec=(
            "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]"
            "/bv*[ext=mp4][vcodec^=avc1]+ba*[acodec^=mp4a]"
            "/b[ext=mp4][vcodec^=avc1]"
            "/b"
        ),
        confirm_high_resolution=True,
    ),
    FormatOption(
        key=FormatKey.VIDEO_BEST,
        label="動画 (最高画質)",
        tooltip="コンテナ・コーデックを問わず本来の最高画質を選びます。webm/av1等になる場合があり、再生環境によっては再生できないことがあります。",
        spec="bv*+ba/b",
        sort=BEST_QUALITY_COMPATIBLE_SORT,
        confirm_high_resolution=True,
    ),
    FormatOption(
        key=FormatKey.AUDIO_BEST_M4A,
        label="音声のみ (最高音質 m4a)",
        tooltip="【推奨】 互換性重視でm4aに限定します。再エンコードは行いません。",
        spec="ba[ext=m4a]/ba[acodec^=mp4a]/ba",
        # 音声のみに限定できない場合の"ba"フォールバックで動画結合フォーマットが
        # 選ばれてしまう事態に備え、常に音声トラックのみを取り出す後処理を付ける
        # (対象が既に音声のみ・良コーデックならffmpegは何もせずスキップする)
        extract_audio_codec="best",
    ),
    FormatOption(
        key=FormatKey.AUDIO_BEST,
        label="音声のみ (最高音質)",
        tooltip="コーデックを問わず本来の最高音質を選びます。opus等になる場合があり、再生環境によっては再生できないことがあります。",
        spec="ba/b",
        sort=BEST_AUDIO_COMPATIBLE_SORT,
        # "ba"に一致するフォーマットが無い場合の"/b"フォールバックで動画結合
        # フォーマットが選ばれてしまう事態に備え、音声トラックのみを取り出す
        extract_audio_codec="best",
    ),
    FormatOption(
        key=FormatKey.AUDIO_MP3,
        label="音声のみ (mp3)",
        tooltip="再生互換性は最も高い形式ですが、非可逆で192kbpsに変換されるためm4a版より音質は劣化します。",
        spec="ba/b",
        extract_audio_codec="mp3",
    ),
)

_OPTIONS_BY_LABEL = {option.label: option for option in FORMAT_OPTIONS}
_OPTIONS_BY_KEY = {option.key: option for option in FORMAT_OPTIONS}


def find_format_option(label: str) -> FormatOption | None:
    """コンボに表示しているラベルから形式を引く(未知のラベルならNone)"""
    return _OPTIONS_BY_LABEL.get(label)


def format_option(key: FormatKey) -> FormatOption:
    return _OPTIONS_BY_KEY[key]
```

`format_spec_1080p` の `if format_label == "動画 (最高画質 mp4)":` を次にする：

```python
    option = find_format_option(format_label)
    if option is not None and option.key is FormatKey.VIDEO_BEST_MP4:
```

- [ ] **Step 4: format_engine.py を実装する**

import を次にする：

```python
import copy
from dataclasses import dataclass
from typing import NamedTuple

from config import CONFIG
from formats import (
    FormatKey,
    filter_mismatched_formats,
    find_format_option,
    format_option,
    format_size,
    format_spec_1080p,
    is_codec_container_mismatch,
)
```

`MP3_POSTPROCESSOR` を削除し、次に置き換える：

```python
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
```

`resolve_format_spec` を次にする（docstring の説明は残し、戻り値の行を更新する）：

```python
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
```

`compute_auto_format_note` を次にする：

```python
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
```

`HighResolutionPlan` を次にし、`plan_high_resolution_confirmation` の最後の `return` から `has_fallback=has_fallback,` を削除する（ローカル変数 `has_fallback` は残してよい）：

```python
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
```

`src/storyboard.py` の `@dataclass` を `@dataclass(frozen=True)` にする。

- [ ] **Step 5: UI と MainWindow を置き換える**

`src/main_window_ui.py`：import を `from formats import FORMAT_COLUMN_WIDTHS, FORMAT_OPTIONS` にし、127-132行を次にする：

```python
        self.format_combo = QComboBox()
        for option in FORMAT_OPTIONS:
            self.format_combo.addItem(option.label, userData=option.key)
            self.format_combo.setItemData(
                self.format_combo.count() - 1, option.tooltip, Qt.ItemDataRole.ToolTipRole
            )
```

`src/main_window.py`：import から `HIGH_RESOLUTION_CHECK_LABELS` を外して `find_format_option` を加え、761-763行を次にする：

```python
        if not self.manual_toggle_btn.isChecked():
            format_label = self.format_combo.currentText()
            option = find_format_option(format_label)
            if option is not None and option.confirm_high_resolution:
```

- [ ] **Step 6: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（444件 = 前タスク + 追加9件）。`grep -rn "FORMAT_OPTION_TOOLTIPS\|HIGH_RESOLUTION_CHECK_LABELS\|MP3_POSTPROCESSOR\|audio_m4a\|audio_best\"\|\"audio_mp3" src tests` が0件であること。

- [ ] **Step 7: コミット**

```bash
git add src/formats.py src/format_engine.py src/main_window_ui.py src/main_window.py src/storyboard.py tests/test_formats.py tests/test_format_engine.py tests/test_main_window_ui.py
git commit -m "自動設定の形式をFormatOptionで定義し、表示ラベルによる分岐をやめる" -m "FORMAT_OPTIONSの値にformat_specと目印用の文字列(audio_m4a等)が混在し、ラベル文字列で分岐していたのを、形式ごとの設定(spec/sort/音声抽出/高解像度確認/ツールチップ)を持つFormatOptionにまとめる。resolve_format_specの戻り値はFormatSelection(NamedTuple)にした。表示とyt-dlpに渡す値は変更なし。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: MainWindow の長い処理を分割し、名前を整える

**Files:**
- Modify: `src/main_window.py`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: `DownloadRequest`（Task 5）、`FormatSelection` / `find_format_option`（Task 7）
- Produces:
  - `MainWindow.download_worker`（旧 `worker`）
  - `MainWindow._current_format_selection() -> FormatSelection`（旧 `resolve_format_spec`）
  - `MainWindow.confirm_high_resolution_download(format_label, format_spec, format_sort) -> str | None`（ダウンロードに使う format_spec。キャンセル時は None）
  - `main_window._looks_like_url(text: str) -> bool`
  - 内部メソッド：`_clear_video_info`、`_populate_format_combos(formats) -> tuple[int, int]`、`_build_download_request() -> DownloadRequest | None`、`_check_required_inputs(url, out_dir) -> bool`、`_check_clip_within_duration(clip_start, clip_end) -> None`（範囲外なら ValueError）、`_confirm_format_choice(selection) -> str | None`、`_confirm_mismatched_formats() -> bool`、`_prepare_output_dir(out_dir) -> bool`、`_set_busy(busy: bool) -> None`

- [ ] **Step 1: テストを新しい名前・戻り値に合わせる（失敗させる）**

`tests/test_main_window.py`：
- `self.window.worker` を `self.window.download_worker` にすべて置き換える（14か所。`grep -n "window\.worker\b"` が0件になるまで）。
- `self.window.resolve_format_spec()` を `self.window._current_format_selection()` にする（3か所）。
- `ConfirmHighResolutionDownloadTest` を新しい戻り値に合わせる：

```python
MP4_SPEC = "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b"


class ConfirmHighResolutionDownloadTest(MainWindowTestCase):
    def test_no_confirmation_needed_returns_original_spec(self):
        self.window.available_formats = [make_video(height=1080)]
        self.assertEqual(
            self.window.confirm_high_resolution_download("動画 (最高画質 mp4)", MP4_SPEC, None), MP4_SPEC
        )

    def test_label_without_confirmation_returns_original_spec(self):
        self.window.available_formats = [make_video(format_id="399", height=2160, width=3840)]
        self.assertEqual(
            self.window.confirm_high_resolution_download("音声のみ (mp3)", "ba/b", None), "ba/b"
        )

    def test_confirmation_needed_best_button_clicked(self):
        formats = [
            make_video(format_id="399", height=1440, width=2560, filesize=80_000_000),
            make_video(format_id="137", height=1080, filesize=50_000_000),
        ]
        self.assertEqual(self._run_with_clicked_button_index(formats, 0), MP4_SPEC)

    def test_1080p_button_clicked_returns_fallback_spec(self):
        formats = [
            make_video(format_id="399", height=1440, width=2560, filesize=80_000_000),
            make_video(format_id="137", height=1080, filesize=50_000_000),
        ]
        spec = self._run_with_clicked_button_index(formats, 1)
        self.assertIsNotNone(spec)
        self.assertNotEqual(spec, MP4_SPEC)
```

`_run_with_clicked_button_index` は既存のまま使い、中の `confirm_high_resolution_download("動画 (最高画質 mp4)", "bv*[...]/b", None)` の第2引数を `MP4_SPEC` にする。既存の「キャンセルボタン」テストは `self.assertIsNone(self._run_with_clicked_button_index(formats, 2))` にする（1080p 候補がありボタンが3つの場合）。ダイアログを閉じた場合のテストを追加する（Review Focus 4）：

```python
    def test_dialog_closed_without_button_returns_none(self):
        """Esc/×で閉じるとclickedButton()はNoneを返す。1080p候補が無くボタンが
        Noneのときでも、None同士の比較で1080pを選んだ扱いにならないこと"""
        self.window.available_formats = [
            make_video(format_id="399", height=1440, width=2560, filesize=80_000_000),
        ]
        with patch.object(main_window_module.QMessageBox, "exec", return_value=0), \
             patch.object(main_window_module.QMessageBox, "clickedButton", return_value=None):
            self.assertIsNone(
                self.window.confirm_high_resolution_download("動画 (最高画質 mp4)", MP4_SPEC, None)
            )
```

`_looks_like_url` のテストを追加する：

```python
class LooksLikeUrlTest(unittest.TestCase):
    def test_http_and_https(self):
        self.assertTrue(main_window_module._looks_like_url("https://example.com"))
        self.assertTrue(main_window_module._looks_like_url("  http://example.com  "))

    def test_other_text(self):
        for text in ("", "example.com", "ftp://example.com", "hello"):
            self.assertFalse(main_window_module._looks_like_url(text))
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_main_window.py -q`
Expected: FAIL（`AttributeError: ... 'download_worker'` / `'_looks_like_url'` 等）

- [ ] **Step 3: main_window.py を実装する**

import の `resolve_format_spec as resolve_format_spec_logic` を `resolve_format_spec` にし、`FormatSelection` を加える。`from PyQt6.QtCore import ...` に `QThread` を、`from PyQt6.QtGui import ...` に `QCloseEvent` を加える。モジュールレベル（クラスの前）に追加する：

```python
def _looks_like_url(text: str) -> bool:
    """自動で動画情報を取得しにいく対象か(http/httpsで始まるか)"""
    return text.strip().startswith(("http://", "https://"))
```

クラス定数を追加し、`QTimer.singleShot(1000, ...)` を `QTimer.singleShot(self._UPDATE_CHECK_DELAY_MS, ...)` にする：

```python
    # ウィンドウの初回表示より前にアップデートのダイアログが割り込まないよう、表示後まで遅らせる時間
    _UPDATE_CHECK_DELAY_MS = 1000
```

`self.worker` をファイル全体で `self.download_worker` に改名する（`__init__` の宣言、`on_update_available`、`start_download`、`cancel_download`、`closeEvent`、`_running_workers`）。

動画情報のリセットをまとめる。`on_url_changed` と `fetch_formats` を次にし、`_clear_video_info` を追加する：

```python
    def on_url_changed(self, text: str) -> None:
        self._info_fetch_timer.stop()
        # URLが削除・変更・別のものに貼り替えられた場合、直前の動画に対する選択
        # (フォーマット・mp3変換・クリップ範囲・進捗バー等)が次の動画にそのまま
        # 引き継がれてしまわないよう、都度すべての入力内容をリセットする
        self._clear_video_info()
        self.progress_bar.reset()
        self.status_label.setText(IDLE_STATUS_TEXT)

        if _looks_like_url(text):
            self._info_fetch_timer.start()

    def _clear_video_info(self) -> None:
        """表示中の動画の情報(タイトル・サムネイル・フォーマット・クリップ範囲)を破棄し、
        ダウンロードできない状態に戻す"""
        self.info_ready = False
        self.download_btn.setEnabled(False)
        self.title_label.setText("")
        self.thumbnail_label.clear()
        self._reset_format_state()
        self._reset_video_state()
```

```python
    def fetch_formats(self, auto: bool = False) -> None:
        url = self.url_edit.text().strip()
        if not url:
            return

        self._clear_video_info()
        self.status_label.setText("動画情報を取得中...")
        self.spinner.start()
        # (以降のworker生成・接続・startは既存のまま)
```

`auto_paste_from_clipboard` の条件を `if _looks_like_url(text):` にする。

`on_formats_fetched` のコンボへの流し込みを分ける。`self.video_format_combo.clear()` から `combo.setItemData(row, is_codec_container_mismatch(...))` までのうち、コンボに関わる部分を次のメソッドへ移し、呼び出し側は `video_count, audio_count = self._populate_format_combos(formats)` にする（`available_formats` / `video_duration` / ストーリーボード / スライダーの設定は呼び出し側に残す）：

```python
    def _populate_format_combos(self, formats: list[Format]) -> tuple[int, int]:
        """手動設定の動画/音声コンボにフォーマット一覧を流し込み、(動画件数, 音声件数)を返す"""
        self.video_format_combo.clear()
        self.audio_format_combo.clear()
        self.video_format_combo.addItem("なし", userData=None)
        self.audio_format_combo.addItem("なし", userData=None)

        video_count = 0
        audio_count = 0
        # コンテナ/コーデックが一致しない非推奨フォーマットを一覧の下の方に追いやる(安定ソートなので
        # 元々の解像度順は各グループ内で保たれる)
        for fmt in sorted(formats, key=is_codec_container_mismatch):
            if has_video(fmt):
                combo = self.video_format_combo
                video_count += 1
            elif has_audio(fmt):
                combo = self.audio_format_combo
                audio_count += 1
            else:
                continue

            combo.addItem(describe_format_plain(fmt), userData=fmt)
            row = combo.count() - 1
            combo.setItemData(row, format_columns(fmt), FORMAT_COLUMN_ROLE)
            combo.setItemData(row, is_codec_container_mismatch(fmt), FORMAT_MISMATCH_ROLE)
        return video_count, audio_count
```

（`Format` は `from formats import ... Format` で import する。）

`resolve_format_spec` メソッドを改名する：

```python
    def _current_format_selection(self) -> FormatSelection:
        manual_mode = self.manual_toggle_btn.isChecked()
        return resolve_format_spec(
            manual_mode,
            self.video_format_combo.currentData() if manual_mode else None,
            self.audio_format_combo.currentData() if manual_mode else None,
            self.mp3_checkbox.isChecked(),
            self.format_combo.currentText(),
        )
```

`confirm_high_resolution_download` を次にする：

```python
    def confirm_high_resolution_download(
        self, format_label: str, format_spec: str, format_sort: list[str] | None
    ) -> str | None:
        """自動設定の最高画質が1920x1080を超える場合に確認する。
        戻り値はダウンロードに使うformat_spec(1080pを選んだ場合はその代替)。キャンセル時はNone"""
        option = find_format_option(format_label)
        if option is None or not option.confirm_high_resolution:
            return format_spec
        plan = plan_high_resolution_confirmation(self.available_formats, format_label, format_spec, format_sort)
        if not plan.needs_confirmation:
            return format_spec

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("高解像度の動画です")
        box.setText(plan.message)
        best_btn = box.addButton("最高画質でダウンロード", QMessageBox.ButtonRole.AcceptRole)
        p1080_btn = (
            box.addButton("1080pでダウンロード", QMessageBox.ButtonRole.ActionRole)
            if plan.has_fallback
            else None
        )
        box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(p1080_btn if p1080_btn is not None else best_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is best_btn:
            return format_spec
        if p1080_btn is not None and clicked is p1080_btn:
            return plan.fallback_spec
        return None
```

`start_download` を分割する：

```python
    def start_download(self) -> None:
        request = self._build_download_request()
        if request is None or not self._prepare_output_dir(request.out_dir):
            return

        self._set_busy(True)
        self.download_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("ダウンロード中...")

        self.download_worker = DownloadWorker(request)
        self.download_worker.progress.connect(self.on_progress)
        self.download_worker.log.connect(self.append_log)
        self.download_worker.finished_ok.connect(self.on_finished_ok)
        self.download_worker.finished_error.connect(self.on_finished_error)
        self.download_worker.start()

    def _build_download_request(self) -> DownloadRequest | None:
        """入力内容を検証・確認してダウンロード要求を組み立てる。入力エラーや
        ユーザーのキャンセルの場合は、ダイアログを出した上でNoneを返す"""
        url = self.url_edit.text().strip()
        out_dir = self.out_edit.text().strip()
        if not self._check_required_inputs(url, out_dir):
            return None

        try:
            selection = self._current_format_selection()
            clip_start, clip_end = resolve_clip_range(self.clip_start_edit.text(), self.clip_end_edit.text())
            self._check_clip_within_duration(clip_start, clip_end)
        except ValueError as e:
            QMessageBox.warning(self, "入力エラー", str(e))
            return None

        format_spec = self._confirm_format_choice(selection)
        if format_spec is None:
            return None

        return DownloadRequest(
            url=url,
            out_dir=out_dir,
            format_spec=format_spec,
            postprocessors=selection.postprocessors,
            format_sort=selection.sort,
            exclude_mismatched=not self.manual_toggle_btn.isChecked(),
            clip_start=clip_start,
            clip_end=clip_end,
        )

    def _check_required_inputs(self, url: str, out_dir: str) -> bool:
        if not url:
            QMessageBox.warning(self, "入力エラー", "URLを入力してください")
            return False
        if not out_dir:
            QMessageBox.warning(self, "入力エラー", "保存先フォルダを指定してください")
            return False
        if get_ffmpeg_location() is None:
            QMessageBox.critical(
                self,
                "ffmpegが見つかりません",
                f"ffmpegが見つかりません。アプリの ffmpeg{os.sep}{FFMPEG_EXECUTABLE_NAME} を配置するか、"
                "システムにffmpegをインストールしてPATHを通してください。",
            )
            return False
        return True

    def _check_clip_within_duration(self, clip_start: float | None, clip_end: float | None) -> None:
        """resolve_clip_rangeは開始・終了の前後関係のみを見るため、動画の長さとの整合性は
        ここで確認する(範囲外ならValueError)。長さが不明(ライブ配信等)な場合はチェックできないためスキップする"""
        if not self.video_duration:
            return
        if clip_start is not None and clip_start >= self.video_duration:
            raise ValueError("開始時刻が動画の長さを超えています")
        if clip_end is not None and clip_end > self.video_duration:
            raise ValueError("終了時刻が動画の長さを超えています")

    def _confirm_format_choice(self, selection: FormatSelection) -> str | None:
        """選んだフォーマットに注意が必要な場合にユーザーへ確認し、使うformat_specを返す(中止ならNone)"""
        if self.manual_toggle_btn.isChecked():
            return selection.spec if self._confirm_mismatched_formats() else None
        return self.confirm_high_resolution_download(
            self.format_combo.currentText(), selection.spec, selection.sort
        )

    def _confirm_mismatched_formats(self) -> bool:
        """手動設定で非推奨フォーマットを選んでいる場合に続行してよいか確認する"""
        mismatched_fmts = mismatched_selected_formats(
            self.video_format_combo.currentData(), self.audio_format_combo.currentData()
        )
        if not mismatched_fmts:
            return True
        ids = ", ".join(f"[{fmt.get('format_id')}]" for fmt in mismatched_fmts)
        reply = QMessageBox.question(
            self,
            "非推奨フォーマットの選択",
            f"選択中のフォーマット({ids})はコンテナとコーデックが一致しない非推奨のものです。"
            "再生環境によっては正しく再生できない場合があります。\n\nこのままダウンロードしますか?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _prepare_output_dir(self, out_dir: str) -> bool:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            # (既存の770-779行のコメントをそのまま)
            QMessageBox.warning(
                self, "入力エラー", f"保存先フォルダを作成できませんでした:\n{out_dir}\n\n{e}"
            )
            return False
        self.last_output_dir = out_dir
        self.settings.setValue("last_output_dir", out_dir)
        return True

    def _set_busy(self, busy: bool) -> None:
        """ダウンロード中/待機中で切り替わる入力欄・キャンセルボタン・スピナーをまとめて設定する"""
        self.set_inputs_enabled(not busy)
        self.cancel_btn.setEnabled(busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
```

完了・失敗時の処理を次にする（ステータス文言を設定する順序は既存と同じに保つ）：

```python
    def on_finished_ok(self) -> None:
        # url_edit.clear()がtextChangedを発火させ、on_url_changed内のリセット処理で
        # フォーマット選択・mp3変換・クリップ範囲・進捗バー等の入力内容が一括で初期化される
        self.url_edit.clear()
        self._set_busy(False)
        self.download_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(True)
        self.progress_bar.reset()
        self.status_label.setText("完了")
        self.open_output_folder()

    def on_finished_error(self, message: str) -> None:
        self.status_label.setText("エラーまたはキャンセル")
        self._set_busy(False)
        self.download_btn.setEnabled(self.info_ready)
        self.progress_bar.reset()
        QMessageBox.critical(self, "ダウンロード失敗", message)
```

型注釈を整える：`closeEvent(self, event: QCloseEvent) -> None`、`_running_workers(self) -> list[QThread]`、`self.available_formats: list[Format] = []`、`self.storyboard_format: Format | None = None`。

- [ ] **Step 4: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（448件 = 前タスク + ラベル対象外 1件 + ×で閉じる 1件 + URL判定 2件）

- [ ] **Step 5: コミット**

```bash
git add src/main_window.py tests/test_main_window.py
git commit -m "MainWindowのダウンロード開始処理を分割し、動画情報のリセット・URL判定の重複をまとめる" -m "約100行あったstart_downloadを入力検証・確認ダイアログ・保存先の準備・busy状態の切り替えに分ける。confirm_high_resolution_downloadは使うformat_specを直接返すようにし、1080p候補が無い状態でダイアログを閉じるとformat_spec=Noneで進んでしまう経路を塞いだ。worker属性はdownload_workerに改名。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: フォルダを開く処理を folder_opener.py に切り出す

**Files:**
- Create: `src/folder_opener.py`
- Modify: `src/main_window.py:508-546`
- Test: `tests/test_main_window.py:896-915`、Create: `tests/test_folder_opener.py`

**Interfaces:**
- Produces: `folder_opener.find_open_explorer_window(path: str) -> Any | None`、`folder_opener.open_folder(path: str) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_folder_opener.py`：

```python
"""folder_opener.py(保存先フォルダをOSのファイルマネージャで開く処理)の単体テスト"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import folder_opener


class OpenFolderTest(unittest.TestCase):
    def test_windows_without_existing_window_opens_new(self):
        with patch.object(folder_opener.sys, "platform", "win32"), \
             patch.object(folder_opener, "find_open_explorer_window", return_value=None), \
             patch.object(folder_opener.QDesktopServices, "openUrl") as open_url_mock:
            folder_opener.open_folder("C:/out")
        open_url_mock.assert_called_once()

    def test_windows_reuses_existing_window(self):
        window = MagicMock()
        with patch.object(folder_opener.sys, "platform", "win32"), \
             patch.object(folder_opener, "find_open_explorer_window", return_value=window), \
             patch.object(folder_opener, "_bring_to_front") as front_mock, \
             patch.object(folder_opener.QDesktopServices, "openUrl") as open_url_mock:
            folder_opener.open_folder("C:/out")
        front_mock.assert_called_once_with(window)
        open_url_mock.assert_not_called()

    def test_macos_opens_via_qt(self):
        with patch.object(folder_opener.sys, "platform", "darwin"), \
             patch.object(folder_opener.QDesktopServices, "openUrl") as open_url_mock:
            folder_opener.open_folder("/tmp/out")
        self.assertEqual(open_url_mock.call_args[0][0].toLocalFile(), "/tmp/out")
```

`tests/test_main_window.py` の 896〜915行の2テストを、画面側は委譲だけを確かめる1テストに置き換える：

```python
    def test_open_folder_button_click_opens_last_output_dir(self):
        self.window.last_output_dir = "C:/out"
        self.window.open_folder_btn.setEnabled(True)
        with patch.object(main_window_module.os.path, "isdir", return_value=True), \
             patch.object(main_window_module, "open_folder") as open_folder_mock:
            self.window.open_folder_btn.click()
        open_folder_mock.assert_called_once_with("C:/out")
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_folder_opener.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: folder_opener.py を作る**

```python
"""保存先フォルダをOSのファイルマネージャで開く。Windowsでは、同じフォルダを
既に開いているエクスプローラーのウィンドウがあれば新しく開かずに前面へ出す"""

import os
import sys
from typing import Any

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices

from paths import log_debug


def find_open_explorer_window(path: str) -> Any | None:
    (main_window.py 508-526行の本体をdocstring・コメントごとそのまま)


def _bring_to_front(window: Any) -> None:
    try:
        import win32gui

        window.Visible = True
        win32gui.SetForegroundWindow(window.HWND)
    except Exception as e:
        log_debug(f"open_folder: 既存ウィンドウの前面化に失敗 ({e!r})")


def open_folder(path: str) -> None:
    if sys.platform == "win32":
        window = find_open_explorer_window(path)
        if window is not None:
            _bring_to_front(window)
            return

    # Qtの薄いラッパー経由でOS標準のファイルマネージャ(Finder/Nautilus等)を開く。
    # Windows以外では、既存ウィンドウの再利用のような最適化は行わず素直に開くだけにする
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
```

（`(main_window.py … そのまま)` の行は、`find_open_explorer_window` の本体を貼って置き換える。`self` 引数は外す。）

`src/main_window.py`：`find_open_explorer_window` メソッドを削除し、`open_output_folder` を次にする。import から使わなくなった `QDesktopServices`・`QUrl` を外し、`from folder_opener import open_folder` を加える：

```python
    def open_output_folder(self) -> None:
        if self.last_output_dir and os.path.isdir(self.last_output_dir):
            open_folder(self.last_output_dir)
```

`sys` はまだ `__init__` の `sys.frozen` で使うので残す。

- [ ] **Step 4: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（450件 = 前タスク + 3件 − 置き換えで減る 1件）

- [ ] **Step 5: コミット**

```bash
git add src/folder_opener.py src/main_window.py tests/test_folder_opener.py tests/test_main_window.py
git commit -m "保存先フォルダを開く処理(Windowsのエクスプローラー再利用を含む)をfolder_opener.pyへ分離" -m "画面クラスからwin32com/win32guiを使うOS固有のコードを取り除く。動作の変更なし。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: ウィジェットと UI 定義の重複をまとめ、定数と型を整える

**Files:**
- Create: `src/theme.py`
- Modify: `src/widgets.py`、`src/main_window_ui.py`、`src/main_window.py:140-148, 407-463`
- Test: `tests/test_widgets.py`、`tests/test_main_window_ui.py`

**Interfaces:**
- Produces:
  - `theme.ACCENT_COLOR = "#1a73e8"`
  - `widgets.HandleName = Literal["low", "high"]`（`RangeSlider.active_handle`、`previewRequested`、`set_preview_pixmap` で使う）
  - `widgets._draw_columns(painter: QPainter, x: int, y: int, height: int, texts: Iterable[str]) -> None`
  - `main_window_ui.toggle_button_text(label: str, expanded: bool) -> str`（例：`toggle_button_text("ログ", True) == "ログ ▴"`）
  - `main_window_ui.THUMBNAIL_SIZE = QSize(120, 68)`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_main_window_ui.py` に追加する：

```python
from main_window_ui import THUMBNAIL_SIZE, toggle_button_text


class ToggleButtonTextTest(unittest.TestCase):
    def test_expanded_and_collapsed(self):
        self.assertEqual(toggle_button_text("ログ", True), "ログ ▴")
        self.assertEqual(toggle_button_text("詳細設定", False), "詳細設定 ▾")

    def test_thumbnail_size_is_unchanged(self):
        self.assertEqual((THUMBNAIL_SIZE.width(), THUMBNAIL_SIZE.height()), (120, 68))
```

`tests/test_widgets.py` に追加する：

```python
class DrawColumnsTest(unittest.TestCase):
    def test_draws_each_text_at_accumulated_x(self):
        painter = MagicMock()
        widgets._draw_columns(painter, 4, 0, 20, ["a", "b"])
        rects = [c.args[0] for c in painter.drawText.call_args_list]
        self.assertEqual([r.x() for r in rects], [4, 4 + widgets.FORMAT_COLUMN_WIDTHS[0]])
        self.assertEqual([c.args[2] for c in painter.drawText.call_args_list], ["a", "b"])
```

（`tests/test_widgets.py` に `import widgets` と `MagicMock` の import が無ければ追加する。）

- [ ] **Step 2: 失敗を確認する**

Run: `python -m pytest tests/test_widgets.py tests/test_main_window_ui.py -q`
Expected: FAIL（`ImportError` / `AttributeError`）

- [ ] **Step 3: 実装する**

`src/theme.py`：

```python
"""アプリ全体で共有する配色"""

# ボタン・スピナー・スライダー等の強調色(Googleブルー)
ACCENT_COLOR = "#1a73e8"
```

`src/widgets.py`：
- `from collections.abc import Iterable`、`from typing import Literal`、`from theme import ACCENT_COLOR` を追加し、`HandleName = Literal["low", "high"]` を定義する。
- 列描画を1つの関数にまとめ、`FormatItemDelegate.paint` / `FormatComboBox.paintEvent` / `FormatHeaderWidget.paintEvent` のループ3か所をこれに置き換える（x の開始位置はそれぞれ既存の `option.rect.x() + 4` / `field_rect.x() + 2` / `4`、y と高さも既存のまま渡す）：

```python
def _draw_columns(painter: QPainter, x: int, y: int, height: int, texts: Iterable[str]) -> None:
    """フォーマット一覧の列幅(FORMAT_COLUMN_WIDTHS)に沿って、左から順にテキストを描く"""
    for text, width in zip(texts, FORMAT_COLUMN_WIDTHS):
        painter.drawText(QRect(x, y, width, height), int(Qt.AlignmentFlag.AlignVCenter), text)
        x += width
```

- `QColor("#1a73e8")` の2か所を `QColor(ACCENT_COLOR)` にする。
- `RangeSlider`：`self._active_handle: HandleName | None`、`active_handle -> HandleName | None`、`set_preview_pixmap(self, which: HandleName, pixmap: QPixmap)`。`_drag_to` と `paintEvent` 内の `"low"`/`"high"` リテラルはそのままでよい（Literal 型なので型チェックが効く）。
- 型注釈を付ける：`paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None`、`sizeHint(...) -> QSize`、各 `paintEvent(self, event: QPaintEvent) -> None`、`mousePressEvent/mouseMoveEvent/mouseReleaseEvent(self, event: QMouseEvent) -> None`、`__init__(self, parent: QWidget | None = None, ...)`、`showPopup(self) -> None`、`start/stop/_advance(self) -> None`。必要な import（`QModelIndex`、`QMouseEvent`、`QPaintEvent`、`QStyleOptionViewItem`）を追加する。

`src/main_window_ui.py`：
- 4行目と41行目の「ウィジェント」を「ウィジェット」に直す。
- モジュールの docstring の「(pyuicが生成するUi_MainWindowクラスと同じ役割分担)」を「(pyuicが生成するUi_MainWindowクラスと同じ役割分担。ただしpyuicのように別オブジェクトに組み込むのではなく、QMainWindowを継承してMainWindowの基底クラスとして使う)」にする。
- `from PyQt6.QtCore import QSize, Qt` と `from theme import ACCENT_COLOR` を追加し、次を定義する：

```python
THUMBNAIL_SIZE = QSize(120, 68)


def toggle_button_text(label: str, expanded: bool) -> str:
    """折りたたみ式のトグルボタンの表示(展開中は▴、折りたたみ中は▾)"""
    return f"{label} {'▴' if expanded else '▾'}"
```

- `LINK_BUTTON_STYLE` と `download_btn` のスタイルシートを f 文字列にして `#1a73e8` を `{ACCENT_COLOR}` にする（CSS の `{` `}` は `{{` `}}` にエスケープする）。
- `Ui_MainWindow` にヘルパーを2つ追加し、トグルボタン3つとフォーマットコンボ2つの生成をこれで置き換える：

```python
    @staticmethod
    def _make_toggle_button(text: str, fixed_size: bool = True) -> QPushButton:
        """詳細表示を開閉するリンク風のトグルボタン"""
        button = QPushButton(text)
        button.setCheckable(True)
        button.setFlat(True)
        if fixed_size:
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setStyleSheet(LINK_BUTTON_STYLE)
        return button

    def _make_format_combo(self, min_width: int) -> FormatComboBox:
        combo = FormatComboBox()
        combo.setEnabled(False)
        combo.setItemDelegate(self.format_item_delegate)
        combo.setMinimumWidth(min_width)
        return combo
```

  - `self.manual_toggle_btn = self._make_toggle_button("手動設定 ▾")`
  - `self.detail_toggle_btn = self._make_toggle_button(toggle_button_text("詳細設定", False))`
  - `self.log_toggle_btn = self._make_toggle_button(toggle_button_text("ログ", False), fixed_size=False)`（既存のログボタンは SizePolicy を設定していないため）
  - `self.video_format_combo = self._make_format_combo(format_combo_min_width)`、音声も同様
- サムネイルまわりの `68` と `120, 68` を `THUMBNAIL_SIZE` から取る：`preview_container.setFixedHeight(THUMBNAIL_SIZE.height())`、`self.thumbnail_label.setFixedSize(THUMBNAIL_SIZE)`、`self.title_label.setMaximumHeight(THUMBNAIL_SIZE.height())`。

`src/main_window.py`：import に `toggle_button_text` を加え、`on_log_toggle` と `on_detail_toggled` のテキスト設定を `toggle_button_text("ログ", checked)` / `toggle_button_text("詳細設定", checked)` にする（`on_manual_toggled` は表示名自体が切り替わるので現状のまま）。`on_clip_preview_requested` / `_apply_storyboard_tile` / `_on_storyboard_fragment_fetched` の `which: str` を `which: HandleName` にする（`from widgets import HandleName, ScrubPreviewPopup`）。

- [ ] **Step 4: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（453件 = 前タスク + 追加3件）。`grep -rn "1a73e8" src` が `theme.py` の1件だけであること。

- [ ] **Step 5: 目視で確認する**

Run: `python src/main.py`
確認すること：フォーマット一覧の見出しとコンボの列位置が揃っている／「手動設定」「詳細設定」「ログ」の開閉で表示が ▴▾ で切り替わる／ダウンロードボタンの青色がこれまでと同じ。確認したらウィンドウを閉じる。

- [ ] **Step 6: コミット**

```bash
git add src/theme.py src/widgets.py src/main_window_ui.py src/main_window.py tests/test_widgets.py tests/test_main_window_ui.py
git commit -m "列描画・トグルボタン・フォーマットコンボ生成の重複をまとめ、強調色を共通定数にする" -m "3か所の列描画ループを_draw_columnsに、3つのトグルボタンと2つのフォーマットコンボの生成をヘルパーに寄せる。4か所に直書きしていた#1a73e8はtheme.ACCENT_COLORに、スライダーのハンドル名はLiteral型にした。誤字(ウィジェント)とUi_MainWindowのdocstringも修正。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: 型引数のない list / dict と型注釈の抜けを埋める

**Files:**
- Modify: `src/config.py:33, 66-95`、`src/format_engine.py`、`src/storyboard.py`、`src/workers.py`、`src/formats.py:67`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `formats.Format`（Task 1）
- Produces: `AppConfig.clip_video_encoder_by_codec_prefix: dict[str, list[str]]`（`_validated_value` は `typing.get_origin` で総称型にも対応する）

- [ ] **Step 1: 失敗するテストを書く**（Review Focus 5）

`tests/test_config.py` の `LoadConfigTest` に追加する（既存の `self._load(data)` ヘルパーを使う）：

```python
    def test_dict_setting_is_merged_even_with_generic_annotation(self):
        """clip_video_encoder_by_codec_prefixの型注釈をdict[str, list[str]]にしても、
        辞書の部分上書きが効くこと"""
        cfg = self._load({"clip_video_encoder_by_codec_prefix": {"hevc": ["libx265", "22"]}})
        self.assertEqual(cfg.clip_video_encoder_by_codec_prefix["hevc"], ["libx265", "22"])
        self.assertEqual(cfg.clip_video_encoder_by_codec_prefix["avc1"], ["libx264", "18"])

    def test_non_dict_for_dict_setting_is_rejected(self):
        cfg = self._load({"clip_video_encoder_by_codec_prefix": ["libx264"]})
        self.assertEqual(cfg.clip_video_encoder_by_codec_prefix["avc1"], ["libx264", "18"])
```

- [ ] **Step 2: 型注釈を変え、失敗を確認する**

`src/config.py` の `clip_video_encoder_by_codec_prefix: dict = ...` を `clip_video_encoder_by_codec_prefix: dict[str, list[str]] = ...` にする。

Run: `python -m pytest tests/test_config.py -q`
Expected: FAIL（`isinstance(value, dict[str, list[str]])` の TypeError、または上書きが効かない）

- [ ] **Step 3: _validated_value を総称型に対応させる**

`from typing import get_origin` を追加し、`_validated_value` の冒頭で総称型をその元の型に直してから判定する：

```python
def _validated_value(expected_type, current, value):
    """(既存のdocstringの末尾に1文追加)
    dict[str, list[str]] のような総称型の注釈は、元の型(dict)で判定する。
    """
    expected_type = get_origin(expected_type) or expected_type
    # (以降は既存のまま)
```

- [ ] **Step 4: 残りの型注釈を埋める**

次を置き換える（`from formats import Format` を各ファイルの import に加える）：
- `src/format_engine.py`：`available_formats: list` → `list[Format]`、`video_fmt: dict | None` / `audio_fmt: dict | None` → `Format | None`、`selected: dict | None` → `Format | None`、`select_best_format(...) -> Format | None`、`format_sort: list | None` → `list[str] | None`、`mismatched_selected_formats(*formats: Format | None) -> list[Format]`。
- `src/storyboard.py`：`formats: list[dict]` → `list[Format]`、`-> dict | None` → `-> Format | None`、`storyboard: dict` → `Format`。
- `src/workers.py`：`FormatListWorker.finished_ok` の直前のコメントはそのまま。`_thumbnail_url_candidates(info: Format)`、`_describe_selected_format(info: Format)`、`_init_component_weights(self, probe_info: Format) -> None`、`_expected_ext(self, probe_info: Format)`、`_build_title(self, probe_info: Format)`、`_probe(...) -> Format`、`_prepare_output_name(self, probe_info: Format)`、`cancel(self) -> None`、`run(self) -> None`（3クラスとも）、`_snapshot_preexisting_files(self) -> None`、`_cleanup_leftover_files(self, preserve_final: bool = False) -> None`、`_postprocessor_hook(self, hook_info: dict) -> None`、`_format_eta(seconds: float) -> str`（既存）、`__init__(self, url: str) -> None`。
- `src/formats.py`：`format_size(num_bytes: int | float | None) -> str`、`format_codec(fmt: Format)`、`is_codec_container_mismatch(fmt: Format)`、`filter_mismatched_formats(formats: list[Format]) -> list[Format]`、`format_protocol(fmt: Format)`、`protocol_rank(fmt: Format)`、`describe_format_plain(fmt: Format)`。
- `src/main.py`：`install_exception_hook() -> None`、`main() -> None`。

- [ ] **Step 5: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（455件 = 前タスク + 追加2件）

- [ ] **Step 6: コミット**

```bash
git add src tests/test_config.py
git commit -m "型引数のないlist/dictと型注釈の抜けを埋める" -m "yt-dlpのフォーマット辞書はformats.Formatで表す。config.jsonの検証はdict[str, list[str]]のような総称型の注釈でも元の型で判定するようにした。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: ruff と mypy を導入して、型と書式をツールで守る

**Files:**
- Create: `pyproject.toml`、`requirements-dev.txt`
- Modify: `README.md`（開発手順に1節追加）、ruff / mypy の指摘があった `src/`・`tests/` のファイル

- [ ] **Step 1: 設定ファイルを作る**

`requirements-dev.txt`：

```
# 開発時のみ使うツール(アプリの実行・ビルドには不要)
-r requirements.txt
ruff
mypy
```

`pyproject.toml`：

```toml
[tool.ruff]
line-length = 120
target-version = "py313"
src = ["src", "tests"]

[tool.ruff.lint]
# E/W: pycodestyle, F: pyflakes, I: import順, UP: 新しい構文への書き換え, B: よくあるバグの兆候
select = ["E", "W", "F", "I", "UP", "B"]
# 行の長さは既存コードに合わせてformatterに任せず、lintでは見ない
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
# テストはsys.pathを通してからsrc配下をimportするため、import位置の規則を外す
"tests/*" = ["E402"]

[tool.ruff.lint.isort]
known-first-party = [
    "clip_range", "clip_trimmer", "config", "errors", "folder_opener", "format_engine", "formats",
    "main_window", "main_window_ui", "paths", "storyboard", "theme", "updater", "widgets", "workers",
    "yt_dlp_selection",
]

[tool.mypy]
python_version = "3.13"
mypy_path = "src"
files = ["src"]
ignore_missing_imports = true
warn_unused_ignores = true
check_untyped_defs = true
```

- [ ] **Step 2: ツールを入れて ruff を実行する**

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check src tests --fix
python -m ruff check src tests
```

Expected：`--fix` なしの2回目で `All checks passed!`。自動で直せない指摘が残ったら、次の方針で手で直す：
- `B006`/`B008`（引数の既定値が可変・関数呼び出し）→ `None` を既定にして関数内で作る。
- `B023`（ループ変数を捕捉するラムダ）→ 既存コードの `w=worker` のような既定引数束縛にする。
- `F841`（未使用のローカル変数）→ 削除する。
- それ以外で、直すと動作が変わるもの → その行に `# noqa: <コード>` と理由のコメントを付ける。

`git diff --stat` で差分が import 順・書式・型構文（`Optional[X]` → `X | None` 等）だけであることを確かめる。

- [ ] **Step 3: mypy を実行して指摘を直す**

Run: `python -m mypy`
Expected：最初は PyQt6 まわりを中心に指摘が出る。次の方針で0件にする：
- `Item "None" of "X | None" has no attribute` → 直前で `None` を確認済みなら `assert x is not None`、未確認ならガード節を足す（動作を変えないこと）。
- `QApplication.instance()` の戻り値 → `app = QApplication.instance(); assert app is not None`。
- ラムダの型推論の失敗 → 型注釈付きの小さなメソッドにする。
- yt-dlp の非公開 API（`FFmpegPostProcessor._ffmpeg_location` 等）→ その行に `# type: ignore[attr-defined]  # yt-dlp内部のcontextvar` を付ける。

直した後、`python -m mypy` が `Success: no issues found` になること。

- [ ] **Step 4: README に開発手順を追記する**

README の開発・テストに関する節（`python -m unittest discover -s tests` が書かれている所）の後に次を追加する：

```markdown
### コードチェック

開発用ツールを入れて、コミット前に書式と型を確認します。

    python -m pip install -r requirements-dev.txt
    python -m ruff check src tests
    python -m mypy
```

- [ ] **Step 5: 全テストを通す**

Run: `python -m unittest discover -s tests`
Expected: OK（455件）

- [ ] **Step 6: コミット**

```bash
git add pyproject.toml requirements-dev.txt README.md src tests
git commit -m "ruffとmypyを導入し、指摘された書式・型の問題を修正" -m "開発用の依存はrequirements-dev.txtに分け、実行時の依存(requirements.txt)は変えない。" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: 最終確認

**Files:** なし

- [ ] **Step 1: すべてのチェックを実行する**

```bash
python -m unittest discover -s tests
python -m ruff check src tests
python -m mypy
```

Expected: 3つとも成功。テストは455件 OK（件数がずれた場合は、各タスクの追加分と照らし合わせて抜けや重複がないか確かめる）。

- [ ] **Step 2: 残っている旧名がないか確認する**

```bash
grep -rn "resolve_format_spec_logic\|_probe_video_streams\|FORMAT_OPTION_TOOLTIPS\|HIGH_RESOLUTION_CHECK_LABELS\|MP3_POSTPROCESSOR\|\.start_time\b\|\._start_time\b\|self\.worker\b\|ウィジェント" src tests
```

Expected: 0件。

- [ ] **Step 3: アプリを起動して主要な流れを確かめる**

`python src/main.py` で次を行う：
1. YouTube の URL を貼る → タイトル・サムネイル・「動画N件・音声M件を検出しました」が出る。
2. 自動設定の「音声のみ (m4a)」で短い動画をダウンロード → 完了後にフォルダが開く。
3. 詳細設定で切り抜き範囲（例 0:10〜0:20）を指定してダウンロード → ログに「切り抜き範囲: 0:10 〜 0:20」「切り出し完了」が出る。
4. ダウンロード中にキャンセル → 保存先に `.part` が残らない。

- [ ] **Step 4: ブランチ全体のレビューと統合**

superpowers:requesting-code-review で `master` との差分全体をレビューさせ、指摘に対応した後、superpowers:finishing-a-development-branch で統合方法を決める。
