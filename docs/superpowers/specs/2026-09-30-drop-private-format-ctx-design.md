# yt-dlp 非公開 ctx 契約への依存の解消 設計

## 目的

`src/yt_dlp_selection.py` の `_build_ctx` は、yt-dlp の非公開メソッド `YoutubeDL._select_formats` が内部で組み立てる ctx dict(`formats` / `has_merged_format` / `incomplete_formats`)の形を写している。yt-dlp の更新でこの形が変わると、プレビューの選択結果と実ダウンロードが食い違う恐れがあり、そのために yt-dlp のバージョンを `==` で固定している。

アプリは利用可能なフォーマット一覧を既に取得しているので、ctx を自前で組み立てず、info dict を yt-dlp の通常の処理経路(`process_ie_result`)に渡して選択させる。

## 成功条件

- 自動設定・手動設定とも、プレビュー(画質低下の注記、1080p 確認ダイアログ)と実ダウンロードで選ばれるフォーマットが今と同じ。
- 自動設定でコンテナ/コーデック不一致の非推奨フォーマットを候補から除外する動作を維持する。
- `_build_ctx` と `make_filtering_format_selector` を削除し、ctx の形に依存するコードがなくなる。
- 既存テスト(`tests/test_format_engine.py` など)が変更なしで通る。`ruff` と `mypy` が通る。

## 前提として確認した yt-dlp の挙動(2026.08.19、ネットワークなし)

- `YoutubeDL(opts).process_ie_result(info, download=False)` に `{"id", "title", "extractor", "formats"}` だけの info を渡すと、`format` / `format_sort` に従って選択される。`extractor` がないと、一致する候補がないときに `KeyError` になるため必須。
- 処理済みの info に `sanitize_info`(README 記載の公開 API)をかけ、`formats` を絞ってから再度 `process_ie_result` に渡すと、正しく選び直される。`--load-info-json`(`download_with_info_file`)と同じ経路である。
- 一致する候補がないと `ExtractorError`(ダウンロード時は `DownloadError`)が送出される。
- `check_formats` は既定で無効のため、`download=False` の選択でネットワークアクセスは発生しない。

`process_ie_result` も README には載っていないが、yt-dlp 自身の `--load-info-json` が使う経路であり、ctx の形よりはるかに変わりにくい。

## 採用しなかった案

- **除外条件を format_spec の文字列で表す**: 除外条件は「ext=mp4 かつ codec=vp9」のような組み合わせで、yt-dlp のフィルタは AND しか書けない。全候補に展開すると spec が大きく膨らむため不採用。
- **関数 selector を使い続ける**: ctx の形に依存するため目的と矛盾する。

## 設計

### `src/yt_dlp_selection.py`

yt-dlp との唯一の接点という役割は変えない。`_build_ctx` と `make_filtering_format_selector` を削除する。

- `select_formats(formats, format_spec, format_sort=None) -> list[dict]`
  - シグネチャと戻り値の形は変えない(選択結果1件のリスト。複合フォーマットは `requested_formats` 付き)。
  - 内部で `{"id", "title", "extractor", "formats"}` の最小 info を組み立て、`{"quiet", "no_warnings", "format", "format_sort"}` を指定した `YoutubeDL` の `process_ie_result(download=False)` で選択する。
  - 一致する候補がない場合や yt-dlp が例外を送出した場合は `[]` を返す。
- `select_from_info(ydl, info, exclude=None) -> tuple[dict, dict]`(新設)
  - `ydl.sanitize_info(info)` で作ったコピーから、`exclude` に一致するフォーマットを `formats` から取り除く(`exclude=None` なら何も除外しない)。
  - そのコピーの複製を `ydl.process_ie_result(..., download=False)` に渡し、選択済み info を得る。
  - 戻り値は `(選択済み info, 除外済み info)`。除外済み info はダウンロード時にそのまま再利用する。
  - 例外は捕まえずに送出する。入力の `info` は変更しない。
- モジュール docstring を新しい方式の説明に書き換え、「非公開契約」「バージョン固定」の運用ルールは削除する。

### `src/workers.py`(`DownloadWorker`)

- `_probe` を次の流れに置き換える。
  1. `extract_info(url, download=False)` で処理済み info を1回だけ取得する。
  2. `select_from_info` で除外と選択を行う。`exclude` は自動設定なら `is_codec_container_mismatch`、手動設定なら `None`。
  3. 選択済み info を今の probe_info と同じ用途(ファイル名・拡張子・進捗の重み付け)に使う。
- ダウンロードは `ydl.download([url])` の代わりに `YoutubeDL(download_opts).process_ie_result(除外済み info, download=True)` を呼ぶ。`download_opts` の中身(outtmpl、フック、`format`、後処理、切り抜き範囲など)は変えない。`format` には今と同じく文字列の format_spec を渡す。
- `_build_format_selector` は削除する。
- これにより、ページ取得が2回(probe とダウンロード)から1回になる。

### 変更しないもの

`format_engine.py`、`main_window.py`、`FormatListWorker`、`formats.py`。

### `requirements.in`

- `yt-dlp==2026.8.19` を `yt-dlp>=2026.8.19` に変え、非公開契約を理由とした固定の注記を削除する。
- `requirements.txt` はロックファイルなので、固定バージョンは変えない。
- `requirements.in` には作業前からの未コミット変更がある。その上に重ねて編集し、コミットに含めるかどうかはユーザーに確認する。

## エラー処理

- `select_formats`: 失敗時は `[]`。呼び出し元の `select_best_format` はもともと例外を捕まえて `None` にしているので、挙動は変わらない。
- `select_from_info` とダウンロード: 失敗時は今と同じく `DownloadError` などが `DownloadWorker.run()` の except に届き、`describe_error` で日本語メッセージに変換される。キャンセル時も進捗フックから `DownloadError` が出て同じ経路を通る。未完成ファイルの削除、キャンセルとネットワークエラーの優先順位の判定は変更しない。

## テスト

- `tests/test_yt_dlp_selection.py`
  - `SelectFormatsTest` は変更せずに通す(今と同じ選択結果になることの確認)。
  - 一致する候補がないときに `[]` を返すテストを追加する。
  - `MakeFilteringFormatSelectorTest` を削除し、`SelectFromInfoTest` を追加する。確認する内容:
    - 除外したフォーマットが選ばれない(2160p の「mp4 だが中身は VP9」を除外すると 1080p の H.264 が選ばれる)。
    - 入力の info が変わらない。
    - 除外済み info に、除外したフォーマットが含まれない。
    - `exclude=None` なら何も除外しない。
- `tests/test_workers.py`
  - `BuildFormatSelectorTest` を廃止し、自動設定では `is_codec_container_mismatch`、手動設定では `None` が `exclude` に渡ることのテストに置き換える。
  - `RunErrorHandlingTest` などのモックを `ydl.download` から `process_ie_result` へ差し替える。
  - `extract_info` が1回しか呼ばれないこと、ダウンロードに除外済み info が渡されることのテストを追加する。
- `tests/test_format_engine.py` は変更せずに全件通す。
- `ruff` と `mypy` を通す。
- 実際に YouTube で自動設定と手動設定を1本ずつダウンロードして確認する(ネットワークを使うため、実行前にユーザーに確認する)。
