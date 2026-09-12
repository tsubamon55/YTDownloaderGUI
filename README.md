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
mp3変換や動画+音声の結合ダウンロードには ffmpeg が必要なので、以下の手順で配置してください。

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
├── main.py
└── ...
```

アプリは実行時にこの `ffmpeg/` フォルダを自動検出して使用します(システムのPATHには依存しません)。

### 3. ソースから実行

```powershell
.venv\Scripts\python main.py
```

## 配布用exeのビルド

Python未インストールの環境でも動く単体exeを作成できます。

```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --name YTDownloaderGUI --windowed --add-data "ffmpeg;ffmpeg" main.py
```

ビルド後、`dist\YTDownloaderGUI\` フォルダ一式(`YTDownloaderGUI.exe` と `_internal`)を配布してください。
フォルダ内にffmpegが同梱されているため、配布先のPCに追加のインストール作業は不要です。

## ライセンスに関する注意

同梱・利用する ffmpeg essentials build は **GPL** ライセンスの構成でビルドされています。
第三者に配布する場合は、ffmpegのライセンス表記(COPYING.GPLv3など)を同梱し、ソースの入手先を明記することを推奨します。
