"""widgets.py の RangeSlider に対する単体テスト(値域の計算・クランプ処理のみ)。

実際のマウスドラッグ描画はQtのイベントループ・ウィンドウ表示を要するため対象外とし、
座標<->値の変換や範囲外入力のクランプなど、座標系に依存しない値の扱いだけを検証する。
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

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


if __name__ == "__main__":
    unittest.main()
