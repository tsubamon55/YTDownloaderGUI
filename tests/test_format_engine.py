"""format_engine.py の純粋関数群に対する単体テスト(Qt起動不要)"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from format_engine import (
    compute_auto_format_note,
    estimate_selection_size,
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

    def test_fallback_matches_same_format_when_no_lower_resolution_exists(self):
        # format_spec_1080pは最終手段として"/b"を含むため、1080p以下の候補が無くても
        # フォールバック自体は成立し、結果的に同じ解像度が"1080p版"として提示される
        formats = [make_video("399", "mp4", "avc1.640028", height=1440, width=2560, filesize=80_000_000)]
        plan = plan_high_resolution_confirmation(formats, "動画 (最高画質 mp4)", "399", None)
        self.assertTrue(plan.needs_confirmation)
        self.assertTrue(plan.has_fallback)
        self.assertIsNotNone(plan.fallback_spec)


class MismatchedSelectedFormatsTest(unittest.TestCase):
    def test_filters_out_none_and_matched_formats(self):
        mismatched = {"ext": "mp4", "vcodec": "vp9", "acodec": "none"}
        matched = {"ext": "mp4", "vcodec": "avc1", "acodec": "none"}
        result = mismatched_selected_formats(None, matched, mismatched)
        self.assertEqual(result, [mismatched])


if __name__ == "__main__":
    unittest.main()
