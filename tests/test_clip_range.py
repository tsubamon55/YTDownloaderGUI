"""clip_range.py のダウンロード範囲(開始・終了時刻)解析・検証ロジックの単体テスト"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from clip_range import clip_range_label, parse_clip_time, resolve_clip_range


class ParseClipTimeTest(unittest.TestCase):
    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_clip_time(""))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(parse_clip_time("   "))

    def test_seconds_only(self):
        self.assertEqual(parse_clip_time("45"), 45.0)

    def test_minutes_and_seconds(self):
        self.assertEqual(parse_clip_time("1:23"), 83.0)

    def test_hours_minutes_seconds(self):
        self.assertEqual(parse_clip_time("1:02:03"), 3723.0)

    def test_fractional_seconds(self):
        self.assertEqual(parse_clip_time("1:02.5"), 62.5)

    def test_too_many_segments_raises(self):
        with self.assertRaises(ValueError):
            parse_clip_time("1:02:03:04")

    def test_non_numeric_raises(self):
        with self.assertRaises(ValueError):
            parse_clip_time("abc")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            parse_clip_time("-5")


class ResolveClipRangeTest(unittest.TestCase):
    def test_both_empty_returns_none_none(self):
        self.assertEqual(resolve_clip_range("", ""), (None, None))

    def test_start_only(self):
        self.assertEqual(resolve_clip_range("1:00", ""), (60.0, None))

    def test_end_only(self):
        self.assertEqual(resolve_clip_range("", "2:00"), (None, 120.0))

    def test_start_before_end_is_valid(self):
        self.assertEqual(resolve_clip_range("1:00", "2:00"), (60.0, 120.0))

    def test_end_equal_to_start_raises(self):
        with self.assertRaises(ValueError):
            resolve_clip_range("1:00", "1:00")

    def test_end_before_start_raises(self):
        with self.assertRaises(ValueError):
            resolve_clip_range("2:00", "1:00")

    def test_invalid_start_propagates(self):
        with self.assertRaises(ValueError):
            resolve_clip_range("abc", "1:00")


class ClipRangeLabelTest(unittest.TestCase):
    def test_both_none_returns_none(self):
        self.assertIsNone(clip_range_label(None, None))

    def test_both_specified(self):
        self.assertEqual(clip_range_label(60.0, 120.0), "1:00-2:00")

    def test_missing_start_leaves_start_side_empty(self):
        self.assertEqual(clip_range_label(None, 120.0), "-2:00")

    def test_missing_end_leaves_end_side_empty(self):
        self.assertEqual(clip_range_label(60.0, None), "1:00-")


if __name__ == "__main__":
    unittest.main()
