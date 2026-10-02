# コード全体レビュー結果(2026-09-30)

観点: バグ・セキュリティ・保守性。テストコードは対象外(挙動確認の参照のみ)。
6ブロックに分割してサブエージェントで並列レビューし、結果を集約・重複統合した。「再現済み」はエージェントが実際にコードを実行して確認したもの、「推測」はコード読解からの推定。

| # | ブロック | 対象 |
|---|---|---|
| 1 | フォーマット選択コア | `src/formats.py`, `format_engine.py`, `yt_dlp_selection.py`, `storyboard.py` |
| 2 | ワーカー/エラー | `src/workers.py`, `errors.py` |
| 3 | クリップ切り出し | `src/clip_range.py`, `clip_trimmer.py` |
| 4 | GUI | `src/main_window.py`, `main_window_ui.py`, `widgets.py`, `theme.py` |
| 5 | 基盤/設定/更新 | `src/main.py`, `config.py`, `paths.py`, `folder_opener.py`, `updater.py`, `config.json` |
| 6 | ビルド/CI/依存 | `.github/workflows/*`, `requirements*`, `pyproject.toml`, `YTDownloaderGUI.spec`, `installer.iss`, `README.md`, `.gitignore` |

---

## サマリ(重大度順)

状態: ✅ 対応済み(修正コミット。「今回」は2026-10-02の修正) / 🔶 一部対応 / 見送り・空欄 未対応

### 高

| ID | 状態 | 場所 | 内容 |
|---|---|---|---|
| H1 | ✅ fe8e294 | `src/main.py:27` | GUIビルド(`console=False`)では `sys.stderr` が None。例外フック先頭の `sys.stderr.write` で AttributeError となり、crash.log 記録もダイアログも出ない |
| H2 | ✅ fe8e294 | `src/workers.py:379-424` | mp3変換時、同名の `Title.webm`/`.m4a` が既存だと yt-dlp がそれを入力に使い変換後に削除する。衝突判定が `.mp3` しか見ていないためユーザーファイルが消える(再現済み) |
| H3 | ✅ ce032ae | `.github/workflows/release.yml:47-54, 88-96` | 同梱 ffmpeg を「最新版」URLからハッシュ検証なしで取得。改ざん時はアップデータ経由で全ユーザーに `/VERYSILENT` 実行される |

### 中

| ID | 状態 | 場所 | 内容 |
|---|---|---|---|
| M1 | ✅ ce032ae + 今回(アップデーターがSHA256SUMSと照合。macOSのquarantine除去は照合後のみなので維持) | `src/updater.py:145-192`, `release.yml:125-143` | インストーラーのハッシュ/署名検証なし。macOS は quarantine も除去。リリースに SHA256SUMS なし |
| M2 | ✅ ce032ae | `.github/workflows/ci.yml` | `permissions:` 未指定。全 action がタグ参照で SHA 固定なし |
| M3 | ✅ ce032ae | `requirements*.txt` | ハッシュなし・`--require-hashes` 未使用 |
| M4 | ✅ 今回 | `main_window.py:307`, `main_window_ui.py:148` 他 | 動画タイトル・エラー文が QLabel/QMessageBox の AutoText で HTML として描画され得る |
| M5 | ✅ ec2acf1 | `main_window.py:177-187, 291` | 取得中に URL を変えても古いワーカーが無効化されず、古い動画情報で新URLをダウンロード可能 |
| M6 | ✅ cf8b1f8 | `workers.py:499` | タイトル中の `%(` が outtmpl として解釈され、保存名がずれて衝突判定・後片付けが効かない(再現済み) |
| M7 | ✅ 6ac3848 | `workers.py:460-468, 238` | except 節内の `os.listdir` 失敗で `finished_error` が送られず UI がビジーのまま固まる |
| M8 | ✅ 今回(probe後・切り抜き中もキャンセル可。再エンコードの所要時間は動画長次第のため一律のタイムアウトは設けない) | `workers.py:521-523`, `clip_trimmer.py` | probe・クリップ再エンコード中はキャンセル不可、ffmpeg にタイムアウトなし。キャンセル後も「完了」扱い |
| M9 | ✅ ec2acf1 | `main_window.py:865-879` | 終了時の待ちが 10秒×ワーカー数。タイムアウトしても終了し、ダウンロード中でもアップデートを適用 |
| M10 | ✅ ec2acf1 | `main_window.py:270-271, 890-899` | 再取得時に旧 FormatListWorker が終了待ち対象から外れ、解放もされない |
| M11 | ✅ ec2acf1 + 今回(他のダイアログ表示中は更新提案を保留、更新DL中は動画DLを開始しない) | `main_window.py:615-630, 851-858` | アップデート適用中に終了をキャンセルすると、ボタンなしモーダルが残り操作不能 |
| M12 | ✅ cf8b1f8 | `clip_trimmer.py:201-202, 230` | `str(float)` が指数表記(`9.9e-06`)になり ffmpeg が時刻として受け付けない |
| M13 | ✅ cf8b1f8 | `clip_range.py:32-38` | `parse_clip_time('1e308:0:0')` が inf を返し、スロットで未捕捉 OverflowError(再現済み) |
| M14 | ✅ 6ac3848 | `clip_trimmer.py:102, 122, 252` | 固定の一時ファイル名で既存同名ファイルを上書き/削除し得る |
| M15 | ✅ 今回(実測では音声ずれは発生せず。キーフレーム時刻の換算を正確化) | `clip_trimmer.py:164-170, 195-202` | start_time≠0 のファイルでキーフレーム時刻がずれる可能性(推測) |
| M16 | ✅ 今回 | `clip_trimmer.py:225-228`, `config.py:32-39, 75-79` | エンコーダ設定: 既定キー `avc1`/`vp09`/`vp08` は ffprobe の codec_name と一致せず無効。AV1/HEVC は既定品質で再エンコード。VP9 に `-b:v 0` なし。dict 値未検証 |
| M17 | ✅ cf8b1f8 | `yt_dlp_selection.py:37-43` | `check_formats` 未指定のため新しい動画でテストダウンロードが走り、メインスレッドから通信(再現済み) |
| M18 | ✅ 今回 | `format_engine.py:182-200` | 1080p 以下が無いと末尾 `/b` で 4K が選ばれるのに「1080pでダウンロード」を表示(再現済み) |
| M19 | ✅ 今回 | `format_engine.py:117, 181, 187` | width 欠落時に 0 扱いとなり横長動画に縦型上限がかかる、表示も "0x2160"(再現済み) |
| M20 | ✅ 今回 | `formats.py:91-95` | AUDIO_BEST_M4A が結合フォーマットのみのサイトで必ず失敗。コメントの前提も誤り(再現済み) |
| M21 | ✅ 今回 | `main_window.py:725, 805-818` | 保存先の相対パス・`~` を未検証 |
| M22 | ✅ 今回 | `release.yml:86-96`, `licenses/FFMPEG_NOTICE.md` | macOS dmg にライセンス未同梱、NOTICE に macOS 版 ffmpeg の出典なし |
| M23 | ✅ ce032ae | `requirements.in:7`, `README.md:125` | README は「yt-dlp を固定」と記載、実際は下限指定 |
| M24 | ✅ 今回 | `release.yml:77`, `updater.py:75-77` | arm64 専用 dmg を Intel Mac にも配信し得る(推測) |
| M25 | 見送り(大規模リファクタリングのため別途) | `main_window.py` 全体 | MainWindow が約850行・9責務。状態遷移バグ群(M5, M9-M11)の温床 |

### 低

| ID | 状態 | 場所 | 内容 |
|---|---|---|---|
| L1 | ✅ 今回 | `updater.py:123`, `main_window.py:577` | アセット名・ダウンロードURLを未検証(パストラバーサル、実害は低) |
| L2 | ✅ 今回 | `updater.py:71-74` | "setup" を含まない exe も選択され `/VERYSILENT` 実行される |
| L3 | ✅ 今回 | `updater.py:152, 202-234` | `.part` 経由の書き込みでない、旧インストーラーが溜まる、macOS スクリプトの mv 失敗が無ログ |
| L4 | 🔶 今回(適用直前にハッシュを再照合。照合から起動までの僅かな間は残る) | `updater.py:168` → `main_window.py:879` | ダウンロード〜実行間の TOCTOU(同一ユーザー権限なので影響限定) |
| L5 | ✅ 今回 | `config.py:69-83` | 値範囲未検証(timeout=0、負数、NaN/Infinity) |
| L6 | ✅ 今回 | `paths.py:22, 27` | `LOCALAPPDATA`/`XDG_DATA_HOME` の相対パスを許容 |
| L7 | ✅ 今回 | `paths.py:92-97` | SHGetKnownFolderPath 失敗時に `CoTaskMemFree` 漏れ、argtypes 未宣言 |
| L8 | ✅ 今回 | `main.py:39` | 非GUIスレッドの例外で QMessageBox を出し得る(推測) |
| L9 | ✅ 今回 | `main.py:8, 64-69` | import 時失敗が crash.log に残らない |
| L10 | ✅ 今回 | `workers.py:129-132, 158-159` | サムネイル/ストーリーボード取得で URL スキーム・サイズ無制限 |
| L11 | ✅ 今回 | `workers.py:141-142, 460-468`, `errors.py` | トレースバック未記録、`str(exc)` が空だと空ダイアログ |
| L12 | ✅ 今回 | `workers.py:33` | 端末起動時にエラー文へ ANSI 色コード混入(`no_color` 未指定) |
| L13 | ✅ 今回 | `errors.py:547-557` | SSL 証明書エラー等を「ネットワーク切断」と誤案内 |
| L14 | ✅ 今回 | `workers.py:327-335` | 進捗の component_percent が 1 を超え得る、経過時間に probe を含む |
| L15 | 🔶 今回(probe・切り抜きはffmpegの場所を明示。FFmpegFD向けのcontextvar設定は残し、属性が無くても落ちないようにした) | `workers.py:445`, `clip_trimmer.py:250` | yt-dlp 非公開属性 `FFmpegPostProcessor._ffmpeg_location` に依存。probe は ffmpeg_location を渡さず暗黙依存 |
| L16 | ✅ 今回 | `clip_trimmer.py:103-110` | サムネイル抽出失敗時に途中ファイルが残る |
| L17 | ✅ 今回 | `clip_trimmer.py:157-170` | キーフレーム探索がファイル全体を走査、`float(pts_time)` が try 外 |
| L18 | ✅ 今回(先頭の欄は60以上も可) | `clip_range.py:24` | 時刻パースが寛容すぎる(`1_0`、全角、`1e3`、`90:00` など受理) |
| L19 | ✅ 今回 | `clip_range.py:44, 63` | ファイル名ラベルの秒丸めで範囲表記が不正確 |
| L20 | ✅ 今回 | `clip_trimmer.py:258` | `video_stream_count` の名前が実態と不一致 |
| L21 | ✅ 今回 | `format_engine.py:71-76` | 手動 format_id が拡張子名・予約語と衝突すると別フォーマットが選ばれる(再現済み) |
| L22 | 🔶 今回(握りつぶしは維持し、トレースバックをcrash.logに記録) | `format_engine.py:89-93` | `except Exception` で yt-dlp API 変更も握りつぶす |
| L23 | 🔶 今回(deepcopyを1回に、select_formatへ改名。呼び出し毎のYoutubeDL生成は残る) | `format_engine.py:88`, `yt_dlp_selection.py:40-43` | 呼び出し毎に deepcopy×2 と YoutubeDL 生成。`select_formats` の名前と戻り値が誤解を招く |
| L24 | ✅ 今回 | `format_engine.py:138` | 縦型動画で「1920pに制限」と表示 |
| L25 | ✅ 今回 | `storyboard.py:44-49, 67-78` | fragment に url 無しで KeyError、負の duration、width/height 欠落の候補を許容 |
| L26 | ✅ 今回 | `main_window.py:446-456` | ストーリーボード取得スレッド数に上限なし、URL変更後の結果がキャッシュに混入 |
| L27 | ✅ 今回 | `main_window.py:916-921` | ユーザーキャンセルで「失敗」critical ダイアログ、エラー後に「フォルダを開く」が無効のまま |
| L28 | ✅ 今回(ログ表示中はユーザーが広げた高さを保つ。「最小サイズ0」は誤指摘で、0はQtがレイアウトから最小サイズを決める指定。明示的に設定すると起動時に中身が重なったため撤回) | `main_window.py:150-165` | ユーザーが変えたウィンドウ高さを強制的に戻す、最小サイズ 0 のまま |
| L29 | ✅ 今回 | `main_window.py:714, 539, 578-600` | 終了済みワーカー・進捗ダイアログを deleteLater しない |
| L30 | 🔶 今回(子ウィジェットのクリックを拾うためアプリ全体のフィルタは維持し、判定を軽量化) | `main_window.py:104-123` | アプリ全体への eventFilter |
| L31 | ✅ 今回 | `widgets.py:284-300` | RangeSlider が右/中クリックでも反応、キーボード操作不可 |
| L32 | ✅ 今回 | `theme.py` ほか | 色が各所に直書き、thumbnail の二重スケーリング |
| L33 | 🔶 今回(ISCCの場所を探索し、6系であることを確認・版をログに出す。厳密な版固定は未対応) | `release.yml:59-66` | Inno Setup のバージョン未固定、パス決め打ち |
| L34 | ✅ 今回 | `ci.yml`, `release.yml` | timeout-minutes / concurrency なし |
| L35 | ✅ 今回 | CI | `.in` とロックの整合を CI で検査していない |
| L36 | ✅ 今回 | `YTDownloaderGUI.spec:42, 59` | `upx=True`、exe にバージョンリソースなし |
| L37 | ✅ 今回 | `.gitignore:21-22` | crash.log のコメントが実装と不一致、`.icns`/`*.iconset` 未 ignore |

---

## 問題なしと確認された点

- 非公開 ctx への依存は完全に解消済み(使用は `process_ie_result`, `add_post_processor(when="pre_process")`, `pp_key`, 例外クラスのみ)。
- ffmpeg 引数インジェクションなし(yt-dlp が `file:` 前置、リスト引数、shell 不使用)。
- シグナルの二重接続・接続漏れなし。ワーカースレッドから GUI を直接操作する箇所なし。
- workflow に `pull_request_target` や信頼できない入力のスクリプト埋め込みなし、シークレット露出なし、`contents: write` は release ジョブのみ。
- バージョンの単一情報源(VERSION)が spec/iss/updater/release で成立。
- `folder_opener` はコマンド・パスインジェクションなし。
- config.json の破損時は既定値にフォールバック。アプリからの書き込みはなし。
- 未コミットの `updater.py` 差分(`_launch_windows_installer` の OS ガード)に問題なし。
- `.in` とロックファイルのアプリ依存バージョンは現時点で一致。

---

## 推奨修正順

1. ✅ **H1, H2**: 小さな修正でデータ消失と障害診断不能を解消。
2. ✅ **M5, M9, M10, M11**: URL変更・ワーカー管理・終了処理を状態遷移としてまとめて修正(M11 は一部。M25 のリファクタリングと合わせて検討)。
3. ✅ **M6, M12, M13, M17**: それぞれ1〜数行の修正。
4. ✅ **H3, M1, M2, M3**: サプライチェーン対策(ffmpeg ハッシュ固定、SHA256SUMS、permissions、ロックのハッシュ化)。M1 はアップデーター側の検証が未対応。
5. ✅ 残りの中・低を順次(M7, M14 は対応済み。2026-10-02 に M25 以外を対応。🔶 は表の注記の範囲まで対応)。
6. M25(MainWindowの責務分割)は未対応。
