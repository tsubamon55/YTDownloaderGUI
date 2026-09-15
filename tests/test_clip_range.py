"""clip_range.py のダウンロード範囲(開始・終了時刻)解析・検証ロジックの単体テスト"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from clip_range import (
    auto_format_clip_input,
    clip_range_label,
    format_clip_digits,
    parse_clip_time,
    resolve_clip_range,
)


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

    def test_end_zero_with_blank_start_raises(self):
        """開始欄が空欄(=先頭から)の場合、終了時刻は0との比較になる。
        "0"を指定すると長さ0の無意味なクリップになってしまうためエラーとする"""
        with self.assertRaises(ValueError):
            resolve_clip_range("", "0")

    def test_end_after_zero_with_blank_start_is_valid(self):
        self.assertEqual(resolve_clip_range("", "0:01"), (None, 1.0))


class ClipRangeLabelTest(unittest.TestCase):
    def test_both_none_returns_none(self):
        self.assertIsNone(clip_range_label(None, None))

    def test_both_specified(self):
        self.assertEqual(clip_range_label(60.0, 120.0), "1:00-2:00")

    def test_missing_start_leaves_start_side_empty(self):
        self.assertEqual(clip_range_label(None, 120.0), "-2:00")

    def test_missing_end_leaves_end_side_empty(self):
        self.assertEqual(clip_range_label(60.0, None), "1:00-")


class FormatClipDigitsTest(unittest.TestCase):
    def test_empty_stays_empty(self):
        self.assertEqual(format_clip_digits(""), "")

    def test_one_or_two_digits_stay_as_seconds_only(self):
        self.assertEqual(format_clip_digits("5"), "5")
        self.assertEqual(format_clip_digits("45"), "45")

    def test_three_digits_becomes_minutes_seconds(self):
        # "1"分"30"秒 (1桁の分)
        self.assertEqual(format_clip_digits("130"), "1:30")

    def test_four_digits_becomes_minutes_seconds(self):
        self.assertEqual(format_clip_digits("1230"), "12:30")

    def test_five_digits_becomes_hours_minutes_seconds(self):
        self.assertEqual(format_clip_digits("10203"), "1:02:03")

    def test_six_digits_becomes_hours_minutes_seconds(self):
        self.assertEqual(format_clip_digits("120304"), "12:03:04")

    def test_seventh_digit_drops_the_oldest_one(self):
        # 7桁目を打つと、最も古い(先頭の)桁があふれて消える
        self.assertEqual(format_clip_digits("1203045"), "20:30:45")


class AutoFormatClipInputTest(unittest.TestCase):
    def test_typing_digits_in_order_fills_from_the_right(self):
        # "1" "3" "0" と1桁ずつ打っていくと、末尾(秒側)に積み上がっていき
        # 最終的に "1:30" (1分30秒) になる。左詰めの2桁区切りだと "13:0" に
        # なってしまい意味が変わるため、右詰め方式にしている
        self.assertEqual(auto_format_clip_input("1"), "1")
        self.assertEqual(auto_format_clip_input("13"), "13")
        self.assertEqual(auto_format_clip_input("130"), "1:30")

    def test_already_formatted_text_is_idempotent(self):
        self.assertEqual(auto_format_clip_input("1:02:03"), "1:02:03")

    def test_backspace_shifts_remaining_digits_back(self):
        # "12:03:04" から末尾の数字を1つ消すと、桁が繰り上がる前の状態
        # ("1:20:30" = "120304"から末尾の"4"を除いた"12030"の右詰め表示) に戻る
        self.assertEqual(auto_format_clip_input("12:03:0"), "1:20:30")

    def test_fractional_seconds_input_is_left_alone(self):
        # "."など数字・コロン以外を含む場合は、端数秒などの手入力を尊重して何もしない
        self.assertEqual(auto_format_clip_input("1:02.5"), "1:02.5")

    def test_empty_stays_empty(self):
        self.assertEqual(auto_format_clip_input(""), "")


if __name__ == "__main__":
    unittest.main()
