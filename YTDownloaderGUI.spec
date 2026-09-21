# -*- mode: python ; coding: utf-8 -*-

import sys

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[],
    datas=[('ffmpeg', 'ffmpeg'), ('config.json', '.')],
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
    app = BUNDLE(
        coll,
        name='YTDownloaderGUI.app',
        icon=None,
        bundle_identifier='com.tsubamon55.ytdownloadergui',
        info_plist={
            'CFBundleShortVersionString': '1.2.0',
            'CFBundleVersion': '1.2.0',
            'NSHighResolutionCapable': True,
            'NSHumanReadableCopyright': 'yt-dlp GUI ダウンローダー',
        },
    )
