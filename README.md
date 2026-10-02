# yt-dlp GUI ダウンローダー

[yt-dlp](https://github.com/yt-dlp/yt-dlp) を PyQt6 でGUI化した動画/音声ダウンローダーです。

## 機能

- URLを入力してダウンロード
- 保存先フォルダの選択
- 形式選択
  - 動画 (最高画質 mp4)
  - 動画 (最高画質)
  - 音声のみ (mp3)
  - 音声のみ (最高音質)
- 進捗バー・ログ表示
- ダウンロードのキャンセル
- 起動時の自動アップデート確認・適用

## セットアップ(開発用)

Windows / macOS のどちらでも動作します。OSに応じたコマンドを使ってください。

### 1. 仮想環境の作成と依存関係のインストール

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**macOS**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` は、全パッケージのバージョンを固定したロックファイルです(後述の「依存関係の更新」参照)。
`pywin32` はWindows専用パッケージで、`sys_platform == "win32"` の条件付きのためmacOSにはインストールされません。

### 2. ffmpegの配置

このリポジトリに `ffmpeg/` フォルダは含まれていません(バイナリが大きいため `.gitignore` で除外)。
mp3変換や動画+音声の結合ダウンロードには ffmpeg が必要です。以下のどちらかの方法で用意してください。

**方法A: プロジェクト直下に配置する(推奨)**

Windows: [FFmpeg (Essentials Build) by Gyan.Dev](https://www.gyan.dev/ffmpeg/builds/) をダウンロード、または winget を使う:
```powershell
winget install --id Gyan.FFmpeg.Essentials -e
```
展開した `bin` フォルダの中から `ffmpeg.exe` と `ffprobe.exe` を、プロジェクト直下の `ffmpeg/` フォルダにコピーする。

```
YTDownloaderGUI/
├── ffmpeg/
│   ├── ffmpeg.exe       (Windows)
│   ├── ffprobe.exe      (Windows)
│   ├── ffmpeg           (macOS)
│   └── ffprobe          (macOS)
├── src/
│   └── main.py
└── ...
```

macOS: [ffmpeg公式サイト](https://ffmpeg.org/download.html)のビルド、または Homebrew でインストールしたバイナリ (`brew install ffmpeg` 後、`which ffmpeg`/`which ffprobe` で場所を確認) を `ffmpeg/` フォルダにコピーする。

**方法B: システムにインストールしてPATHを通す**

Windows: `winget install --id Gyan.FFmpeg.Essentials -e`
macOS: `brew install ffmpeg`

いずれもPATHが通っていれば、`ffmpeg/` フォルダが無くてもアプリが自動検出して使用します。

アプリは起動時にまず同梱の `ffmpeg/` フォルダ(OSに応じて `ffmpeg.exe`/`ffmpeg` を探索)を探し、無ければシステムPATH上のffmpegにフォールバックします。

### 3. ソースから実行

**Windows (PowerShell)**

```powershell
.venv\Scripts\python src\main.py
```

**macOS**

```bash
.venv/bin/python src/main.py
```

### 4. テストとコードチェック

テストは標準ライブラリのunittestで実行します。書式と型のチェックには開発用ツール(ruff / mypy)を使います。
アプリの実行には不要なので、依存関係は `requirements-dev.txt` に分けています(配布用ビルドに使うPyInstallerもここに含みます)。

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests
python -m ruff check src tests
python -m mypy
```

### 5. 依存関係の更新

依存関係は、直接使うパッケージを書く `.in` ファイルと、そこから生成するロックファイル(`.txt`)に分けています。
ロックファイルには間接依存を含む全パッケージのバージョンとハッシュが固定されているため、どのPCやCIでも同じ環境を再現でき、
PyPI上のファイルが差し替えられていた場合はインストールが失敗します。

| ファイル | 内容 | 編集 |
| --- | --- | --- |
| `requirements.in` | アプリの実行に必要なパッケージ | 手で編集する |
| `requirements-dev.in` | 開発・ビルド用のツール(ruff / mypy / PyInstaller) | 手で編集する |
| `requirements.txt` / `requirements-dev.txt` | 上記から生成したロックファイル | 手で編集しない |

パッケージを追加・更新するときは、`.in` を編集してからロックファイルを再生成し、`.in` と `.txt` を一緒にコミットします。
再生成には [uv](https://docs.astral.sh/uv/) を使います(`pip install uv` か、公式のインストーラーで入れてください)。
`--universal` を付けているため、どのOSで生成してもWindows/macOS両方で使えるロックファイルになります。

```bash
uv pip compile requirements.in -o requirements.txt --universal --python-version 3.13 --generate-hashes
uv pip compile requirements-dev.in -o requirements-dev.txt --universal --python-version 3.13 --generate-hashes
python -m pip install -r requirements-dev.txt
```

既存パッケージを新しいバージョンに上げる場合は、上のコマンドに `--upgrade`(特定のパッケージだけなら `--upgrade-package <名前>`)を付けます。
yt-dlpは `requirements.in` で下限だけを指定しているため、`--upgrade` を付けて再生成すると新しい版に上がります。
上がった場合は、`requirements.in` の注意書きの通り `tests/test_yt_dlp_selection.py` を含むテストが通ることを確認してからコミットしてください。

## CI/CD (GitHub Actions)

- **CI** (`.github/workflows/ci.yml`): `master` へのpushとPull Requestで、ruffと `.in`・ロックファイルの整合確認をUbuntu上で、mypyとunittestをWindows/macOS上で実行します。
- **リリース** (`.github/workflows/release.yml`): `VERSION` と同じ番号の `v<バージョン>` タグをpushすると、CIを通したうえで
  `YTDownloaderGUI-Setup-<バージョン>.exe`(Windows)と `YTDownloaderGUI-<バージョン>-arm64.dmg`(macOS, Apple Silicon)をビルドし、
  GitHub Releasesに**下書き**として添付します。公開した時点で既存ユーザーの自動アップデートが始まるため、
  アセットを確認してから手動で公開してください。各アセットのSHA256を記した `SHA256SUMS` も添付されます。
  同梱するffmpegは版とSHA256を `release.yml` に固定しており、更新する場合はURLとハッシュを一緒に書き換えます。

```bash
git tag v$(cat VERSION)
git push origin v$(cat VERSION)
```

## 設定ファイル (config.json)

タイムアウトやmp3変換品質など、頻繁に変える必要はないが調整したい場合がある定数は、
実行ファイルと同じフォルダ(開発時はプロジェクト直下)の `config.json` にまとめてあります。
ファイルが無い/一部のキーが欠けている場合はコード内蔵の既定値が使われるため、
変更したい項目だけを残して他を削除しても構いません。
型が違う値や、範囲外の値(0秒以下のタイムアウト・負の件数など)は無視して既定値を使います。

| キー | 既定値 | 内容 |
| --- | --- | --- |
| `thumbnail_max_candidates` | `5` | サムネイル取得時に試す候補URLの最大数 |
| `thumbnail_fetch_timeout_seconds` | `5` | サムネイル1候補あたりの取得タイムアウト(秒) |
| `storyboard_fetch_timeout_seconds` | `10` | クリップ範囲スライダーのプレビュー画像取得タイムアウト(秒) |
| `info_fetch_debounce_ms` | `700` | URL入力後、自動でフォーマット取得を始めるまでの待ち時間(ミリ秒) |
| `mp3_quality` | `"192"` | 「音声のみ (mp3)」選択時の変換ビットレート(kbps) |
| `clip_video_encoder_by_codec_prefix` | (コード参照) | クリップ切り出し時の再エンコード設定。キーはffprobeが返す映像コーデック名(`h264`・`vp9`等)、値は `[エンコーダ, CRF値]`。表に無いコーデック(HEVC・AV1等)は、出力がwebmなら `vp9`、それ以外は `h264` の設定で再エンコードします |
| `auto_update_enabled` | `true` | 起動時にGitHub Releasesへ新バージョンの有無を問い合わせるかどうか |
| `update_check_timeout_seconds` | `5` | アップデート確認(GitHub API)・ダウンロードそれぞれ1回あたりのタイムアウト(秒) |

## 自動アップデート

ビルド済み実行ファイル(`sys.frozen`)で起動した場合のみ、起動から少し経ったタイミングで
[GitHub Releases](https://github.com/tsubamon55/YTDownloaderGUI/releases) の最新リリースを
問い合わせ、同梱の `VERSION` より新しいバージョンが公開されていれば通知します。「今すぐ
ダウンロードしてインストール」を選ぶと、OSに応じたリリースアセットをダウンロードして適用し、
アプリを再起動します。

いずれのOSでも、更新の適用はアプリの終了が確定してから始まります(動画のダウンロード中は
更新を提案せず、終了確認で「いいえ」を選んだ場合は更新も中止します)。

- **Windows**: ファイル名が `.exe` で終わり `Setup` を含むアセットを選び、アプリ終了時に
  `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` でサイレントインストールし、インストーラーの
  `[Run]` セクションでアプリを再起動します(`installer.iss` 参照)。
- **macOS**: ファイル名が `.dmg` で終わり、実行中のMacのCPUに合う(名前に `arm64`/`x86_64`/`universal` を含む)
  アセットをダウンロードし、アプリ終了後にヘルパースクリプトが
  dmgをマウントして、実行中だった `.app` と同じ場所へ `ditto` でコピー・入れ替えてから再起動します。
  入れ替えに失敗した場合は元の `.app` が残ります。ダウンロードしたdmg由来の `com.apple.quarantine`
  属性を外すため、Gatekeeperの警告なしで再起動できます(このアプリ自体はアドホック署名のみで
  公証していないため、手動でdmgを開いた場合は引き続き初回起動時に警告が出ます)。
  ヘルパーの実行結果は `crash.log` に記録されます。
  (アーキテクチャ表記の無い `.dmg` は、表記を付ける前のApple Silicon専用ビルドとみなします)

ダウンロードしたファイルは、同じリリースに添付された `SHA256SUMS` の値と照合し、一致した場合だけ
適用します(適用の直前にも再度照合します)。`SHA256SUMS` が無い、または該当ファイルの記載が無い
リリースは更新として扱いません。前回までにダウンロードしたファイルは、次の確認時に削除されます。

そのため、GitHub Releasesで新バージョンを公開する際は、`YTDownloaderGUI-Setup-<バージョン>.exe`
(Windows)・`YTDownloaderGUI-<バージョン>-arm64.dmg`(macOS)と `SHA256SUMS` をアセットとして添付し
(`release.yml` が自動で行います)、タグ名(`tag_name`)にはVERSIONファイルと同じバージョン番号を使ってください。
ソースから直接実行している間や `auto_update_enabled` を `false` にした場合は確認自体を行いません。

## アプリアイコン

`downloader-icon/` にアイコンの元データを置いています。

| ファイル | 用途 |
| --- | --- |
| `app-icon.svg` | 編集用の元データ(ベクター) |
| `app-icon-1024.png` | ウィンドウ/タスクバー用アイコン(`main.py` が実行時に読み込む)と、macOS用 `.icns` 生成の元画像 |
| `app-icon.ico` | Windows用(exe埋め込み・インストーラー) |

Windows用の `.exe`・インストーラーには `app-icon.ico` がそのまま使われるため、追加の手順は不要です。
macOS用の `.icns` はmacOS上でのみ生成できるため、リポジトリには含めていません。`.app` にアイコンを
付ける場合は、macOSビルドの前に一度だけ以下を実行してください(`sips`/`iconutil` はmacOS標準ツールです)。

```bash
ICONSET=downloader-icon/app-icon.iconset
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size downloader-icon/app-icon-1024.png --out "$ICONSET/icon_${size}x${size}.png"
  sips -z $((size * 2)) $((size * 2)) downloader-icon/app-icon-1024.png --out "$ICONSET/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$ICONSET" -o downloader-icon/app-icon.icns
rm -rf "$ICONSET"
```

`downloader-icon/app-icon.icns` が生成されていれば、`YTDownloaderGUI.spec` のmacOSビルド(`BUNDLE()`)が
自動的に読み込みます(無い場合はアイコン無しのままビルドされます)。

## 配布用実行ファイルのビルド

Python未インストールの環境でも動く実行ファイルを作成できます。ビルド設定は `YTDownloaderGUI.spec` にまとめてあるので、ソースコードを変更したら以下を実行するだけで再ビルドできます(`PyInstaller`はビルドを実行したOS向けの成果物しか作れないため、Windows用exeが欲しい場合はWindows上で、macOS用アプリが欲しい場合はmacOS上でそれぞれ実行してください)。

**Windows (PowerShell)**

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\pyinstaller YTDownloaderGUI.spec --noconfirm
```

`dist\YTDownloaderGUI\` フォルダ一式(`YTDownloaderGUI.exe` と、同階層に展開されるDLL・ライブラリ・`ffmpeg\`・`config.json`)が更新されます。
フォルダ内にffmpegが同梱されているため、配布先のPCに追加のインストール作業は不要です。

**注意:** `YTDownloaderGUI.exe` 単体だけをコピーして配布・実行すると、同階層にあるべきDLLやライブラリが見つからず起動時にエラーになります。フォルダごと配布するか、下記のインストーラーを使ってください。

**macOS**

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pyinstaller YTDownloaderGUI.spec --noconfirm
```

`dist/YTDownloaderGUI/` フォルダ一式に加え、`dist/YTDownloaderGUI.app` としてFinderが認識できる `.app` バンドルも生成されます(`YTDownloaderGUI.spec` の `BUNDLE()` 設定、macOS実行時のみ有効)。同梱したffmpeg等の実行ファイルが依存する共有ライブラリもPyInstallerが自動検出してバンドル内に含めるため、他のMac(同じCPUアーキテクチャ)にも `.app` ごとコピーすれば動作します。

**注意:** コード署名は行っていない(ad-hoc署名のみ)ため、他のMacに配布すると初回起動時にGatekeeperの警告が出ます。ビルドした本人のMacで使う分には問題ありません。他者に配布する場合は右クリック→「開く」で起動する旨を案内するか、Apple Developer Programでのコード署名・公証(notarization)を検討してください。

### macOS用dmgの作成

`.app` をドラッグ&ドロップでインストールできる `.dmg` にまとめる場合は、`.app` ビルド後に以下を実行します。

```bash
mkdir -p dist/dmg_staging
ln -s /Applications dist/dmg_staging/Applications
cp -R dist/YTDownloaderGUI.app dist/dmg_staging/
cp -R licenses dist/dmg_staging/licenses
hdiutil create -volname "YTDownloaderGUI" -srcfolder dist/dmg_staging -ov -format UDZO dist/YTDownloaderGUI-<バージョン>-arm64.dmg
rm -rf dist/dmg_staging
```

`dist/YTDownloaderGUI-<バージョン>-arm64.dmg` が生成されます(Intel Macでビルドした場合は `arm64` を `x86_64` にしてください。
自動アップデートはこの表記で実行中のMacに合うdmgを選びます)。マウントすると `.app` と `Applications` フォルダへのショートカットが表示され、ドラッグでインストールできます。

## インストーラーのビルド(Windowsのみ)

[Inno Setup](https://jrsoftware.org/isinfo.php) がインストールされていれば、`installer.iss` からインストーラーを作成できます(未インストールの場合は `winget install --id JRSoftware.InnoSetup -e`)。Inno SetupはWindows専用ツールのため、この手順はWindows上でのみ実行できます。

```powershell
.venv\Scripts\pyinstaller YTDownloaderGUI.spec --noconfirm
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

`installer_output\YTDownloaderGUI-Setup-<バージョン>.exe` が生成されます。このインストーラーは管理者権限不要で `%LOCALAPPDATA%\Programs\YTDownloaderGUI` にインストールし、スタートメニュー/デスクトップにショートカットを作成します。

バージョンを上げる場合は、プロジェクト直下の `VERSION` ファイルの値を書き換えてから再ビルドしてください。`installer.iss`(`MyAppVersion`)と `YTDownloaderGUI.spec`(`.app`のバージョン情報)はいずれもこの `VERSION` ファイルを唯一の情報源として参照するため、書き換えは1箇所で済みます。

## ライセンスに関する注意

同梱・利用する ffmpeg(Windows: Gyan.Dev essentials build、macOS: Martin Riedl氏のビルド)は **GPL** ライセンスの構成でビルドされています。
第三者に配布する場合は、ffmpegのライセンス表記(COPYING.GPLv3など)を同梱し、ソースの入手先を明記する必要があります。

`licenses/` フォルダに以下を用意しています(Windowsのインストーラーには `licenses/` として、macOSでは `.app` の中とdmgの直下に同梱されます):

- `COPYING.GPLv3.txt` — GNU General Public License v3 の正式テキスト
- `FFMPEG_NOTICE.md` — 同梱ffmpegのビルド元・ソース入手先の明記
