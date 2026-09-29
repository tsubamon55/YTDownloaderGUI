"""widgets.py の RangeSlider に対する単体テスト。

主眼は座標<->値の変換や範囲外入力のクランプなど、座標系に依存しない値の扱いだが、
disabled時の見た目(グレーアウト)についてはgrab()で実際に描画した結果の画素色を
検証する(DisabledAppearanceTest)。それ以外の実際のマウスドラッグ描画は
Qtのイベントループ・ウィンドウ表示を要するため対象外とする。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

import widgets
from widgets import RangeSlider

_app = QApplication.instance() or QApplication(sys.argv)


class SetRangeTest(unittest.TestCase):
    def test_default_range_is_zero_to_hundred(self):
        slider = RangeSlider()
        self.assertEqual(slider.values(), (0, 100))

    def test_narrows_values_within_new_range(self):
        slider = RangeSlider()
        slider.setValues(10, 90)
        slider.setRange(0, 50)
        low, high = slider.values()
        self.assertLessEqual(low, 50)
        self.assertLessEqual(high, 50)

    def test_rejects_zero_width_range(self):
        slider = RangeSlider()
        slider.setRange(10, 10)
        # 内部でゼロ幅を避けるため、maximumはminimumより大きくなる
        slider.setValues(0, 100)
        low, high = slider.values()
        self.assertGreaterEqual(high, low)


class SetValuesTest(unittest.TestCase):
    def test_clamps_to_range(self):
        slider = RangeSlider()
        slider.setRange(0, 100)
        slider.setValues(-10, 200)
        self.assertEqual(slider.values(), (0, 100))

    def test_swaps_when_low_greater_than_high(self):
        slider = RangeSlider()
        slider.setRange(0, 100)
        slider.setValues(80, 20)
        self.assertEqual(slider.values(), (20, 80))

    def test_does_not_emit_range_changed(self):
        slider = RangeSlider()
        slider.setRange(0, 100)
        events = []
        slider.rangeChanged.connect(lambda low, high: events.append((low, high)))
        slider.setValues(10, 90)
        self.assertEqual(events, [])


class DragTest(unittest.TestCase):
    def test_drag_low_handle_emits_range_changed(self):
        slider = RangeSlider()
        slider.resize(200, 24)
        slider.setRange(0, 100)
        slider.setValues(0, 100)
        events = []
        slider.rangeChanged.connect(lambda low, high: events.append((low, high)))

        slider._active_handle = "low"
        slider._drag_to(100)  # 幅200の中央付近へドラッグ

        self.assertEqual(len(events), 1)
        low, high = events[0]
        self.assertGreater(low, 0)
        self.assertEqual(high, 100)

    def test_low_handle_cannot_cross_high_handle(self):
        slider = RangeSlider()
        slider.resize(200, 24)
        slider.setRange(0, 100)
        slider.setValues(0, 30)

        slider._active_handle = "low"
        slider._drag_to(190)  # high(30)を超える位置へドラッグしようとする

        low, high = slider.values()
        self.assertLessEqual(low, high)
        self.assertEqual(high, 30)


class OverlappingHandlesTest(unittest.TestCase):
    """開始・終了が同じ位置に重なった状態(テキスト欄に同じ時刻を入力した場合など)でも、
    範囲を左右どちらへも広げられること。掴んだ側と逆向きの操作はクランプに阻まれるため、
    重なっている間は動かした向きに応じてハンドルを掴み直す"""

    @staticmethod
    def _overlapped_slider(value: int):
        slider = RangeSlider()
        slider.resize(200, 24)
        slider.setRange(0, 100)
        slider.setValues(value, value)
        return slider

    def test_can_widen_to_the_right_from_overlapping_handles(self):
        slider = self._overlapped_slider(50)
        events = []
        slider.rangeChanged.connect(lambda low, high: events.append((low, high)))

        slider._active_handle = "low"
        slider._drag_to(160)  # 重なった位置(x=100)より右へドラッグ

        low, high = slider.values()
        self.assertEqual(low, 50)
        self.assertGreater(high, 50)
        self.assertEqual(len(events), 1)

    def test_can_widen_to_the_left_from_overlapping_handles(self):
        slider = self._overlapped_slider(50)

        slider._active_handle = "high"
        slider._drag_to(40)  # 重なった位置より左へドラッグ

        low, high = slider.values()
        self.assertLess(low, 50)
        self.assertEqual(high, 50)

    def test_separated_handles_are_not_swapped(self):
        """重なっていない通常の状態では掴み直しは起きず、従来どおりクランプされる"""
        slider = RangeSlider()
        slider.resize(200, 24)
        slider.setRange(0, 100)
        slider.setValues(20, 60)

        slider._active_handle = "low"
        slider._drag_to(190)  # high(60)を超える位置へ

        self.assertEqual(slider.values(), (60, 60))
        self.assertEqual(slider._active_handle, "low")


class DisabledAppearanceTest(unittest.TestCase):
    """disabled時(URL未入力等)は有効時と見分けられるよう選択バーがグレーアウトされ、
    通常時の青色のままにならないことを実際の描画結果(画素色)で検証する"""

    @staticmethod
    def _selected_bar_color(slider: RangeSlider):
        slider.resize(200, 24)
        slider.setRange(0, 100)
        slider.setValues(0, 100)
        image = slider.grab().toImage()
        return image.pixelColor(100, 12)  # ハンドルに被らない選択バー中央付近

    def test_disabled_slider_is_grayed_out(self):
        slider = RangeSlider()
        slider.setEnabled(True)
        enabled_color = self._selected_bar_color(slider)

        slider.setEnabled(False)
        disabled_color = self._selected_bar_color(slider)

        self.assertNotEqual(enabled_color.name(), disabled_color.name())
        # 無効時はグレー(R=G=B)であることも確認する
        self.assertEqual(disabled_color.red(), disabled_color.green())
        self.assertEqual(disabled_color.green(), disabled_color.blue())


class DragPreviewTest(unittest.TestCase):
    """ドラッグ中、どちらのハンドルを操作しているか分かるよう現在値・サムネイルを
    ポップアップで追従表示することを検証する(実際の描画はしない)"""

    def test_drag_emits_preview_requested_with_current_value(self):
        slider = RangeSlider()
        slider.resize(200, 24)
        slider.setRange(0, 120)
        slider.setValues(0, 120)
        events = []
        slider.previewRequested.connect(lambda which, value: events.append((which, value)))

        slider._active_handle = "low"
        slider._drag_to(100)  # 幅200・範囲0-120の中央(x=100)は60秒

        self.assertEqual(events, [("low", 60)])

    def test_drag_shows_popup_with_time_text(self):
        slider = RangeSlider()
        slider.resize(200, 24)
        slider.setRange(0, 120)
        slider.setValues(0, 120)

        slider._active_handle = "low"
        slider._drag_to(100)

        self.assertTrue(slider._preview.isVisible())
        self.assertEqual(slider._preview._time_label.text(), "1:00")

    def test_release_hides_popup(self):
        slider = RangeSlider()
        slider.resize(200, 24)
        slider._active_handle = "low"
        slider._drag_to(0)
        self.assertTrue(slider._preview.isVisible())

        slider.mouseReleaseEvent(None)

        self.assertFalse(slider._preview.isVisible())

    def test_set_preview_pixmap_applies_when_handle_still_active(self):
        slider = RangeSlider()
        slider._active_handle = "low"
        pixmap = QPixmap(10, 10)

        slider.set_preview_pixmap("low", pixmap)

        self.assertFalse(slider._preview._image_label.pixmap().isNull())

    def test_set_preview_pixmap_ignored_when_handle_changed(self):
        slider = RangeSlider()
        slider._active_handle = "high"
        pixmap = QPixmap(10, 10)

        slider.set_preview_pixmap("low", pixmap)

        self.assertTrue(slider._preview._image_label.pixmap().isNull())


class DrawColumnsTest(unittest.TestCase):
    def test_draws_each_text_at_accumulated_x(self):
        painter = MagicMock()
        widgets._draw_columns(painter, 4, 0, 20, ["a", "b"])
        rects = [call.args[0] for call in painter.drawText.call_args_list]
        self.assertEqual([r.x() for r in rects], [4, 4 + widgets.FORMAT_COLUMN_WIDTHS[0]])
        self.assertEqual([call.args[2] for call in painter.drawText.call_args_list], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
