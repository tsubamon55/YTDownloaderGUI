"""live.py(ライブ録画に関するQt・yt-dlpに依存しない計算)の単体テスト"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from live import (
    CatchUpTracker,
    catch_up_text,
    recording_text,
    strip_live_title_suffix,
)


class StripLiveTitleSuffixTest(unittest.TestCase):
    def test_removes_date_time_appended_by_yt_dlp(self):
        self.assertEqual(strip_live_title_suffix("【逆凸】加入2日目！ 2026-10-02 19:06"), "【逆凸】加入2日目！")

    def test_keeps_title_without_suffix(self):
        self.assertEqual(strip_live_title_suffix("Morning Stream"), "Morning Stream")

    def test_keeps_date_in_the_middle_of_title(self):
        self.assertEqual(strip_live_title_suffix("2026-10-02 19:06 振り返り"), "2026-10-02 19:06 振り返り")


class ProgressTextTest(unittest.TestCase):
    def test_catch_up_text_rounds_percent(self):
        self.assertEqual(catch_up_text(37.4), "さかのぼり取得中 37%")

    def test_recording_text_with_size(self):
        self.assertEqual(recording_text(754, 345 * 1024 * 1024), "録画中 12:34 · 345.0MB")

    def test_recording_text_over_an_hour(self):
        self.assertEqual(recording_text(3600 + 59 * 60 + 30, None), "録画中 1:59:30")

    def test_recording_text_omits_unknown_size(self):
        self.assertEqual(recording_text(5, 0), "録画中 0:05")


class CatchUpTrackerTest(unittest.TestCase):
    def test_reports_zero_until_every_component_has_reported(self):
        tracker = CatchUpTracker(component_count=2)
        self.assertEqual(tracker.update("140", 3600, 3700), 0.0)

    def test_percent_follows_the_slowest_component(self):
        tracker = CatchUpTracker(component_count=2)
        tracker.update("140", 3000, 4000)
        self.assertAlmostEqual(tracker.update("298", 1000, 4000), 25.0)

    def test_caught_up_when_all_components_are_near_the_live_edge(self):
        tracker = CatchUpTracker(component_count=2)
        tracker.update("140", 3998, 4000)
        self.assertIsNone(tracker.update("298", 3997, 4000))
        self.assertTrue(tracker.caught_up)

    def test_one_fast_component_alone_is_not_caught_up(self):
        """音声は映像より速く追いつくため、片方だけで判定しない"""
        tracker = CatchUpTracker(component_count=2)
        tracker.update("298", 100, 4000)
        self.assertIsNotNone(tracker.update("140", 4000, 4000))
        self.assertFalse(tracker.caught_up)

    def test_stays_caught_up_even_if_count_grows(self):
        """追いついた後に配信が進んで区切りの総数が増えても、さかのぼり表示へ戻らない"""
        tracker = CatchUpTracker(component_count=1)
        tracker.update("96", 4000, 4000)
        self.assertIsNone(tracker.update("96", 4001, 4010))

    def test_missing_fragment_info_keeps_previous_percent(self):
        tracker = CatchUpTracker(component_count=1)
        tracker.update("96", 1000, 4000)
        self.assertAlmostEqual(tracker.update("96", None, None), 25.0)


if __name__ == "__main__":
    unittest.main()
