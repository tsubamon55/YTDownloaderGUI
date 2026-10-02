"""文字列をHTMLとして解釈させずに表示するメッセージボックス。

QMessageBox.critical等の静的メソッドは本文の書式をQt.TextFormat.AutoTextで判定するため、
動画タイトル・エラー文・フォーマットIDのような外部由来の文字列に"<b>"等のタグらしき部分が
含まれると、HTMLとして描画されて文面が崩れたり、リンクやリモート画像の読み込みを含む
任意の装飾を差し込まれたりする。ここでは同じ呼び出し方のまま、常にプレーンテキストで表示する。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget

StandardButton = QMessageBox.StandardButton


def _show(
    icon: QMessageBox.Icon,
    parent: QWidget | None,
    title: str,
    text: str,
    buttons: StandardButton = StandardButton.Ok,
    default_button: StandardButton = StandardButton.NoButton,
) -> StandardButton:
    box = QMessageBox(icon, title, text, buttons, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    if default_button != StandardButton.NoButton:
        box.setDefaultButton(default_button)
    box.exec()
    clicked = box.clickedButton()
    return box.standardButton(clicked) if clicked is not None else StandardButton.NoButton


def critical(parent: QWidget | None, title: str, text: str) -> StandardButton:
    return _show(QMessageBox.Icon.Critical, parent, title, text)


def warning(parent: QWidget | None, title: str, text: str) -> StandardButton:
    return _show(QMessageBox.Icon.Warning, parent, title, text)


def information(parent: QWidget | None, title: str, text: str) -> StandardButton:
    return _show(QMessageBox.Icon.Information, parent, title, text)


def question(
    parent: QWidget | None,
    title: str,
    text: str,
    buttons: StandardButton = StandardButton.Yes | StandardButton.No,
    default_button: StandardButton = StandardButton.NoButton,
) -> StandardButton:
    return _show(QMessageBox.Icon.Question, parent, title, text, buttons, default_button)
