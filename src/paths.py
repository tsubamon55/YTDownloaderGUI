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


def get_app_data_dir() -> str:
    """インストール先(Program Files/Applications等)は書き込み不可なことがあるため、
    OSごとの慣例に沿った、ユーザー書き込み可能なアプリデータ配置先を返す"""
    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "YTDownloaderGUI")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "YTDownloaderGUI")
    # Linux等: XDG Base Directory仕様
    base = os.getenv("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "YTDownloaderGUI")


def get_log_file_path() -> str:
    return os.path.join(get_app_data_dir(), "crash.log")


def append_log_entry(text: str, log_path: str | None = None) -> bool:
    """crash.logへタイムスタンプ付きで1件追記する。書き込めた場合True。
    書き込み失敗はアプリの動作に影響させないため例外は投げない"""
    log_path = log_path or get_log_file_path()
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        return False
    return True


def log_debug(message: str) -> None:
    """crash.logと同じファイルに、ユーザーには見せず処理を続行させた例外の情報を記録する。
    (except Exceptionで握りつぶすだけだと、後から不具合の原因を追跡できなくなるため)"""
    append_log_entry(message)


def remove_file_quietly(path: str, context: str) -> bool:
    """pathのファイルがあれば削除する。削除できた場合True。
    後片付け目的の削除が失敗しても本来の処理を止めないよう、例外は投げずに
    context(呼び出し元の名前)付きでlog_debugへ記録するだけにする"""
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError as e:
        log_debug(f"{context}: {path} の削除に失敗 ({e!r})")
        return False
    return True


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
    # macOS/Linuxでは ~/Downloads が標準の配置先であり、Windows固有のAPIを呼ぶ必要が無い
    if sys.platform != "win32":
        return fallback

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


def _bundle_search_dirs() -> list[str]:
    """同梱ファイルの探索先。PyInstallerのonedirビルドは、同梱ファイルをexeと同階層に
    置く配置と`_internal`配下(_MEIPASS)にまとめる配置のどちらにもなり得るため両方を見る
    (spec側の設定だけに依存していると、specを再生成した拍子に読み込めなくなるため)"""
    dirs = [get_base_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    return dirs


def find_bundled_file(filename: str) -> str | None:
    """実行ファイル/プロジェクト直下、またはPyInstallerの一時展開先に同梱された
    ファイルを探す(どこにも無ければNone)"""
    for base in _bundle_search_dirs():
        path = os.path.join(base, filename)
        if os.path.isfile(path):
            return path
    return None


# 同梱ffmpegの実行ファイル名(拡張子の有無)はOSによって異なる
FFMPEG_EXECUTABLE_NAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


def get_ffmpeg_location() -> str | None:
    for base in _bundle_search_dirs():
        ffmpeg_dir = os.path.join(base, "ffmpeg")
        if os.path.isfile(os.path.join(ffmpeg_dir, FFMPEG_EXECUTABLE_NAME)):
            return ffmpeg_dir

    # 同梱フォルダが無い場合、システムPATHのffmpegにフォールバック
    # (winget/brew等で別途インストール済みの開発者向け)
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return os.path.dirname(system_ffmpeg)
    return None
