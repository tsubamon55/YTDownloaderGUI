"""format_engine.py の純粋関数群に対する単体テスト(Qt起動不要)"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from format_engine import (
    FormatSelection,
    compute_auto_format_note,
    estimate_selection_size,
    extract_audio_postprocessor,
    mismatched_selected_formats,
    plan_high_resolution_confirmation,
    resolve_format_spec,
    select_best_format,
    selection_resolution,
)


def make_video(format_id, ext, vcodec, height, width=None, fps=30, filesize=None, protocol="https"):
    return {
        "format_id": format_id,
        "ext": ext,
        "vcodec": vcodec,
        "acodec": "none",
        "height": height,
        "width": width or int(height * 16 / 9),
        "fps": fps,
        "filesize": filesize,
        "protocol": protocol,
        "url": f"https://example.com/{format_id}",
    }


def make_audio(format_id, ext, acodec, abr=128, filesize=None, protocol="https"):
    return {
        "format_id": format_id,
        "ext": ext,
        "vcodec": "none",
        "acodec": acodec,
        "abr": abr,
        "filesize": filesize,
        "protocol": protocol,
        "url": f"https://example.com/{format_id}",
    }


class ResolveFormatSpecTest(unittest.TestCase):
    def test_manual_mode_requires_at_least_one_selection(self):
        with self.assertRaises(ValueError):
            resolve_format_spec(True, None, None, False, "動画 (最高画質 mp4)")

    def test_manual_mode_video_and_audio_are_merged(self):
        video = {"format_id": "137"}
        audio = {"format_id": "140"}
        spec, postprocessors, sort = resolve_format_spec(True, video, audio, False, "")
        self.assertEqual(spec, "137+140")
        self.assertEqual(postprocessors, [])
        self.assertIsNone(sort)

    def test_manual_mode_quotes_ids_that_collide_with_reserved_words(self):
        """"b"や"mp4"のようなIDを素のまま書くと「最良」「拡張子mp4」の指定として解釈される"""
        formats = [
            make_video("b", "mp4", "avc1.640028", height=360),
            make_video("mp4", "mp4", "avc1.640028", height=480),
            make_video('x"y', "mp4", "avc1.640028", height=720),
            make_video("hi", "mp4", "avc1.640028", height=1080),
        ]
        for fmt in formats[:3]:
            spec, _, _ = resolve_format_spec(True, fmt, None, False, "")
            self.assertEqual(select_best_format(formats, spec, None)["format_id"], fmt["format_id"])

    def test_manual_mode_keeps_numeric_ids_readable(self):
        spec, _, _ = resolve_format_spec(True, {"format_id": "137"}, {"format_id": "hls-audio"}, False, "")
        self.assertEqual(spec, '137+b*[format_id="hls-audio"]')

    def test_manual_mode_audio_only_without_mp3_has_no_postprocessor(self):
        audio = {"format_id": "140"}
        spec, postprocessors, _ = resolve_format_spec(True, None, audio, False, "")
        self.assertEqual(spec, "140")
        self.assertEqual(postprocessors, [])

    def test_manual_mode_audio_only_with_mp3_adds_postprocessor(self):
        audio = {"format_id": "140"}
        spec, postprocessors, _ = resolve_format_spec(True, None, audio, True, "")
        self.assertEqual(spec, "140")
        self.assertEqual(postprocessors[0]["preferredcodec"], "mp3")

    def test_auto_mode_mp3_option(self):
        spec, postprocessors, sort = resolve_format_spec(False, None, None, False, "音声のみ (mp3)")
        self.assertEqual(spec, "ba/b")
        self.assertEqual(postprocessors[0]["preferredcodec"], "mp3")
        self.assertIsNone(sort)

    def test_auto_mode_best_quality_uses_compatible_sort(self):
        spec, postprocessors, sort = resolve_format_spec(False, None, None, False, "動画 (最高画質)")
        self.assertEqual(postprocessors, [])
        self.assertIsNotNone(sort)

    def test_auto_mode_best_quality_mp4_has_no_special_sort(self):
        _, _, sort = resolve_format_spec(False, None, None, False, "動画 (最高画質 mp4)")
        self.assertIsNone(sort)


class SelectBestFormatTest(unittest.TestCase):
    def setUp(self):
        self.formats = [
            make_video("137", "mp4", "avc1.640028", height=1080, filesize=50_000_000),
            make_video("248", "webm", "vp9", height=1080, filesize=40_000_000),
            make_audio("140", "m4a", "mp4a.40.2", filesize=4_000_000),
            make_audio("251", "webm", "opus", filesize=4_500_000),
        ]

    def test_no_available_formats_returns_none(self):
        self.assertIsNone(select_best_format([], "b", None))

    def test_prefers_mp4_h264_when_requested(self):
        spec, _, _ = resolve_format_spec(False, None, None, False, "動画 (最高画質 mp4)")
        selected = select_best_format(self.formats, spec, None)
        self.assertEqual(selection_resolution(selected), (1920, 1080))
        video_part = selected["requested_formats"][0]
        self.assertEqual(video_part["format_id"], "137")

    def test_m4a_audio_falls_back_to_combined_format(self):
        """映像+音声の結合フォーマットしか無いサイトでも「音声のみ (最高音質 m4a)」が失敗しない
        (音声の取り出しはextract_audioの後処理が行う)"""
        combined = make_video("18", "mp4", "avc1.42001E", height=360)
        combined["acodec"] = "mp4a.40.2"
        spec, postprocessors, sort = resolve_format_spec(False, None, None, False, "音声のみ (最高音質 m4a)")
        self.assertEqual(select_best_format([combined], spec, sort)["format_id"], "18")
        self.assertEqual(postprocessors, [extract_audio_postprocessor("best")])

    def test_invalid_format_spec_returns_none(self):
        self.assertIsNone(select_best_format(self.formats, "not-a-real-selector[[", None))


class EstimateAndResolutionTest(unittest.TestCase):
    def test_estimate_selection_size_sums_parts(self):
        selected = {"requested_formats": [{"filesize": 10}, {"filesize": 20}]}
        self.assertEqual(estimate_selection_size(selected), 30)

    def test_estimate_selection_size_unknown_if_any_part_unknown(self):
        selected = {"requested_formats": [{"filesize": 10}, {}]}
        self.assertIsNone(estimate_selection_size(selected))

    def test_selection_resolution_none_when_no_selection(self):
        self.assertIsNone(selection_resolution(None))

    def test_selection_resolution_picks_first_video_part(self):
        selected = {"requested_formats": [{"width": 1280, "height": 720}, {}]}
        self.assertEqual(selection_resolution(selected), (1280, 720))


class ComputeAutoFormatNoteTest(unittest.TestCase):
    def test_empty_when_no_formats_available(self):
        self.assertEqual(compute_auto_format_note([], "動画 (最高画質 mp4)"), "")

    def test_empty_for_other_labels(self):
        formats = [make_video("137", "mp4", "avc1.640028", height=1080)]
        self.assertEqual(compute_auto_format_note(formats, "動画 (最高画質)"), "")

    def test_notes_when_mp4_h264_loses_resolution(self):
        formats = [
            make_video("137", "mp4", "avc1.640028", height=720),
            make_video("399", "mp4", "av01.0.05M.08", height=1080),
        ]
        note = compute_auto_format_note(formats, "動画 (最高画質 mp4)")
        self.assertIn("720p", note)
        self.assertIn("1080p", note)

    def test_portrait_note_uses_short_side(self):
        """縦型(1080x1920)を「1920p」と表示しない"""
        formats = [
            make_video("137", "mp4", "avc1.640028", height=1280, width=720),
            make_video("399", "mp4", "av01.0.05M.08", height=1920, width=1080),
        ]
        note = compute_auto_format_note(formats, "動画 (最高画質 mp4)")
        self.assertIn("720p", note)
        self.assertIn("1080p", note)
        self.assertNotIn("1920p", note)

    def test_no_note_when_resolution_matches(self):
        formats = [make_video("137", "mp4", "avc1.640028", height=1080)]
        self.assertEqual(compute_auto_format_note(formats, "動画 (最高画質 mp4)"), "")


class PlanHighResolutionConfirmationTest(unittest.TestCase):
    def test_no_confirmation_needed_at_or_below_1080p(self):
        formats = [make_video("137", "mp4", "avc1.640028", height=1080)]
        plan = plan_high_resolution_confirmation(formats, "動画 (最高画質 mp4)", "137", None)
        self.assertFalse(plan.needs_confirmation)

    def test_no_confirmation_when_nothing_selectable(self):
        plan = plan_high_resolution_confirmation([], "動画 (最高画質 mp4)", "137", None)
        self.assertFalse(plan.needs_confirmation)

    def test_confirmation_needed_above_1080p_with_fallback(self):
        formats = [
            make_video("399", "mp4", "avc1.640028", height=1440, width=2560, filesize=80_000_000),
            make_video("137", "mp4", "avc1.640028", height=1080, filesize=50_000_000),
        ]
        plan = plan_high_resolution_confirmation(formats, "動画 (最高画質 mp4)", "399", None)
        self.assertTrue(plan.needs_confirmation)
        self.assertTrue(plan.has_fallback)
        self.assertIn("2560x1440", plan.message)
        self.assertIn("1080", plan.message)
        self.assertIsNotNone(plan.fallback_spec)

    def test_no_fallback_offered_when_no_lower_resolution_exists(self):
        """format_spec_1080pは最終手段として"/b"を含むため、1080p以下の候補が無いと同じ
        高解像度が選ばれる。押しても1080pにならない「1080pでダウンロード」は出さない"""
        formats = [make_video("399", "mp4", "avc1.640028", height=2160, width=3840, filesize=80_000_000)]
        plan = plan_high_resolution_confirmation(formats, "動画 (最高画質 mp4)", "399", None)
        self.assertTrue(plan.needs_confirmation)
        self.assertFalse(plan.has_fallback)
        self.assertIsNone(plan.fallback_spec)
        self.assertNotIn("1080pにすると", plan.message)

    def test_missing_width_is_treated_as_landscape(self):
        """幅が欠けた横長4Kを、幅0の縦型動画として扱わない(縦型の上限で1080p版を選ばない)"""
        uhd = make_video("401", "mp4", "avc1.640028", height=2160, filesize=80_000_000)
        fhd = make_video("137", "mp4", "avc1.640028", height=1080, filesize=50_000_000)
        uhd["width"] = fhd["width"] = None
        plan = plan_high_resolution_confirmation([uhd, fhd], "動画 (最高画質 mp4)", "401", None)
        self.assertTrue(plan.needs_confirmation)
        self.assertIn("2160p", plan.message)
        self.assertNotIn("0x2160", plan.message)
        self.assertIn("1080p(", plan.message)
        self.assertTrue(plan.has_fallback)

    def test_missing_width_is_derived_from_aspect_ratio(self):
        fmt = make_video("401", "mp4", "avc1.640028", height=1920)
        fmt["width"] = None
        fmt["aspect_ratio"] = 0.5625
        self.assertEqual(selection_resolution(fmt), (1080, 1920))


class MismatchedSelectedFormatsTest(unittest.TestCase):
    def test_filters_out_none_and_matched_formats(self):
        mismatched = {"ext": "mp4", "vcodec": "vp9", "acodec": "none"}
        matched = {"ext": "mp4", "vcodec": "avc1", "acodec": "none"}
        result = mismatched_selected_formats(None, matched, mismatched)
        self.assertEqual(result, [mismatched])


class ResolveFormatSpecTypeTest(unittest.TestCase):
    def test_returns_named_selection(self):
        selection = resolve_format_spec(False, None, None, False, "音声のみ (mp3)")
        self.assertIsInstance(selection, FormatSelection)
        self.assertEqual(selection.spec, "ba/b")
        self.assertEqual(selection.postprocessors, [extract_audio_postprocessor("mp3")])

    def test_unknown_label_raises_value_error(self):
        with self.assertRaises(ValueError):
            resolve_format_spec(False, None, None, False, "存在しない形式")


class ExtractAudioPostprocessorTest(unittest.TestCase):
    def test_mp3_includes_quality(self):
        pp = extract_audio_postprocessor("mp3")
        self.assertEqual(pp["key"], "FFmpegExtractAudio")
        self.assertEqual(pp["preferredcodec"], "mp3")
        self.assertIn("preferredquality", pp)

    def test_best_has_no_quality(self):
        self.assertEqual(
            extract_audio_postprocessor("best"), {"key": "FFmpegExtractAudio", "preferredcodec": "best"}
        )


if __name__ == "__main__":
    unittest.main()
