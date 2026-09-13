"""yt_dlp_selection.py の回帰テスト。

format_engine.pyとworkers.pyが共有するyt-dlp内部の非公開の契約
(build_format_selectorが要求するctxの形)をここで直接検証する。
yt-dlpの更新でこの契約が変わった場合、真っ先にこのテストが落ちることを意図している。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from yt_dlp_selection import make_filtering_format_selector, select_formats


def make_video(format_id, ext, vcodec, height, width=None):
    return {
        "format_id": format_id,
        "ext": ext,
        "vcodec": vcodec,
        "acodec": "none",
        "height": height,
        "width": width or int(height * 16 / 9),
        "protocol": "https",
    }


def make_audio(format_id, ext, acodec, abr=128):
    return {
        "format_id": format_id,
        "ext": ext,
        "vcodec": "none",
        "acodec": acodec,
        "abr": abr,
        "protocol": "https",
    }


class SelectFormatsTest(unittest.TestCase):
    def setUp(self):
        self.formats = [
            make_video("137", "mp4", "avc1.640028", height=1080),
            make_video("248", "webm", "vp9", height=1080),
            make_audio("140", "m4a", "mp4a.40.2"),
        ]

    def test_selects_single_format_by_id(self):
        selected = select_formats(self.formats, "137", None)
        self.assertEqual(selected[0]["format_id"], "137")

    def test_merges_video_and_audio(self):
        selected = select_formats(self.formats, "137+140", None)
        self.assertEqual(selected[0]["requested_formats"][0]["format_id"], "137")
        self.assertEqual(selected[0]["requested_formats"][1]["format_id"], "140")

    def test_fallback_chain_skips_unmatched_alternatives(self):
        selected = select_formats(self.formats, "bv*[ext=avi]/137", None)
        self.assertEqual(selected[0]["format_id"], "137")

    def test_empty_formats_returns_empty_list(self):
        self.assertEqual(select_formats([], "b", None), [])


class MakeFilteringFormatSelectorTest(unittest.TestCase):
    def setUp(self):
        self.formats = [
            make_video("137", "mp4", "avc1.640028", height=1080),
            make_video("248", "webm", "vp9", height=1080),
            make_audio("140", "m4a", "mp4a.40.2"),
        ]

    def test_excludes_matching_formats_before_selection(self):
        selector = make_filtering_format_selector(
            "b", lambda f: f["format_id"] == "137"
        )
        ctx = {
            "formats": self.formats,
            "has_merged_format": False,
            "incomplete_formats": False,
        }
        selected = selector(ctx)
        selected_ids = {f["format_id"] for f in selected} | {
            component["format_id"]
            for f in selected
            for component in f.get("requested_formats", [])
        }
        self.assertNotIn("137", selected_ids)

    def test_returns_callable(self):
        selector = make_filtering_format_selector("b", lambda f: False)
        self.assertTrue(callable(selector))


if __name__ == "__main__":
    unittest.main()
