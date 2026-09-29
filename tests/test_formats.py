"""formats.py の純粋関数群に対する単体テスト(Qt起動不要)"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from formats import (
    codec_prefix,
    format_filesize,
    has_audio,
    has_video,
    describe_format_plain,
    filter_mismatched_formats,
    format_codec,
    format_columns,
    format_protocol,
    format_size,
    format_spec_1080p,
    is_codec_container_mismatch,
    protocol_rank,
)


def make_video(format_id="137", ext="mp4", vcodec="avc1.640028", height=1080, width=None,
                fps=30, filesize=None, protocol="https", format_note=""):
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
        "format_note": format_note,
    }


def make_audio(format_id="140", ext="m4a", acodec="mp4a.40.2", abr=128, filesize=None,
                protocol="https", format_note=""):
    return {
        "format_id": format_id,
        "ext": ext,
        "vcodec": "none",
        "acodec": acodec,
        "abr": abr,
        "filesize": filesize,
        "protocol": protocol,
        "format_note": format_note,
    }


class FormatSizeTest(unittest.TestCase):
    def test_none_or_zero_is_unknown(self):
        self.assertEqual(format_size(None), "不明")
        self.assertEqual(format_size(0), "不明")

    def test_megabytes_below_1gb(self):
        self.assertEqual(format_size(5 * 1024 * 1024), "5.0MB")

    def test_gigabytes_at_or_above_1024mb(self):
        self.assertEqual(format_size(2 * 1024 * 1024 * 1024), "2.00GB")

    def test_rounds_up_to_gb_instead_of_showing_1024mb(self):
        """1024MB未満でも、小数第1位への丸めで繰り上がる境界(1023.95MiB以上)は
        "1024.0MB"という桁のおかしい表示になるため、GB表記へ切り替える"""
        self.assertEqual(format_size(1073699880), "1.00GB")  # = 1023.959...MiB

    def test_just_below_rounding_boundary_stays_in_mb(self):
        self.assertEqual(format_size(1073689000), "1023.9MB")  # = 1023.949...MiB


class FormatCodecTest(unittest.TestCase):
    def test_known_video_and_audio_codec_labels(self):
        fmt = {"vcodec": "avc1.640028", "acodec": "mp4a.40.2"}
        self.assertEqual(format_codec(fmt), "H.264+AAC")

    def test_unknown_codec_falls_back_to_uppercase_prefix(self):
        fmt = {"vcodec": "xyz123", "acodec": "none"}
        self.assertEqual(format_codec(fmt), "XYZ123")

    def test_none_codec_is_omitted(self):
        fmt = {"vcodec": "none", "acodec": "opus"}
        self.assertEqual(format_codec(fmt), "Opus")

    def test_missing_codec_keys_produce_empty_string(self):
        self.assertEqual(format_codec({}), "")


class CodecContainerMismatchTest(unittest.TestCase):
    def test_vp9_in_mp4_is_mismatch(self):
        fmt = make_video(ext="mp4", vcodec="vp9")
        self.assertTrue(is_codec_container_mismatch(fmt))

    def test_avc1_in_webm_is_mismatch(self):
        fmt = make_video(ext="webm", vcodec="avc1.640028")
        self.assertTrue(is_codec_container_mismatch(fmt))

    def test_opus_in_m4a_is_mismatch(self):
        fmt = make_audio(ext="m4a", acodec="opus")
        self.assertTrue(is_codec_container_mismatch(fmt))

    def test_matching_mp4_h264_is_not_mismatch(self):
        fmt = make_video(ext="mp4", vcodec="avc1.640028")
        self.assertFalse(is_codec_container_mismatch(fmt))

    def test_matching_webm_vp9_is_not_mismatch(self):
        fmt = make_video(ext="webm", vcodec="vp9")
        self.assertFalse(is_codec_container_mismatch(fmt))

    def test_unrelated_ext_is_never_mismatch(self):
        fmt = make_video(ext="mkv", vcodec="vp9")
        self.assertFalse(is_codec_container_mismatch(fmt))


class FilterMismatchedFormatsTest(unittest.TestCase):
    def test_removes_only_mismatched_entries(self):
        good = make_video(format_id="137", ext="mp4", vcodec="avc1.640028")
        bad = make_video(format_id="399", ext="mp4", vcodec="vp9")
        result = filter_mismatched_formats([good, bad])
        self.assertEqual(result, [good])


class FormatProtocolTest(unittest.TestCase):
    def test_m3u8_is_hls(self):
        self.assertEqual(format_protocol({"protocol": "m3u8_native"}), "HLS")

    def test_dash_variants(self):
        self.assertEqual(format_protocol({"protocol": "http_dash_segments"}), "DASH")

    def test_https_and_http(self):
        self.assertEqual(format_protocol({"protocol": "https"}), "HTTPS")
        self.assertEqual(format_protocol({"protocol": "http"}), "HTTP")

    def test_unknown_protocol_is_passed_through(self):
        self.assertEqual(format_protocol({"protocol": "rtmp"}), "rtmp")

    def test_missing_protocol_is_empty_string(self):
        self.assertEqual(format_protocol({}), "")


class ProtocolRankTest(unittest.TestCase):
    def test_https_ranks_above_dash_above_hls(self):
        https_rank = protocol_rank({"protocol": "https"})
        dash_rank = protocol_rank({"protocol": "http_dash_segments"})
        hls_rank = protocol_rank({"protocol": "m3u8_native"})
        self.assertGreater(https_rank, dash_rank)
        self.assertGreater(dash_rank, hls_rank)


class FormatColumnsTest(unittest.TestCase):
    def test_video_and_audio_kind(self):
        fmt = make_video()
        fmt["acodec"] = "mp4a.40.2"
        columns = format_columns(fmt)
        self.assertEqual(columns[2], "動画+音声")

    def test_video_only_kind(self):
        columns = format_columns(make_video())
        self.assertEqual(columns[2], "動画のみ")

    def test_audio_only_kind_and_bitrate(self):
        columns = format_columns(make_audio(abr=128))
        self.assertEqual(columns[2], "音声のみ")
        self.assertEqual(columns[3], "128kbps")

    def test_no_video_no_audio_is_unknown_kind(self):
        columns = format_columns({"format_id": "0", "ext": "mhtml", "vcodec": "none", "acodec": "none"})
        self.assertEqual(columns[2], "不明")

    def test_resolution_and_fps_columns(self):
        columns = format_columns(make_video(height=1080, width=1920, fps=30))
        self.assertEqual(columns[3], "1920x1080")
        self.assertEqual(columns[4], "30fps")

    def test_integer_fps_is_not_shown_with_decimal(self):
        columns = format_columns(make_video(fps=29.97))
        self.assertEqual(columns[4], "29.97fps")

    def test_mismatched_format_gets_warning_prefix_in_note(self):
        fmt = make_video(ext="mp4", vcodec="vp9", format_note="some note")
        columns = format_columns(fmt)
        self.assertTrue(columns[-1].startswith("⚠非推奨"))
        self.assertIn("some note", columns[-1])

    def test_matched_format_has_plain_note(self):
        fmt = make_video(ext="mp4", vcodec="avc1.640028", format_note="note")
        columns = format_columns(fmt)
        self.assertEqual(columns[-1], "note")


class DescribeFormatPlainTest(unittest.TestCase):
    def test_joins_non_empty_columns_with_separator(self):
        fmt = make_video(format_id="137", ext="mp4", height=1080, width=1920, fps=30)
        text = describe_format_plain(fmt)
        self.assertIn("[137]", text)
        self.assertIn(" | ", text)
        self.assertNotIn("|  |", text)  # 空欄が連続しても余分な区切りが残らない


class FormatSpec1080pTest(unittest.TestCase):
    def test_landscape_uses_1920x1080_cap(self):
        spec = format_spec_1080p("動画 (最高画質 mp4)", portrait=False)
        self.assertIn("width<=1920", spec)
        self.assertIn("height<=1080", spec)

    def test_portrait_swaps_width_and_height_caps(self):
        spec = format_spec_1080p("動画 (最高画質 mp4)", portrait=True)
        self.assertIn("width<=1080", spec)
        self.assertIn("height<=1920", spec)

    def test_mp4_label_constrains_container_and_codec(self):
        spec = format_spec_1080p("動画 (最高画質 mp4)", portrait=False)
        self.assertIn("[ext=mp4][vcodec^=avc1]", spec)
        self.assertTrue(spec.endswith("/b"))

    def test_other_label_has_no_container_constraint(self):
        spec = format_spec_1080p("動画 (最高画質)", portrait=False)
        self.assertNotIn("ext=mp4", spec)
        self.assertTrue(spec.endswith("/b"))



class FormatPredicatesTest(unittest.TestCase):
    def test_has_video_true_for_real_codec(self):
        self.assertTrue(has_video({"vcodec": "avc1.640028"}))

    def test_has_video_false_for_none_missing_and_null(self):
        for fmt in ({"vcodec": "none"}, {}, {"vcodec": None}):
            self.assertFalse(has_video(fmt))

    def test_has_audio_true_for_real_codec(self):
        self.assertTrue(has_audio({"acodec": "mp4a.40.2"}))

    def test_has_audio_false_for_none_missing_and_null(self):
        for fmt in ({"acodec": "none"}, {}, {"acodec": None}):
            self.assertFalse(has_audio(fmt))


class FormatFilesizeTest(unittest.TestCase):
    def test_prefers_exact_filesize(self):
        self.assertEqual(format_filesize({"filesize": 100, "filesize_approx": 90}), 100)

    def test_falls_back_to_approx(self):
        self.assertEqual(format_filesize({"filesize": None, "filesize_approx": 90}), 90)

    def test_none_when_unknown(self):
        self.assertIsNone(format_filesize({}))
        self.assertIsNone(format_filesize({"filesize": 0, "filesize_approx": 0}))


class CodecPrefixTest(unittest.TestCase):
    def test_takes_lowercased_head_before_dot(self):
        self.assertEqual(codec_prefix("avc1.640028"), "avc1")
        self.assertEqual(codec_prefix("VP09.00.40.08"), "vp09")

    def test_empty_for_none_or_missing(self):
        self.assertEqual(codec_prefix("none"), "")
        self.assertEqual(codec_prefix(None), "")


if __name__ == "__main__":
    unittest.main()
