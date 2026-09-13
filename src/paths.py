"""実行ファイル/ffmpeg/ダウンロードフォルダのパス解決"""

import ctypes
import os
import shutil
import sys
from datetime import datetime
from uuid import UUID


def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # 開発時: このファイルは <プロジェクトルート>/src/ にあるため、一つ上がルート
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_log_file_path() -> str:
    """インストール先(Program Files等)は書き込み不可なことがあるため、
    クラッシュログは常にユーザー書き込み可能なLOCALAPPDATA配下に置く"""
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "YTDownloaderGUI", "crash.log")


def log_debug(message: str) -> None:
    """crash.logと同じファイルに、ユーザーには見せず処理を続行させた例外の情報を記録する。
    (except Exceptionで握りつぶすだけだと、後から不具合の原因を追跡できなくなるため)"""
    log_path = get_log_file_path()
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, guid_str: str):
        u = UUID(guid_str)
        self.Data1, self.Data2, self.Data3, rest = u.fields[0], u.fields[1], u.fields[2], u.bytes[8:]
        for i, b in enumerate(rest):
            self.Data4[i] = b


def get_downloads_folder() -> str:
    fallback = os.path.join(os.path.expanduser("~"), "Downloads")
    try:
        folder_id = _GUID("{374DE290-123F-4565-9164-39C4925E467B}")  # FOLDERID_Downloads
        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, 0, ctypes.byref(path_ptr)
        )
        if result == 0 and path_ptr.value:
            path = path_ptr.value
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            if os.path.isdir(path):
                return path
    except Exception as e:
        log_debug(f"get_downloads_folder: SHGetKnownFolderPathに失敗、~/Downloadsにフォールバック ({e!r})")
    return fallback


def get_ffmpeg_location() -> str | None:
    candidates = [get_base_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(meipass)

    for base in candidates:
        ffmpeg_dir = os.path.join(base, "ffmpeg")
        if os.path.isfile(os.path.join(ffmpeg_dir, "ffmpeg.exe")):
            return ffmpeg_dir

    # 同梱フォルダが無い場合、システムPATHのffmpegにフォールバック
    # (winget等で別途インストール済みの開発者向け)
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return os.path.dirname(system_ffmpeg)
    return None
