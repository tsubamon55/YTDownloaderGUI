"""保存先フォルダをOSのファイルマネージャで開く。Windowsでは、同じフォルダを
既に開いているエクスプローラーのウィンドウがあれば新しく開かずに前面へ出す"""

import os
import sys
from typing import Any

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices

from paths import log_debug


def find_open_explorer_window(path: str) -> Any | None:
    """指定フォルダを既に開いているエクスプローラーウィンドウがあれば返す(Windows専用)"""
    normalized = os.path.normcase(os.path.normpath(path))
    try:
        import win32com.client

        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            try:
                folder_path = window.Document.Folder.Self.Path
            except Exception:
                # 制御パネル等、フォルダを持たないシェルウィンドウもあるため無視して次へ
                continue
            if os.path.normcase(os.path.normpath(folder_path)) == normalized:
                return window
    except Exception as e:
        log_debug(f"find_open_explorer_window: シェルウィンドウの列挙に失敗 ({e!r})")
        return None
    return None


def _bring_to_front(window: Any) -> None:
    try:
        import win32gui

        window.Visible = True
        win32gui.SetForegroundWindow(window.HWND)
    except Exception as e:
        log_debug(f"open_folder: 既存ウィンドウの前面化に失敗 ({e!r})")


def open_folder(path: str) -> None:
    if sys.platform == "win32":
        window = find_open_explorer_window(path)
        if window is not None:
            _bring_to_front(window)
            return

    # Qtの薄いラッパー経由でOS標準のファイルマネージャ(Finder/Nautilus等)を開く。
    # Windows以外では、既存ウィンドウの再利用のような最適化は行わず素直に開くだけにする
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
