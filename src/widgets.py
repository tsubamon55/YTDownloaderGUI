"""カスタムQWidget/QComboBox/QStyledItemDelegate"""

from collections.abc import Iterable
from typing import Literal

from PyQt6.QtCore import QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFocusEvent, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
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
from theme import (
    ACCENT_COLOR,
    DISABLED_CONTROL_RGB,
    NEUTRAL_RGB,
    POPUP_BACKGROUND_COLOR,
    POPUP_IMAGE_BACKGROUND,
    POPUP_TEXT_COLOR,
)

# RangeSliderの2つのハンドル(開始側/終了側)
HandleName = Literal["low", "high"]


def _draw_columns(painter: QPainter, x: int, y: int, height: int, texts: Iterable[str]) -> None:
    """フォーマット一覧の列幅(FORMAT_COLUMN_WIDTHS)に沿って、左から順にテキストを描く"""
    # 列幅より多いテキストは描かない(見出し・各行とも列数は列幅の数と一致する前提)
    for text, width in zip(texts, FORMAT_COLUMN_WIDTHS, strict=False):
        painter.drawText(QRect(x, y, width, height), int(Qt.AlignmentFlag.AlignVCenter), text)
        x += width


class FormatItemDelegate(QStyledItemDelegate):
    def paint(
        self, painter: QPainter | None, option: QStyleOptionViewItem | None, index: QModelIndex
    ) -> None:
        assert painter is not None and option is not None
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            painter.setPen(option.palette.highlightedText().color())
        else:
            if index.data(FORMAT_MISMATCH_ROLE):
                # コンテナ/コーデック不一致の非推奨フォーマットだと分かるよう背景をグレーにする
                painter.fillRect(option.rect, QColor(*NEUTRAL_RGB, 60))
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

        _draw_columns(painter, option.rect.x() + 4, option.rect.y(), option.rect.height(), columns)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        size.setHeight(FORMAT_ROW_HEIGHT)
        size.setWidth(sum(FORMAT_COLUMN_WIDTHS))
        return size


class FormatComboBox(QComboBox):
    """ドロップダウンの幅を選択ボックス自身の幅に一致させ、選択後の表示も列位置を揃えるQComboBox"""

    def showPopup(self) -> None:
        view = self.view()
        assert view is not None
        view.setMinimumWidth(self.width())
        super().showPopup()

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QStylePainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))

        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)

        columns = self.itemData(self.currentIndex(), FORMAT_COLUMN_ROLE)
        if not columns:
            painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)
            return

        style = self.style()
        assert style is not None
        field_rect = style.subControlRect(
            QStyle.ComplexControl.CC_ComboBox, opt, QStyle.SubControl.SC_ComboBoxEditField, self
        )
        _draw_columns(painter, field_rect.x() + 2, field_rect.y(), field_rect.height(), columns)


class SpinnerWidget(QWidget):
    """処理がフリーズしていないことを示す回転インジケータ"""

    def __init__(self, parent: QWidget | None = None, diameter: int = 18):
        super().__init__(parent)
        self._diameter = diameter
        self._angle = 0
        self.setFixedSize(diameter, diameter)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._advance)
        self.setVisible(False)

    def start(self) -> None:
        self._angle = 0
        self.setVisible(True)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self._diameter / 2, self._diameter / 2)
        painter.rotate(self._angle)
        pen = QPen(QColor(ACCENT_COLOR))
        pen.setWidth(3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        radius = self._diameter / 2 - 2
        rect = QRect(int(-radius), int(-radius), int(radius * 2), int(radius * 2))
        painter.drawArc(rect, 0, 270 * 16)
        painter.end()


class FormatHeaderWidget(QWidget):
    """フォーマット一覧の各列が何を示すかを示す見出し行"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(18)

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QPainter(self)
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        painter.setFont(font)
        painter.setPen(self.palette().color(self.foregroundRole()).lighter(160))

        _draw_columns(painter, 4, 0, self.height(), FORMAT_COLUMN_LABELS)
        painter.end()


class ScrubPreviewPopup(QWidget):
    """RangeSliderのドラッグ中、現在時刻とサムネイル(取得できた場合)を
    ハンドルの上に追従表示する小さなポップアップ。サムネイルが無い間は
    プレースホルダーの灰色枠のみ表示する"""

    # storyboard.select_storyboard_formatの最小解像度指定にもこの値を使い、
    # 画質が足りるストーリーボード階層(YouTubeの場合は概ね160x90)を選ばせる
    PREVIEW_SIZE = QSize(160, 90)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(f"background-color: {POPUP_BACKGROUND_COLOR}; border-radius: 4px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setFixedSize(self.PREVIEW_SIZE)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet(f"background-color: {POPUP_IMAGE_BACKGROUND}; border-radius: 2px;")
        layout.addWidget(self._image_label)

        self._time_label = QLabel()
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_label.setStyleSheet(f"color: {POPUP_TEXT_COLOR}; font-weight: bold;")
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

    キーボードでも操作できる(矢印キーで1、PageUp/PageDownで10ずつ動かし、Home/Endで端へ。
    Tab/Shift+Tabで開始・終了のハンドルを切り替える)。
    """

    rangeChanged = pyqtSignal(int, int)
    previewRequested = pyqtSignal(str, int)

    _HANDLE_RADIUS = 7
    _GROOVE_HEIGHT = 4
    _PAGE_STEP = 10

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 100
        self._low = 0
        self._high = 100
        self._active_handle: HandleName | None = None
        # キーボード操作の対象。最後にマウスで掴んだハンドルに合わせる
        self._keyboard_handle: HandleName = "low"
        self._preview = ScrubPreviewPopup(self)
        self.setFixedHeight(24)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @property
    def active_handle(self) -> HandleName | None:
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

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if not self.isEnabled() or event is None:
            return
        # 右クリック(コンテキストメニューのつもり)や中クリックで値が動かないよう、左ボタンだけで操作する
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        x = event.position().x()
        low_x, high_x = self._value_to_x(self._low), self._value_to_x(self._high)
        self._active_handle = "low" if abs(x - low_x) <= abs(x - high_x) else "high"
        self._drag_to(x)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if self._active_handle is None or event is None:
            return
        self._drag_to(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and event.button() != Qt.MouseButton.LeftButton:
            return
        if self._active_handle is not None:
            self._keyboard_handle = self._active_handle
        self._active_handle = None
        self.update()
        self._preview.hide_popup()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is None or not self.isEnabled():
            return super().keyPressEvent(event)
        current = self._low if self._keyboard_handle == "low" else self._high
        targets = {
            Qt.Key.Key_Left: current - 1,
            Qt.Key.Key_Down: current - 1,
            Qt.Key.Key_Right: current + 1,
            Qt.Key.Key_Up: current + 1,
            Qt.Key.Key_PageDown: current - self._PAGE_STEP,
            Qt.Key.Key_PageUp: current + self._PAGE_STEP,
            Qt.Key.Key_Home: self._minimum,
            Qt.Key.Key_End: self._maximum,
        }
        target = targets.get(Qt.Key(event.key()))
        if target is None:
            return super().keyPressEvent(event)
        self._move_handle(self._keyboard_handle, target)
        event.accept()

    def focusNextPrevChild(self, next: bool) -> bool:
        """Tabで開始→終了、Shift+Tabで終了→開始のハンドルへ移り、端からさらに進むと
        通常どおり隣のウィジェットへフォーカスを移す"""
        if next and self._keyboard_handle == "low":
            self._keyboard_handle = "high"
        elif not next and self._keyboard_handle == "high":
            self._keyboard_handle = "low"
        else:
            return super().focusNextPrevChild(next)
        self.update()
        return True

    def focusInEvent(self, event: QFocusEvent | None) -> None:
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent | None) -> None:
        self.update()
        super().focusOutEvent(event)

    def _move_handle(self, which: HandleName, value: int) -> None:
        """whichのハンドルをvalueへ動かす(もう一方のハンドルは越えない)。変わればrangeChangedを出す"""
        value = min(max(value, self._minimum), self._maximum)
        if which == "low":
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

    def _drag_to(self, x: float) -> None:
        value = self._x_to_value(x)

        # 両ハンドルが同じ位置に重なっている場合、掴んだ側と逆向きへ動かそうとしても
        # クランプ(low は high を超えられない / high は low を下回れない)に阻まれ、
        # 一点に潰れた範囲を広げられなくなる。重なっている間は「動かした向き」を
        # 意図とみなし、その向きへ動けるハンドルへ掴み直す
        if self._low == self._high:
            if self._active_handle == "low" and value > self._low:
                self._active_handle = "high"
            elif self._active_handle == "high" and value < self._high:
                self._active_handle = "low"

        handle: HandleName = "low" if self._active_handle == "low" else "high"
        self._move_handle(handle, value)
        value = self._low if handle == "low" else self._high

        # ハンドルが近接していると、どちら側を動かしているのか見た目だけでは
        # 分かりにくいため、ドラッグ中は現在値と(取得できれば)サムネイルを追従表示する
        handle_x = self._value_to_x(value)
        self._preview.set_time_text(format_clip_time(value))
        self._preview.move_above(self.mapToGlobal(QPoint(round(handle_x), 0)))
        self.previewRequested.emit(self._active_handle, value)

    def set_preview_pixmap(self, which: HandleName, pixmap: QPixmap) -> None:
        """previewRequestedを受けて呼び出し側が取得したサムネイルを反映する。
        既に別のハンドルの操作に移っている/ドラッグが終わっている場合は無視する"""
        if which != self._active_handle:
            return
        self._preview.set_pixmap(pixmap)

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid_y = self.height() / 2
        groove_rect = QRectF(
            self._HANDLE_RADIUS, mid_y - self._GROOVE_HEIGHT / 2,
            max(self.width() - 2 * self._HANDLE_RADIUS, 0), self._GROOVE_HEIGHT,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*NEUTRAL_RGB, 90))
        painter.drawRoundedRect(groove_rect, 2, 2)

        # disabled中(URL未入力等)は有効時と同じ見た目にならないよう、
        # 選択バー・ハンドルをグレーにする(Qt標準ウィジェットと違い自前描画のため
        # isEnabled()を明示的に見ないと自動でグレーアウトされない)
        accent_color = QColor(ACCENT_COLOR) if self.isEnabled() else QColor(*DISABLED_CONTROL_RGB)

        low_x, high_x = self._value_to_x(self._low), self._value_to_x(self._high)
        selected_rect = QRectF(low_x, mid_y - self._GROOVE_HEIGHT / 2, high_x - low_x, self._GROOVE_HEIGHT)
        painter.setBrush(accent_color)
        painter.drawRoundedRect(selected_rect, 2, 2)

        painter.setPen(QPen(QColor(POPUP_TEXT_COLOR), 1))
        for which, x in (("low", low_x), ("high", high_x)):
            # ドラッグ中のハンドルだけ一回り大きく描き、2つのハンドルが近接していても
            # どちら(開始/終了)を操作しているか見た目で分かるようにする
            # キーボード操作中(フォーカスあり)は操作対象のハンドルも同様に大きく描いて示す
            emphasized = which == self._active_handle or (self.hasFocus() and which == self._keyboard_handle)
            radius = self._HANDLE_RADIUS + 2 if emphasized else self._HANDLE_RADIUS
            painter.drawEllipse(QPointF(x, mid_y), radius, radius)
        painter.end()
