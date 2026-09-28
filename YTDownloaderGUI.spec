# -*- mode: python ; coding: utf-8 -*-

import os
import sys

# バージョン番号はVERSIONファイルを唯一の情報源とする(installer.issも同じファイルを参照する。
# ここで別々にハードコードすると、過去に実際に起きたバージョン表記の食い違いが再発するため)
with open(os.path.join(SPECPATH, 'VERSION'), encoding='utf-8') as f:
    app_version = f.read().strip()

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(SPECPATH, 'ffmpeg'), 'ffmpeg'),
        (os.path.join(SPECPATH, 'config.json'), '.'),
        # 起動時のアップデート確認(updater.get_current_version)が自分のバージョンを知るために必要
        (os.path.join(SPECPATH, 'VERSION'), '.'),
        # ウィンドウ/タスクバー用のアイコン(main_window.pyがQIcon読み込み時に同梱ファイルとして探す)
        (os.path.join(SPECPATH, 'downloader-icon', 'app-icon-1024.png'), 'downloader-icon'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='YTDownloaderGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory='.',
    # Windowsではexe自体にアイコンが埋め込まれ、エクスプローラー/タスクバー/ショートカットに
    # 反映される(macOSの.appアイコンは下のBUNDLE(icon=...)側で指定するため、ここでは無視される)
    icon=os.path.join(SPECPATH, 'downloader-icon', 'app-icon.ico'),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='YTDownloaderGUI',
)

# macOSではFinderが認識できる.appバンドルも作成する(Windowsではフォルダ配布のみのため対象外)
if sys.platform == 'darwin':
    # .icnsはmacOS専用形式でWindows上では生成できないため、リポジトリには同梱せずビルド時に
    # 生成する(README「配布用実行ファイルのビルド」のsips/iconutilの手順を参照)。
    # 無い場合は従来通りアイコン無し(PyInstaller既定のアイコン)にフォールバックする
    icns_path = os.path.join(SPECPATH, 'downloader-icon', 'app-icon.icns')
    app = BUNDLE(
        coll,
        name='YTDownloaderGUI.app',
        icon=icns_path if os.path.isfile(icns_path) else None,
        bundle_identifier='com.tsubamon55.ytdownloadergui',
        info_plist={
            'CFBundleShortVersionString': app_version,
            'CFBundleVersion': app_version,
            'NSHighResolutionCapable': True,
            'NSHumanReadableCopyright': 'yt-dlp GUI ダウンローダー',
        },
    )
