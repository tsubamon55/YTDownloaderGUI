# 同梱ffmpegについて

このアプリケーションは [FFmpeg](https://ffmpeg.org/) の実行ファイル(`ffmpeg` / `ffprobe`。Windowsでは `ffmpeg.exe` / `ffprobe.exe`)を同梱しています。

## Windows版

- ビルド提供元: [FFmpeg Builds by Gyan.Dev (Essentials Build)](https://www.gyan.dev/ffmpeg/builds/)
  (取得元: [GyanD/codexffmpeg](https://github.com/GyanD/codexffmpeg/releases)。同梱している版は `.github/workflows/release.yml` に記載)
- ライセンス: GNU General Public License v3 (GPLv3) — 同梱の `COPYING.GPLv3.txt` を参照してください
- ソースコード入手先: https://github.com/FFmpeg/FFmpeg (Gyan.Devビルドの詳細は上記ビルドページを参照)

Gyan.Dev Essentials Buildは GPL構成のライブラリ(x264等)を含むため、GPLv3の条件が適用されます。

## macOS版

- ビルド提供元: [Martin Riedl's FFmpeg Build Server](https://ffmpeg.martin-riedl.de/)(Apple Silicon向け静的ビルド。同梱している版は `.github/workflows/release.yml` に記載)
- ライセンス: GNU General Public License v3 (GPLv3) — 同梱の `COPYING.GPLv3.txt` を参照してください
- ソースコード入手先: https://github.com/FFmpeg/FFmpeg (ビルドに使われた各ライブラリの版と構成は上記ビルドサーバーの各ビルドのページを参照)

このビルドも GPL構成のライブラリ(x264等)を含むため、GPLv3の条件が適用されます。

FFmpeg自体はLGPL/GPLのデュアルライセンスです。
