"""実行ファイル/ffmpeg/ダウンロードフォルダのパス解決"""

import ctypes
import os
import sys
from uuid import UUID


def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


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
    except Exception:
        pass
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
    return None
