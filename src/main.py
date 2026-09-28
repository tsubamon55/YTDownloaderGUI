import os
import sys
import traceback
from datetime import datetime

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from main_window import MainWindow
from paths import find_bundled_file, get_log_file_path

APP_ICON_PATH = os.path.join("downloader-icon", "app-icon-1024.png")


def install_exception_hook():
    """Qtのスロット内(main_window.pyのon_xxxハンドラ等)で起きた想定外の例外は
    通常のtry/exceptでは捕まらずイベントループの外側まで伝播し、デフォルトでは
    ダイアログも出さずに abort() でアプリごと落ちる。sys.excepthookを差し替えて
    ログに残しつつダイアログで知らせ、アプリ自体は継続できるようにする。"""
    log_path = get_log_file_path()

    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(message)

        logged = True
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{message}\n")
        except OSError:
            # 書き込みに失敗してもアプリは継続させるが、案内の文言は実際の結果に合わせる。
            # 残っていないログの場所を案内すると、調査の際に誤った手がかりを与えてしまう
            logged = False

        if QApplication.instance() is not None:
            detail = (
                f"詳細はログに記録しました:\n{log_path}"
                if logged
                else f"ログファイルへの記録には失敗しました:\n{log_path}"
            )
            QMessageBox.critical(
                None,
                "予期しないエラー",
                "予期しないエラーが発生しました。動作が不安定な場合はアプリを再起動してください。\n\n"
                f"{detail}\n\n{exc_type.__name__}: {exc_value}",
            )

    sys.excepthook = handle_exception


def main():
    install_exception_hook()
    app = QApplication(sys.argv)

    # QApplication側に設定しておくと、個別にsetWindowIconしていないダイアログ
    # (QMessageBox等)にもそのまま適用され、Windowsではタスクバーのアイコンにもなる
    icon_path = find_bundled_file(APP_ICON_PATH)
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
