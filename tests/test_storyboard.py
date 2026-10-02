"""storyboard.py のストーリーボード選択・時刻→マス位置変換ロジックの単体テスト"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storyboard import select_storyboard_format, storyboard_tile_for_time


def make_storyboard(format_id, width, height, rows, columns, fps, num_fragments):
    return {
        "format_id": format_id,
        "format_note": "storyboard",
        "vcodec": "none",
        "acodec": "none",
        "width": width,
        "height": height,
        "rows": rows,
        "columns": columns,
        "fps": fps,
        "fragments": [{"url": f"https://example.com/{format_id}/M{i}.jpg"} for i in range(num_fragments)],
    }


class SelectStoryboardFormatTest(unittest.TestCase):
    def test_returns_none_when_no_storyboards(self):
        formats = [{"format_id": "137", "vcodec": "avc1", "acodec": "none"}]
        self.assertIsNone(select_storyboard_format(formats))

    def test_ignores_storyboard_without_fragments(self):
        broken = make_storyboard("sb0", 320, 180, 3, 3, 0.2, 1)
        broken["fragments"] = []
        self.assertIsNone(select_storyboard_format([broken]))

    def test_picks_smallest_tile_area_when_no_minimum_given(self):
        coarse = make_storyboard("sb3", 48, 27, 10, 10, 0.15, 1)
        fine = make_storyboard("sb0", 320, 180, 3, 3, 0.2, 15)
        result = select_storyboard_format([fine, coarse])
        self.assertEqual(result["format_id"], "sb3")

    def test_picks_smallest_tile_that_still_meets_minimum_size(self):
        # 実際のYouTubeのsb3/sb2/sb1/sb0相当。プレビューの表示サイズ(160x90)を
        # 満たす最小のもの(sb1)を選び、必要以上に大きい(=フラグメント数が多い)sb0は避ける
        sb3 = make_storyboard("sb3", 48, 27, 10, 10, 0.15, 1)
        sb2 = make_storyboard("sb2", 80, 45, 10, 10, 0.2, 2)
        sb1 = make_storyboard("sb1", 160, 90, 5, 5, 0.2, 6)
        sb0 = make_storyboard("sb0", 320, 180, 3, 3, 0.2, 15)
        result = select_storyboard_format([sb3, sb2, sb1, sb0], min_width=160, min_height=90)
        self.assertEqual(result["format_id"], "sb1")

    def test_falls_back_to_largest_when_none_meet_minimum_size(self):
        # 動画が短い等でどの階層も要求解像度に届かない場合は、拡大表示になっても
        # 最も画質の良い(1マスが最大の)ものを選ぶ
        sb3 = make_storyboard("sb3", 48, 27, 10, 10, 0.15, 1)
        sb2 = make_storyboard("sb2", 80, 45, 10, 10, 0.2, 2)
        result = select_storyboard_format([sb3, sb2], min_width=160, min_height=90)
        self.assertEqual(result["format_id"], "sb2")

    def test_ignores_non_storyboard_formats(self):
        video = {"format_id": "137", "vcodec": "avc1", "acodec": "none"}
        coarse = make_storyboard("sb3", 48, 27, 10, 10, 0.15, 1)
        result = select_storyboard_format([video, coarse])
        self.assertEqual(result["format_id"], "sb3")


class StoryboardTileForTimeTest(unittest.TestCase):
    def setUp(self):
        # 635秒の動画に対するsb3相当(実際のYouTubeレスポンスを参考にした値):
        # 10x10=100マスが1枚のスプライトにすべて収まる
        self.storyboard = make_storyboard("sb3", 48, 27, 10, 10, 100 / 635, 1)
        self.duration = 635.0

    def test_time_zero_maps_to_first_tile(self):
        tile = storyboard_tile_for_time(self.storyboard, self.duration, 0)
        self.assertEqual((tile.x, tile.y), (0, 0))
        self.assertEqual(tile.fragment_url, "https://example.com/sb3/M0.jpg")

    def test_last_second_maps_to_last_tile_of_last_fragment(self):
        tile = storyboard_tile_for_time(self.storyboard, self.duration, self.duration)
        # 100マス目(index 99) -> row9, col9
        self.assertEqual((tile.x, tile.y), (9 * 48, 9 * 27))

    def test_middle_time_maps_to_middle_tile(self):
        tile = storyboard_tile_for_time(self.storyboard, self.duration, self.duration / 2)
        # おおよそ中央のマス(index約49-50) -> row4or5
        self.assertIn(tile.y, (4 * 27, 5 * 27))

    def test_tile_size_matches_storyboard_dimensions(self):
        tile = storyboard_tile_for_time(self.storyboard, self.duration, 100)
        self.assertEqual((tile.width, tile.height), (48, 27))

    def test_multiple_fragments_selects_correct_one(self):
        # 2フラグメント、各100マス(計200マス)の動画長400秒の例
        storyboard = make_storyboard("sb2", 80, 45, 10, 10, 200 / 400, 2)
        # 後半(2番目のフラグメント)に入る時刻
        tile = storyboard_tile_for_time(storyboard, 400.0, 350)
        self.assertEqual(tile.fragment_url, "https://example.com/sb2/M1.jpg")

    def test_negative_time_clamped_to_start(self):
        tile = storyboard_tile_for_time(self.storyboard, self.duration, -10)
        self.assertEqual((tile.x, tile.y), (0, 0))

    def test_time_beyond_duration_clamped_to_end(self):
        tile = storyboard_tile_for_time(self.storyboard, self.duration, self.duration + 1000)
        self.assertEqual((tile.x, tile.y), (9 * 48, 9 * 27))

    def test_missing_fps_returns_none(self):
        broken = dict(self.storyboard)
        broken["fps"] = None
        self.assertIsNone(storyboard_tile_for_time(broken, self.duration, 10))

    def test_zero_duration_returns_none(self):
        self.assertIsNone(storyboard_tile_for_time(self.storyboard, 0, 10))


if __name__ == "__main__":
    unittest.main()


class InvalidStoryboardInputTest(unittest.TestCase):
    @staticmethod
    def _storyboard(**overrides):
        storyboard = {
            "format_note": "storyboard", "width": 48, "height": 27, "rows": 10, "columns": 10, "fps": 0.5,
            "fragments": [{"url": "https://example.com/sb0.jpg", "duration": 200.0}],
        }
        storyboard.update(overrides)
        return storyboard

    def test_fragment_without_url_is_not_used(self):
        """urlの無いフラグメントで切り出し位置を求めるとKeyErrorになる"""
        broken = self._storyboard(fragments=[{"duration": 200.0}])
        self.assertIsNone(select_storyboard_format([broken]))
        self.assertIsNone(storyboard_tile_for_time(broken, 100.0, 10.0))

    def test_candidates_missing_tile_size_are_skipped(self):
        good = self._storyboard()
        for key in ("width", "height", "rows", "columns", "fps"):
            with self.subTest(key=key):
                self.assertIs(select_storyboard_format([self._storyboard(**{key: None}), good]), good)

    def test_non_positive_duration_returns_none(self):
        for duration in (0, -10.0):
            with self.subTest(duration=duration):
                self.assertIsNone(storyboard_tile_for_time(self._storyboard(), duration, 5.0))
