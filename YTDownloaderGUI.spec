# -*- mode: python ; coding: utf-8 -*-

import os
import re
import sys

# バージョン番号はVERSIONファイルを唯一の情報源とする(installer.issも同じファイルを参照する。
# ここで別々にハードコードすると、過去に実際に起きたバージョン表記の食い違いが再発するため)
with open(os.path.join(SPECPATH, 'VERSION'), encoding='utf-8') as f:
    app_version = f.read().strip()

datas = [
    (os.path.join(SPECPATH, 'ffmpeg'), 'ffmpeg'),
    (os.path.join(SPECPATH, 'config.json'), '.'),
    # 起動時のアップデート確認(updater.get_current_version)が自分のバージョンを知るために必要
    (os.path.join(SPECPATH, 'VERSION'), '.'),
    # ウィンドウ/タスクバー用のアイコン(main_window.pyがQIcon読み込み時に同梱ファイルとして探す)
    (os.path.join(SPECPATH, 'downloader-icon', 'app-icon-1024.png'), 'downloader-icon'),
]
if sys.platform == 'darwin':
    # 同梱ffmpegのライセンス(GPLv3)と入手先を.app自体に含める。自動アップデートは.appだけを
    # 差し替えるため、dmg側にしか置かないと更新後の環境から失われる。
    # (.appの署名後にファイルを足すと署名が壊れるため、ビルド後ではなくここで入れる。
    # Windowsはinstaller.issがlicensesフォルダを配置する)
    datas.append((os.path.join(SPECPATH, 'licenses'), 'licenses'))

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)


def _windows_version_info(version):
    """exeのプロパティ(詳細タブ)に出るバージョン情報。無いとエクスプローラーやウイルス対策ソフトから
    どの版のファイルか判別できない"""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = tuple(int(n) for n in re.findall(r'\d+', version)[:4])
    numbers += (0,) * (4 - len(numbers))
    strings = [
        StringStruct('ProductName', 'YTDownloaderGUI'),
        StringStruct('FileDescription', 'yt-dlp GUI ダウンローダー'),
        StringStruct('FileVersion', version),
        StringStruct('ProductVersion', version),
        StringStruct('InternalName', 'YTDownloaderGUI'),
        StringStruct('OriginalFilename', 'YTDownloaderGUI.exe'),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        # 0x0411 = 日本語, 1200 = Unicode
        kids=[StringFileInfo([StringTable('041104B0', strings)]), VarFileInfo([VarStruct('Translation', [0x0411, 1200])])],
    )


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='YTDownloaderGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPXで圧縮した実行ファイルはウイルス対策ソフトに誤検知されやすく、Qtのプラグインを壊すこともある。
    # インストーラー/dmg自体が圧縮されるため、配布サイズの面でも得るものが小さい
    upx=False,
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
    version=_windows_version_info(app_version) if sys.platform == 'win32' else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
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
