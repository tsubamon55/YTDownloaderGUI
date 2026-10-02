import os
import sys
import traceback

# 標準ライブラリだけに依存するため、これより後の読み込みの失敗も記録できる
from paths import append_log_entry, find_bundled_file, get_log_file_path

try:
    from PyQt6.QtCore import QThread
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    import dialogs
except Exception:
    # Qtの共有ライブラリの欠落など、例外フックを入れる前の読み込みで失敗した場合も、
    # コンソールの無いGUIビルドでは何も表示されずに終了してしまうため、crash.logに残す
    append_log_entry(f"起動時の読み込みに失敗\n{traceback.format_exc()}")
    raise

APP_ICON_PATH = os.path.join("downloader-icon", "app-icon-1024.png")


def install_exception_hook() -> None:
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
        # PyInstallerのwindowedビルド(console=False)ではsys.stderrがNoneになるため、
        # ここで例外を出してログ記録やダイアログ表示まで到達しない事態を避ける
        if sys.stderr is not None:
            sys.stderr.write(message)

        # 書き込みに失敗してもアプリは継続させるが、案内の文言は実際の結果に合わせる。
        # 残っていないログの場所を案内すると、調査の際に誤った手がかりを与えてしまう
        logged = append_log_entry(f"未処理の例外\n{message}", log_path)

        app = QApplication.instance()
        # QThread.run()内など、GUIスレッド以外で起きた例外もここに来る。GUIスレッド以外から
        # ダイアログを出すとQtがクラッシュするため、その場合はログへの記録だけにする
        if app is not None and QThread.currentThread() == app.thread():
            detail = (
                f"詳細はログに記録しました:\n{log_path}"
                if logged
                else f"ログファイルへの記録には失敗しました:\n{log_path}"
            )
            dialogs.critical(
                None,
                "予期しないエラー",
                "予期しないエラーが発生しました。動作が不安定な場合はアプリを再起動してください。\n\n"
                f"{detail}\n\n{exc_type.__name__}: {exc_value}",
            )

    sys.excepthook = handle_exception


def main() -> None:
    install_exception_hook()
    # 画面関連のモジュールは例外フックを入れた後に読み込み、その失敗も記録・表示されるようにする
    from main_window import MainWindow

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
        message = traceback.format_exc()
        append_log_entry(f"起動に失敗\n{message}")
        # コンソールの無いGUIビルドではsys.stderrがNoneになる
        if sys.stderr is not None:
            sys.stderr.write(message)
        sys.exit(1)
