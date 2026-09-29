# yt-dlp 非公開 ctx 契約への依存の解消 設計

## 目的

`src/yt_dlp_selection.py` の `_build_ctx` は、yt-dlp の非公開メソッド `YoutubeDL._select_formats` が内部で組み立てる ctx dict(`formats` / `has_merged_format` / `incomplete_formats`)の形を写している。yt-dlp の更新でこの形が変わると、プレビューの選択結果と実ダウンロードが食い違う恐れがあり、そのために yt-dlp のバージョンを `==` で固定している。

アプリは利用可能なフォーマット一覧を既に取得しているので、ctx を自前で組み立てず、yt-dlp の通常の処理経路と公式の拡張点だけを使って選択と除外を行う。

## 成功条件

- 自動設定・手動設定とも、プレビュー(画質低下の注記、1080p 確認ダイアログ)と実ダウンロードで選ばれるフォーマットが今と同じ。
- 自動設定でコンテナ/コーデック不一致の非推奨フォーマットを候補から除外する動作を維持する。
- `_build_ctx` と `make_filtering_format_selector` を削除し、ctx の形に依存するコードがなくなる。
- ダウンロード中のログ表示が今と変わらない(追加する前処理の開始・完了がログに出ない)。
- 既存テスト(`tests/test_format_engine.py` など)が変更なしで通る。`ruff` と `mypy` が通る。

## 前提として確認した yt-dlp の挙動(2026.08.19、ネットワークなし)

- `YoutubeDL(opts).process_ie_result(info, download=False)` に `{"id", "title", "extractor", "extractor_key", "formats"}` だけの info を渡すと、`format` / `format_sort` に従って選択される。`extractor` がないと、一致する候補がないときに `KeyError` になるため必須。
- 一致する候補がないと `ExtractorError`(ダウンロード時は `DownloadError`)が送出される。
- `check_formats` は既定で無効のため、`download=False` の選択でネットワークアクセスは発生しない。
- `YoutubeDL.add_post_processor(pp, when="pre_process")` で登録したポストプロセッサは、`process_video_result` の中でフォーマット選択より前に実行される。その直後に yt-dlp は `# The pre-processors may have modified the formats` として `formats` を読み直すため、ここで `info["formats"]` を絞り込むと選択候補から外れる。`YoutubeDL` の docstring に記載された `when` の仕組みで、CLI の `--use-postprocessor NAME:when=pre_process` と同じもの。
- ポストプロセッサには `postprocessor_hooks` が自動で付き、実行の前後に `status: started / finished` が通知される。`postprocessor` キーの値は `pp_key()`(クラス名から末尾の `PP` を除いたもの)。

## 採用しなかった案

- **処理済み info を再処理する**(当初案): `sanitize_info` は JSON にできない値を文字列にするため、`fragments` に関数を持つフォーマット(ライブ配信のアーカイブなど)がダウンロードできなくなる。`sanitize_info` なしで再処理すると、1回目の選択でトップレベルに書き込まれた `requested_formats` や `height` などが残る(単体フォーマット `18` を選び直しても `requested_formats` に `137+140` が残ることを確認した)。残る値を確実に消す汎用的な方法がないため不採用。
- **除外条件を format_spec の文字列で表す**: 除外条件は「ext=mp4 かつ codec=vp9」のような組み合わせで、yt-dlp のフィルタは AND しか書けない。全候補に展開すると spec が大きく膨らむため不採用。
- **関数 selector を使い続ける**: ctx の形に依存するため目的と矛盾する。

## 設計

### `src/yt_dlp_selection.py`

yt-dlp との唯一の接点という役割は変えない。`_build_ctx` と `make_filtering_format_selector` を削除する。

- `select_formats(formats, format_spec, format_sort=None) -> list[dict]`
  - シグネチャと戻り値の形は変えない(選択結果1件のリスト。複合フォーマットは `requested_formats` 付き)。
  - 内部で最小 info を組み立て、`{"quiet", "no_warnings", "format", "format_sort"}` を指定した `YoutubeDL` の `process_ie_result(download=False)` で選択する。入力の `formats` は変更しない(複製して渡す)。
  - 一致する候補がない場合や yt-dlp が例外を送出した場合は `[]` を返す。
- `ExcludeFormatsPP(PostProcessor)`(新設)
  - コンストラクタで除外条件 `exclude: Callable[[dict], bool]` を受け取る。
  - `run(info)` で `info["formats"]` から `exclude` に一致するものを取り除き、`([], info)` を返す。`formats` がない info はそのまま返す。
- `EXCLUDE_FORMATS_PP_KEY = ExcludeFormatsPP.pp_key()`(値は `"ExcludeFormats"`)
- `add_format_exclusion(ydl, exclude) -> None`(新設)
  - `ydl.add_post_processor(ExcludeFormatsPP(exclude), when="pre_process")` を呼ぶ。
- モジュール docstring を新しい方式の説明に書き換え、「非公開契約」「バージョン固定」の運用ルールは削除する。

### `src/workers.py`(`DownloadWorker`)

- `ydl_opts["format"]` には常に文字列の `request.format_spec` を渡す。`_build_format_selector` は削除する。
- `request.exclude_mismatched` が True(自動設定)のときは、probe 用とダウンロード用の両方の `YoutubeDL` に `add_format_exclusion(ydl, is_codec_container_mismatch)` を登録する。手動設定では登録しない。
- `extract_info` と `ydl.download([url])` の流れは今のまま(ページ取得は今と同じ2回)。
- `_postprocessor_hook` は、`postprocessor` が `EXCLUDE_FORMATS_PP_KEY` の通知を無視する。これにより「後処理開始: ExcludeFormats」のようなログは出ない。

### 変更しないもの

`format_engine.py`、`main_window.py`、`FormatListWorker`、`formats.py`。プレビューは今も `select_best_format` で `filter_mismatched_formats` をかけてから `select_formats` を呼んでいるため、除外の方式は変えない。

### `requirements.in`

- `yt-dlp==2026.8.19` を `yt-dlp>=2026.8.19` に変え、非公開契約を理由とした固定の注記を削除する。
- `requirements.txt` はロックファイルなので、固定バージョンは変えない。
- `requirements.in` には作業前からの未コミット変更がある。その上に重ねて編集し、コミットに含めるかどうかはユーザーに確認する。

## エラー処理

- `select_formats`: 失敗時は `[]`。呼び出し元の `select_best_format` はもともと例外を捕まえて `None` にしているので、挙動は変わらない。
- ダウンロード: 除外によって候補がなくなった場合、今と同じく yt-dlp が `DownloadError`(Requested format is not available)を送出し、`DownloadWorker.run()` の except で `describe_error` により処理される。キャンセル、未完成ファイルの削除、キャンセルとネットワークエラーの優先順位の判定は変更しない。

## テスト

yt-dlp は `url` のないフォーマットを候補から捨てる(実データのフォーマットには必ず `url` がある)。そのため `tests/test_yt_dlp_selection.py` と `tests/test_format_engine.py` のフィクスチャ生成ヘルパー(`make_video` / `make_audio`)には `url` を追加する。テストケースとアサーションは変えない。

- `tests/test_yt_dlp_selection.py`
  - `SelectFormatsTest` は変更せずに通す(今と同じ選択結果になることの確認)。
  - 一致する候補がないときに `[]` を返すテスト、入力の `formats` を変更しないテストを追加する。
  - `MakeFilteringFormatSelectorTest` を削除し、次を追加する。
    - `ExcludeFormatsPP` 単体: 一致するフォーマットを取り除く、`formats` がない info をそのまま返す。
    - `add_format_exclusion` を登録した `YoutubeDL` で `process_ie_result(download=False)` を行うと、2160p の「mp4 だが中身は VP9」が除外され 1080p の H.264 が選ばれる(実際の yt-dlp を使い、ネットワークなし)。
    - `EXCLUDE_FORMATS_PP_KEY` が、フックに通知される `postprocessor` の値と一致する。
- `tests/test_workers.py`
  - `BuildFormatSelectorTest` を廃止し、自動設定では probe 用・ダウンロード用の両方の `YoutubeDL` に除外が登録され、手動設定では登録されないこと、`format` に文字列の format_spec が渡ることのテストに置き換える。
  - `_postprocessor_hook` が `EXCLUDE_FORMATS_PP_KEY` の通知でログを出さないテストを追加する。
- `tests/test_format_engine.py` は変更せずに全件通す。
- `ruff` と `mypy` を通す。
- 実際に YouTube で自動設定と手動設定を1本ずつダウンロードして確認する(ネットワークを使うため、実行前にユーザーに確認する)。
