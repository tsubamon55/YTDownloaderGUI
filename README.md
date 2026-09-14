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

## セットアップ(開発用)

### 1. 仮想環境の作成と依存関係のインストール

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 2. ffmpegの配置

このリポジトリに `ffmpeg/` フォルダは含まれていません(バイナリが大きいため `.gitignore` で除外)。
mp3変換や動画+音声の結合ダウンロードには ffmpeg が必要です。以下のどちらかの方法で用意してください。

**方法A: プロジェクト直下に配置する(推奨)**

1. [FFmpeg (Essentials Build) by Gyan.Dev](https://www.gyan.dev/ffmpeg/builds/) をダウンロード、または winget を使う:
   ```powershell
   winget install --id Gyan.FFmpeg.Essentials -e
   ```
2. 展開した `bin` フォルダの中から `ffmpeg.exe` と `ffprobe.exe` を、プロジェクト直下の `ffmpeg/` フォルダにコピーする。

```
youtube-downloader/
├── ffmpeg/
│   ├── ffmpeg.exe
│   └── ffprobe.exe
├── src/
│   └── main.py
└── ...
```

**方法B: システムにインストールしてPATHを通す**

`winget install --id Gyan.FFmpeg.Essentials -e` などでffmpegをインストールし、PATHが通っていれば、
`ffmpeg/` フォルダが無くてもアプリが自動検出して使用します。

アプリは起動時にまず同梱の `ffmpeg/` フォルダを探し、無ければシステムPATH上のffmpegにフォールバックします。

### 3. ソースから実行

```powershell
.venv\Scripts\python src\main.py
```

## 設定ファイル (config.json)

タイムアウトやmp3変換品質など、頻繁に変える必要はないが調整したい場合がある定数は、
実行ファイルと同じフォルダ(開発時はプロジェクト直下)の `config.json` にまとめてあります。
ファイルが無い/一部のキーが欠けている場合はコード内蔵の既定値が使われるため、
変更したい項目だけを残して他を削除しても構いません。

| キー | 既定値 | 内容 |
| --- | --- | --- |
| `thumbnail_max_candidates` | `5` | サムネイル取得時に試す候補URLの最大数 |
| `thumbnail_fetch_timeout_seconds` | `5` | サムネイル1候補あたりの取得タイムアウト(秒) |
| `storyboard_fetch_timeout_seconds` | `10` | クリップ範囲スライダーのプレビュー画像取得タイムアウト(秒) |
| `info_fetch_debounce_ms` | `700` | URL入力後、自動でフォーマット取得を始めるまでの待ち時間(ミリ秒) |
| `mp3_quality` | `"192"` | 「音声のみ (mp3)」選択時の変換ビットレート(kbps) |
| `clip_video_encoder_by_codec_prefix` | (コード参照) | クリップ切り出し時の再エンコード設定(映像コーデック毎の `[エンコーダ, CRF値]`)。表に無いコーデックはffmpegの既定設定にフォールバックします |

## 配布用exeのビルド

Python未インストールの環境でも動くexeを作成できます。ビルド設定は `YTDownloaderGUI.spec` にまとめてあるので、ソースコードを変更したら以下を実行するだけで再ビルドできます。

```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller YTDownloaderGUI.spec --noconfirm
```

`dist\YTDownloaderGUI\` フォルダ一式(`YTDownloaderGUI.exe` と `_internal`)が更新されます。
フォルダ内にffmpegが同梱されているため、配布先のPCに追加のインストール作業は不要です。

**注意:** `YTDownloaderGUI.exe` 単体だけをコピーして配布・実行すると `_internal` フォルダが見つからず起動時にエラーになります。フォルダごと配布するか、下記のインストーラーを使ってください。

## インストーラーのビルド

[Inno Setup](https://jrsoftware.org/isinfo.php) がインストールされていれば、`installer.iss` からインストーラーを作成できます(未インストールの場合は `winget install --id JRSoftware.InnoSetup -e`)。

```powershell
.venv\Scripts\pyinstaller YTDownloaderGUI.spec --noconfirm
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

`installer_output\YTDownloaderGUI-Setup-<バージョン>.exe` が生成されます。このインストーラーは管理者権限不要で `%LOCALAPPDATA%\Programs\YTDownloaderGUI` にインストールし、スタートメニュー/デスクトップにショートカットを作成します。

バージョンを上げる場合は `installer.iss` 冒頭の `#define MyAppVersion "1.0.0"` を書き換えてから再ビルドしてください。

## ライセンスに関する注意

同梱・利用する ffmpeg essentials build は **GPL** ライセンスの構成でビルドされています。
第三者に配布する場合は、ffmpegのライセンス表記(COPYING.GPLv3など)を同梱し、ソースの入手先を明記する必要があります。

`licenses/` フォルダに以下を用意しています(インストーラーにも `licenses/` として同梱されます):

- `COPYING.GPLv3.txt` — GNU General Public License v3 の正式テキスト
- `FFMPEG_NOTICE.md` — 同梱ffmpegのビルド元・ソース入手先の明記
