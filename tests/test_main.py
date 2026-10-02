"""main.py の install_exception_hook に対する単体テスト(GUI起動不要)"""

import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtCore import QThread

import main


def fake_app():
    """このスレッド(テストを実行しているスレッド)をGUIスレッドとする、起動済みQApplicationの代わり"""
    gui_thread = QThread.currentThread()
    return SimpleNamespace(thread=lambda: gui_thread)


class InstallExceptionHookTest(unittest.TestCase):
    def setUp(self):
        self._original_excepthook = sys.excepthook
        self.addCleanup(setattr, sys, "excepthook", self._original_excepthook)

    def test_keyboard_interrupt_delegates_to_default_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "crash.log")
            with patch.object(main, "get_log_file_path", return_value=log_path):
                main.install_exception_hook()

            with patch("sys.__excepthook__") as default_hook:
                try:
                    raise KeyboardInterrupt()
                except KeyboardInterrupt:
                    sys.excepthook(*sys.exc_info())
                default_hook.assert_called_once()
            self.assertFalse(os.path.exists(log_path))

    def test_other_exception_is_logged_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "nested", "crash.log")
            with patch.object(main, "get_log_file_path", return_value=log_path):
                main.install_exception_hook()

            with patch.object(main.QApplication, "instance", return_value=None):
                try:
                    raise ValueError("boom")
                except ValueError:
                    sys.excepthook(*sys.exc_info())

            self.assertTrue(os.path.isfile(log_path))
            with open(log_path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("ValueError", content)
            self.assertIn("boom", content)

    def test_shows_dialog_only_when_qapplication_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "crash.log")
            with patch.object(main, "get_log_file_path", return_value=log_path):
                main.install_exception_hook()

            with patch.object(main.QApplication, "instance", return_value=fake_app()), \
                 patch.object(main.dialogs, "critical") as critical_mock:
                try:
                    raise RuntimeError("oops")
                except RuntimeError:
                    sys.excepthook(*sys.exc_info())
                critical_mock.assert_called_once()

    def test_logs_and_shows_dialog_when_stderr_is_none(self):
        """PyInstallerのwindowedビルド(console=False)ではsys.stderrがNoneになる。
        その環境でもログ記録とダイアログ表示まで到達しなければならない"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "crash.log")
            with patch.object(main, "get_log_file_path", return_value=log_path):
                main.install_exception_hook()

            with (
                patch.object(sys, "stderr", None),
                patch.object(main.QApplication, "instance", return_value=fake_app()),
                patch.object(main.dialogs, "critical") as critical_mock,
            ):
                try:
                    raise ValueError("boom")
                except ValueError:
                    sys.excepthook(*sys.exc_info())

            self.assertTrue(os.path.isfile(log_path))
            critical_mock.assert_called_once()

    def test_logging_failure_does_not_raise(self):
        # ログ用ディレクトリが作成できない(親がファイルである)場合でも例外を投げない
        with tempfile.TemporaryDirectory() as tmp:
            blocking_file = os.path.join(tmp, "blocked")
            open(blocking_file, "w").close()
            log_path = os.path.join(blocking_file, "crash.log")
            with patch.object(main, "get_log_file_path", return_value=log_path):
                main.install_exception_hook()

            with patch.object(main.QApplication, "instance", return_value=None):
                try:
                    raise ValueError("boom")
                except ValueError:
                    sys.excepthook(*sys.exc_info())  # 例外が伝播しなければOK

    def test_dialog_says_logging_failed_when_log_cannot_be_written(self):
        """ログが残っていないのに「記録しました」と案内すると、ユーザーがそのパスを
        開いても何もなく、調査の際に誤った手がかりを与えてしまう"""
        with tempfile.TemporaryDirectory() as tmp:
            blocking_file = os.path.join(tmp, "blocked")
            open(blocking_file, "w").close()
            log_path = os.path.join(blocking_file, "crash.log")
            with patch.object(main, "get_log_file_path", return_value=log_path):
                main.install_exception_hook()

            with (
                patch.object(main.QApplication, "instance", return_value=fake_app()),
                patch.object(main.dialogs, "critical") as critical_mock,
            ):
                try:
                    raise ValueError("boom")
                except ValueError:
                    sys.excepthook(*sys.exc_info())

            body = critical_mock.call_args[0][2]
            self.assertIn("記録には失敗しました", body)
            self.assertNotIn("詳細はログに記録しました", body)

    def test_dialog_points_to_log_when_write_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "crash.log")
            with patch.object(main, "get_log_file_path", return_value=log_path):
                main.install_exception_hook()

            with (
                patch.object(main.QApplication, "instance", return_value=fake_app()),
                patch.object(main.dialogs, "critical") as critical_mock,
            ):
                try:
                    raise ValueError("boom")
                except ValueError:
                    sys.excepthook(*sys.exc_info())

            body = critical_mock.call_args[0][2]
            self.assertIn("詳細はログに記録しました", body)
            self.assertIn(log_path, body)

    def test_exception_in_worker_thread_is_logged_without_dialog(self):
        """QThread.run()内の例外もここに来る。GUIスレッド以外からダイアログを出すとQtが落ちる"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "crash.log")
            with patch.object(main, "get_log_file_path", return_value=log_path):
                main.install_exception_hook()

            def raise_in_thread():
                try:
                    raise ValueError("from worker")
                except ValueError:
                    sys.excepthook(*sys.exc_info())

            with (
                patch.object(main.QApplication, "instance", return_value=fake_app()),
                patch.object(main.dialogs, "critical") as critical_mock,
            ):
                thread = threading.Thread(target=raise_in_thread)
                thread.start()
                thread.join()

            critical_mock.assert_not_called()
            with open(log_path, encoding="utf-8") as f:
                self.assertIn("from worker", f.read())


if __name__ == "__main__":
    unittest.main()
