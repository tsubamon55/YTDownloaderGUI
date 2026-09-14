"""カスタムQWidget/QComboBox/QStyledItemDelegate"""

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from clip_range import format_clip_time
from formats import (
    FORMAT_COLUMN_LABELS,
    FORMAT_COLUMN_ROLE,
    FORMAT_COLUMN_WIDTHS,
    FORMAT_MISMATCH_ROLE,
    FORMAT_ROW_HEIGHT,
)


class FormatItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            painter.setPen(option.palette.highlightedText().color())
        else:
            if index.data(FORMAT_MISMATCH_ROLE):
                # コンテナ/コーデック不一致の非推奨フォーマットだと分かるよう背景をグレーにする
                painter.fillRect(option.rect, QColor(128, 128, 128, 60))
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


class ScrubPreviewPopup(QWidget):
    """RangeSliderのドラッグ中、現在時刻とサムネイル(取得できた場合)を
    ハンドルの上に追従表示する小さなポップアップ。サムネイルが無い間は
    プレースホルダーの灰色枠のみ表示する"""

    # storyboard.select_storyboard_formatの最小解像度指定にもこの値を使い、
    # 画質が足りるストーリーボード階層(YouTubeの場合は概ね160x90)を選ばせる
    PREVIEW_SIZE = QSize(160, 90)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background-color: #202124; border-radius: 4px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setFixedSize(self.PREVIEW_SIZE)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: rgba(255, 255, 255, 30); border-radius: 2px;")
        layout.addWidget(self._image_label)

        self._time_label = QLabel()
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_label.setStyleSheet("color: #ffffff; font-weight: bold;")
        layout.addWidget(self._time_label)

    def set_time_text(self, text: str) -> None:
        self._time_label.setText(text)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self._image_label.clear()
            return
        scaled = pixmap.scaled(
            self.PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self._image_label.setPixmap(scaled)

    def move_above(self, global_pos: QPoint) -> None:
        self.adjustSize()
        self.move(global_pos.x() - self.width() // 2, global_pos.y() - self.height() - 12)
        if not self.isVisible():
            self.show()

    def hide_popup(self) -> None:
        self._image_label.clear()
        self.hide()


class RangeSlider(QWidget):
    """開始・終了の2つのハンドルをドラッグして範囲(int)を指定するスライダー。

    ドラッグでの変更時のみ rangeChanged を発行する。setRange/setValues による
    プログラムからの変更ではシグナルを発行しないため、テキスト入力欄との
    双方向同期を行っても無限ループにならない。
    ドラッグ中はpreviewRequestedを発行するので、呼び出し側はそれを使って
    サムネイル等を取得し、set_preview_pixmapで返せば追従ポップアップに表示される。
    """

    rangeChanged = pyqtSignal(int, int)
    previewRequested = pyqtSignal(str, int)

    _HANDLE_RADIUS = 7
    _GROOVE_HEIGHT = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 100
        self._low = 0
        self._high = 100
        self._active_handle: str | None = None
        self._preview = ScrubPreviewPopup(self)
        self.setFixedHeight(24)

    @property
    def active_handle(self) -> str | None:
        return self._active_handle

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = minimum
        self._maximum = max(maximum, minimum + 1)
        self._low = min(max(self._low, self._minimum), self._maximum)
        self._high = min(max(self._high, self._minimum), self._maximum)
        if self._low > self._high:
            self._low, self._high = self._high, self._low
        self.update()

    def setValues(self, low: int, high: int) -> None:
        low = min(max(low, self._minimum), self._maximum)
        high = min(max(high, self._minimum), self._maximum)
        if low > high:
            low, high = high, low
        self._low, self._high = low, high
        self.update()

    def values(self) -> tuple[int, int]:
        return self._low, self._high

    def _value_to_x(self, value: int) -> float:
        usable_width = self.width() - 2 * self._HANDLE_RADIUS
        if usable_width <= 0 or self._maximum == self._minimum:
            return self._HANDLE_RADIUS
        ratio = (value - self._minimum) / (self._maximum - self._minimum)
        return self._HANDLE_RADIUS + ratio * usable_width

    def _x_to_value(self, x: float) -> int:
        usable_width = self.width() - 2 * self._HANDLE_RADIUS
        if usable_width <= 0:
            return self._minimum
        ratio = (x - self._HANDLE_RADIUS) / usable_width
        ratio = min(max(ratio, 0.0), 1.0)
        return round(self._minimum + ratio * (self._maximum - self._minimum))

    def mousePressEvent(self, event):
        if not self.isEnabled():
            return
        x = event.position().x()
        low_x, high_x = self._value_to_x(self._low), self._value_to_x(self._high)
        self._active_handle = "low" if abs(x - low_x) <= abs(x - high_x) else "high"
        self._drag_to(x)

    def mouseMoveEvent(self, event):
        if self._active_handle is None:
            return
        self._drag_to(event.position().x())

    def mouseReleaseEvent(self, event):
        self._active_handle = None
        self.update()
        self._preview.hide_popup()

    def _drag_to(self, x: float) -> None:
        value = self._x_to_value(x)
        if self._active_handle == "low":
            value = min(value, self._high)
            changed = value != self._low
            self._low = value
        else:
            value = max(value, self._low)
            changed = value != self._high
            self._high = value
        if changed:
            self.update()
            self.rangeChanged.emit(self._low, self._high)

        # ハンドルが近接していると、どちら側を動かしているのか見た目だけでは
        # 分かりにくいため、ドラッグ中は現在値と(取得できれば)サムネイルを追従表示する
        handle_x = self._value_to_x(value)
        self._preview.set_time_text(format_clip_time(value))
        self._preview.move_above(self.mapToGlobal(QPoint(round(handle_x), 0)))
        self.previewRequested.emit(self._active_handle, value)

    def set_preview_pixmap(self, which: str, pixmap: QPixmap) -> None:
        """previewRequestedを受けて呼び出し側が取得したサムネイルを反映する。
        既に別のハンドルの操作に移っている/ドラッグが終わっている場合は無視する"""
        if which != self._active_handle:
            return
        self._preview.set_pixmap(pixmap)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid_y = self.height() / 2
        groove_rect = QRectF(
            self._HANDLE_RADIUS, mid_y - self._GROOVE_HEIGHT / 2,
            max(self.width() - 2 * self._HANDLE_RADIUS, 0), self._GROOVE_HEIGHT,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(128, 128, 128, 90))
        painter.drawRoundedRect(groove_rect, 2, 2)

        low_x, high_x = self._value_to_x(self._low), self._value_to_x(self._high)
        selected_rect = QRectF(low_x, mid_y - self._GROOVE_HEIGHT / 2, high_x - low_x, self._GROOVE_HEIGHT)
        painter.setBrush(QColor("#1a73e8"))
        painter.drawRoundedRect(selected_rect, 2, 2)

        painter.setPen(QPen(QColor("#ffffff"), 1))
        for which, x in (("low", low_x), ("high", high_x)):
            # ドラッグ中のハンドルだけ一回り大きく描き、2つのハンドルが近接していても
            # どちら(開始/終了)を操作しているか見た目で分かるようにする
            radius = self._HANDLE_RADIUS + 2 if which == self._active_handle else self._HANDLE_RADIUS
            painter.drawEllipse(QPointF(x, mid_y), radius, radius)
        painter.end()
