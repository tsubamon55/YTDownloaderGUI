"""カスタムQWidget/QComboBox/QStyledItemDelegate"""

from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStylePainter,
    QWidget,
)

from formats import FORMAT_COLUMN_LABELS, FORMAT_COLUMN_ROLE, FORMAT_COLUMN_WIDTHS, FORMAT_ROW_HEIGHT


class FormatItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            painter.setPen(option.palette.highlightedText().color())
        else:
            painter.setPen(option.palette.text().color())

        columns = index.data(FORMAT_COLUMN_ROLE)
        if not columns:
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""
            painter.drawText(
                option.rect.adjusted(4, 0, 0, 0),
                int(Qt.AlignmentFlag.AlignVCenter),
                text,
            )
            painter.restore()
            return

        x = option.rect.x() + 4
        for text, width in zip(columns, FORMAT_COLUMN_WIDTHS):
            rect = QRect(x, option.rect.y(), width, option.rect.height())
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter), text)
            x += width

        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(FORMAT_ROW_HEIGHT)
        size.setWidth(sum(FORMAT_COLUMN_WIDTHS))
        return size


class FormatComboBox(QComboBox):
    """ドロップダウンの幅を選択ボックス自身の幅に一致させ、選択後の表示も列位置を揃えるQComboBox"""

    def showPopup(self):
        self.view().setMinimumWidth(self.width())
        super().showPopup()

    def paintEvent(self, event):
        painter = QStylePainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))

        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)

        columns = self.itemData(self.currentIndex(), FORMAT_COLUMN_ROLE)
        if not columns:
            painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)
            return

        field_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, opt, QStyle.SubControl.SC_ComboBoxEditField, self
        )
        x = field_rect.x() + 2
        for text, width in zip(columns, FORMAT_COLUMN_WIDTHS):
            rect = QRect(x, field_rect.y(), width, field_rect.height())
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter), text)
            x += width


class SpinnerWidget(QWidget):
    """処理がフリーズしていないことを示す回転インジケータ"""

    def __init__(self, parent=None, diameter: int = 18):
        super().__init__(parent)
        self._diameter = diameter
        self._angle = 0
        self.setFixedSize(diameter, diameter)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._advance)
        self.setVisible(False)

    def start(self):
        self._angle = 0
        self.setVisible(True)
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.setVisible(False)

    def _advance(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self._diameter / 2, self._diameter / 2)
        painter.rotate(self._angle)
        pen = QPen(QColor("#1a73e8"))
        pen.setWidth(3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        radius = self._diameter / 2 - 2
        rect = QRect(int(-radius), int(-radius), int(radius * 2), int(radius * 2))
        painter.drawArc(rect, 0, 270 * 16)
        painter.end()


class FormatHeaderWidget(QWidget):
    """フォーマット一覧の各列が何を示すかを示す見出し行"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)

    def paintEvent(self, event):
        painter = QPainter(self)
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        painter.setFont(font)
        painter.setPen(self.palette().color(self.foregroundRole()).lighter(160))

        x = 4
        for label, width in zip(FORMAT_COLUMN_LABELS, FORMAT_COLUMN_WIDTHS):
            rect = QRect(x, 0, width, self.height())
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter), label)
            x += width
        painter.end()
