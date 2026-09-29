"""yt_dlp_selection.py の回帰テスト。

format_engine.pyとworkers.pyが共有するyt-dlp内部の非公開の契約
(build_format_selectorが要求するctxの形)をここで直接検証する。
yt-dlpの更新でこの契約が変わった場合、真っ先にこのテストが落ちることを意図している。
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yt_dlp

from yt_dlp_selection import (
    EXCLUDE_FORMATS_PP_KEY,
    ExcludeFormatsPP,
    add_format_exclusion,
    make_filtering_format_selector,
    select_formats,
)


def make_video(format_id, ext, vcodec, height, width=None):
    return {
        "format_id": format_id,
        "ext": ext,
        "vcodec": vcodec,
        "acodec": "none",
        "height": height,
        "width": width or int(height * 16 / 9),
        "protocol": "https",
        "url": f"https://example.com/{format_id}",
    }


def make_audio(format_id, ext, acodec, abr=128):
    return {
        "format_id": format_id,
        "ext": ext,
        "vcodec": "none",
        "acodec": acodec,
        "abr": abr,
        "protocol": "https",
        "url": f"https://example.com/{format_id}",
    }


def make_info(formats):
    """process_ie_resultに渡せる最小限の動画情報(extractorは候補が無いときのエラー組み立てに必須)"""
    return {"id": "x", "title": "x", "extractor": "generic", "extractor_key": "Generic", "formats": formats}


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

    def test_no_matching_format_returns_empty_list(self):
        self.assertEqual(select_formats(self.formats, "bv*[ext=avi]", None), [])

    def test_single_format_has_no_requested_formats(self):
        """単体フォーマットの選択結果にrequested_formatsが付くと、サイズ・解像度の計算を誤る"""
        selected = select_formats(self.formats, "137", None)
        self.assertNotIn("requested_formats", selected[0])

    def test_does_not_modify_input_formats(self):
        before = copy.deepcopy(self.formats)
        select_formats(self.formats, "137+140", None)
        self.assertEqual(self.formats, before)

    def test_format_sort_is_applied(self):
        """同じ解像度ならformat_sortで指定したコーデック(avc)が優先される"""
        selected = select_formats(self.formats, "bv*", ["res", "codec:avc:m4a"])
        self.assertEqual(selected[0]["format_id"], "137")


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


class ExcludeFormatsPPTest(unittest.TestCase):
    def test_removes_matching_formats(self):
        pp = ExcludeFormatsPP(lambda f: f["format_id"] == "137")
        files, info = pp.run({"formats": [
            make_video("137", "mp4", "avc1.640028", height=1080),
            make_audio("140", "m4a", "mp4a.40.2"),
        ]})
        self.assertEqual(files, [])
        self.assertEqual([f["format_id"] for f in info["formats"]], ["140"])

    def test_info_without_formats_is_returned_unchanged(self):
        pp = ExcludeFormatsPP(lambda f: True)
        files, info = pp.run({"id": "x"})
        self.assertEqual(files, [])
        self.assertEqual(info, {"id": "x"})

    def test_pp_key_matches_constant(self):
        self.assertEqual(EXCLUDE_FORMATS_PP_KEY, "ExcludeFormats")


class AddFormatExclusionTest(unittest.TestCase):
    """実際のyt-dlpで、pre_processで除いたフォーマットが選択候補から外れることを確認する(ネットワークなし)"""

    def setUp(self):
        self.formats = [
            make_video("137", "mp4", "avc1.640028", height=1080),
            # YouTubeが高解像度で出す「mp4だが中身はVP9」のフォーマット
            make_video("616", "mp4", "vp09.00.50.08", height=2160),
            make_audio("140", "m4a", "mp4a.40.2"),
        ]

    def _select(self, formats, format_spec, exclude=None, extra_opts=None):
        opts = {"quiet": True, "no_warnings": True, "format": format_spec, **(extra_opts or {})}
        with yt_dlp.YoutubeDL(opts) as ydl:
            if exclude is not None:
                add_format_exclusion(ydl, exclude)
            return ydl.process_ie_result(make_info(formats), download=False)

    def test_without_exclusion_highest_resolution_is_selected(self):
        """対照: 除外しなければ2160pが選ばれる(下のテストが意味を持つことの確認)"""
        self.assertEqual(self._select(self.formats, "bv*+ba/b")["format_id"], "616+140")

    def test_excluded_format_is_not_selected(self):
        selected = self._select(self.formats, "bv*+ba/b", exclude=lambda f: f["format_id"] == "616")
        self.assertEqual(selected["format_id"], "137+140")

    def test_audio_with_mismatched_codec_is_excluded(self):
        formats = [
            make_audio("140", "m4a", "mp4a.40.2", abr=128),
            make_audio("999", "m4a", "opus", abr=160),
        ]
        selected = self._select(formats, "ba[ext=m4a]", exclude=lambda f: f["format_id"] == "999")
        self.assertEqual(selected["format_id"], "140")

    def test_error_when_all_candidates_are_excluded(self):
        with self.assertRaises((yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError)):
            self._select(self.formats, "bv*+ba/b", exclude=lambda f: True)

    def test_hook_reports_pp_key(self):
        seen = []
        self._select(
            self.formats, "bv*+ba/b", exclude=lambda f: False,
            extra_opts={"postprocessor_hooks": [lambda d: seen.append(d["postprocessor"])]},
        )
        self.assertEqual(set(seen), {EXCLUDE_FORMATS_PP_KEY})


if __name__ == "__main__":
    unittest.main()
