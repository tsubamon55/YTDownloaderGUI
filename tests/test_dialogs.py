"""dialogs.py(本文をHTMLとして解釈させないメッセージボックス)の単体テスト"""

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

import dialogs

_app = QApplication.instance() or QApplication(sys.argv)


class DialogsTest(unittest.TestCase):
    def _capture(self, show, clicked=None):
        """exec()を止めて表示直前のQMessageBoxを捕まえ、(box, 戻り値)を返す"""
        captured = {}

        def fake_exec(box):
            captured["box"] = box
            return 0

        with patch.object(QMessageBox, "exec", fake_exec), \
             patch.object(QMessageBox, "clickedButton", lambda box: clicked(box) if clicked else None):
            result = show()
        return captured["box"], result

    def test_text_is_always_plain(self):
        """動画タイトルやエラー文の"<b>"等をHTMLとして描画しない"""
        text = "<b>太字</b><img src='https://example.com/x.png'>"
        for show in (dialogs.critical, dialogs.warning, dialogs.information, dialogs.question):
            with self.subTest(show=show.__name__):
                box, _ = self._capture(lambda show=show: show(None, "タイトル", text))
                self.assertEqual(box.textFormat(), Qt.TextFormat.PlainText)
                self.assertEqual(box.text(), text)

    def test_question_returns_clicked_standard_button(self):
        box, result = self._capture(
            lambda: dialogs.question(None, "t", "q", default_button=dialogs.StandardButton.No),
            clicked=lambda box: box.button(QMessageBox.StandardButton.Yes),
        )
        self.assertEqual(result, QMessageBox.StandardButton.Yes)
        self.assertIs(box.defaultButton(), box.button(QMessageBox.StandardButton.No))

    def test_closed_without_click_returns_no_button(self):
        _, result = self._capture(lambda: dialogs.critical(None, "t", "x"))
        self.assertEqual(result, QMessageBox.StandardButton.NoButton)


if __name__ == "__main__":
    unittest.main()
