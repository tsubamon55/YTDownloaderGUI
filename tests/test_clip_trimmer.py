"""clip_trimmer.py(ダウンロード済みファイルのffmpegによる切り抜き)の単体テスト"""

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import clip_trimmer
from clip_trimmer import trim_clip


class DetectVcodecTest(unittest.TestCase):
    """detect_vcodecは、ffprobeで得たメタデータ(streams)から、実際に書き出された
    本編映像のコーデックを判定する。probe用のextract_info()(実ダウンロードとは別の
    ネットワークリクエスト)の結果をそのまま使うと、2回のリクエストの間にyt-dlp側の
    選択結果がズレて誤ったコーデック向けのエンコード設定を適用してしまうため、
    ffprobeで確定済みのローカルファイルを直接調べたメタデータを渡す"""

    def test_returns_video_stream_codec_name(self):
        metadata = {
            "streams": [
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "video", "codec_name": "h264"},
            ],
        }
        self.assertEqual(clip_trimmer.detect_vcodec(metadata), "h264")

    def test_audio_only_file_returns_none(self):
        metadata = {"streams": [{"codec_type": "audio", "codec_name": "aac"}]}
        self.assertIsNone(clip_trimmer.detect_vcodec(metadata))

    def test_skips_attached_pic_thumbnail_stream(self):
        """埋め込みサムネイルはdisposition=attached_picの映像ストリームとして
        検出されるため、本編映像のコーデック判定から除外されることを確認する"""
        metadata = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "disposition": {"attached_pic": 1},
                },
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "video", "codec_name": "h264", "disposition": {"attached_pic": 0}},
            ],
        }
        self.assertEqual(clip_trimmer.detect_vcodec(metadata), "h264")

    def test_attached_pic_only_returns_none(self):
        metadata = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "disposition": {"attached_pic": 1},
                },
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
        self.assertIsNone(clip_trimmer.detect_vcodec(metadata))


class ProbeVideoStreamsTest(unittest.TestCase):
    """probe_metadataは、detect_vcodecとattached_pic_indicesの両方が
    使う生のffprobeメタデータを1回の呼び出しでまとめて取得する。失敗時は空の
    メタデータを返し、呼び出し元が通常のフォールバック動作を続けられるようにする"""

    def test_returns_metadata_from_ffprobe(self):
        ffpp = MagicMock()
        ffpp.get_metadata_object.return_value = {"streams": [{"codec_type": "video"}]}
        self.assertEqual(
            clip_trimmer.probe_metadata(ffpp, "C:/out/video.mp4"),
            {"streams": [{"codec_type": "video"}]},
        )
        ffpp.get_metadata_object.assert_called_once_with("C:/out/video.mp4")

    def test_ffprobe_failure_returns_empty_metadata(self):
        ffpp = MagicMock()
        ffpp.get_metadata_object.side_effect = RuntimeError("ffprobe not found")
        self.assertEqual(clip_trimmer.probe_metadata(ffpp, "C:/out/video.mp4"), {})


class MainVideoStreamAbsoluteIndexTest(unittest.TestCase):
    def test_returns_index_of_first_non_attached_pic_video_stream(self):
        metadata = {
            "streams": [
                {"codec_type": "audio"},
                {"codec_type": "video", "disposition": {"attached_pic": 0}},
            ],
        }
        self.assertEqual(clip_trimmer.main_video_stream_index(metadata), 1)

    def test_skips_leading_attached_pic_stream(self):
        """埋め込みサムネイルが本編映像より先(絶対インデックス0)に来る場合でも、
        本編映像側の絶対インデックスを返すことを確認する"""
        metadata = {
            "streams": [
                {"codec_type": "video", "disposition": {"attached_pic": 1}},
                {"codec_type": "audio"},
                {"codec_type": "video", "disposition": {"attached_pic": 0}},
            ],
        }
        self.assertEqual(clip_trimmer.main_video_stream_index(metadata), 2)

    def test_no_video_stream_returns_none(self):
        metadata = {"streams": [{"codec_type": "audio"}]}
        self.assertIsNone(clip_trimmer.main_video_stream_index(metadata))


class AttachedPicAbsoluteIndicesTest(unittest.TestCase):
    def test_returns_absolute_indices_of_attached_pic_streams(self):
        metadata = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            ],
        }
        self.assertEqual(clip_trimmer.attached_pic_indices(metadata), [2])

    def test_no_attached_pic_returns_empty_list(self):
        metadata = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
        self.assertEqual(clip_trimmer.attached_pic_indices(metadata), [])


class ExtractAttachedPicsTest(unittest.TestCase):
    """extract_attached_picsは、attached_picストリームをシークを伴わない単発の
    ffmpeg呼び出しで個別の画像ファイルへ抽出する。切り抜き本体の出力側シークが
    低pts(通常0)の静止画コマを問答無用で切り捨ててしまう問題を避けるため、
    切り抜きより前にこの関数で退避しておく"""

    def test_extracts_each_attached_pic_with_codec_based_extension(self):
        ffpp = MagicMock()
        metadata = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}},
            ],
        }
        paths = clip_trimmer.extract_attached_pics(ffpp, "C:/out/video.mp4", metadata, [2])
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].endswith(".png"))
        (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
        self.assertEqual(input_specs, [("C:/out/video.mp4", [])])
        self.assertEqual(
            output_specs, [(paths[0], ["-map", "0:2", "-c", "copy", "-f", "image2", "-update", "1"])]
        )

    def test_unknown_codec_defaults_to_jpg_extension(self):
        ffpp = MagicMock()
        metadata = {"streams": [{"codec_type": "video", "codec_name": "webp", "disposition": {"attached_pic": 1}}]}
        paths = clip_trimmer.extract_attached_pics(ffpp, "C:/out/video.mp4", metadata, [0])
        self.assertTrue(paths[0].endswith(".jpg"))

    def test_extraction_failure_is_skipped_not_raised(self):
        ffpp = MagicMock()
        ffpp.real_run_ffmpeg.side_effect = RuntimeError("ffmpeg crashed")
        metadata = {"streams": [{"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}}]}
        paths = clip_trimmer.extract_attached_pics(ffpp, "C:/out/video.mp4", metadata, [0])
        self.assertEqual(paths, [])


class ReattachThumbnailsTest(unittest.TestCase):
    def test_maps_video_and_thumbnails_with_correct_disposition_index(self):
        ffpp = MagicMock()

        def fake_run(input_specs, output_specs):
            open(output_specs[0][0], "w").close()

        ffpp.real_run_ffmpeg.side_effect = fake_run

        with tempfile.TemporaryDirectory() as tmp:
            video_path = os.path.join(tmp, "clip.mp4")
            open(video_path, "w").close()
            thumb_path = os.path.join(tmp, "clip.thumb2.png")
            open(thumb_path, "w").close()

            clip_trimmer.reattach_thumbnails(ffpp, video_path, 2, [thumb_path])

            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(video_path, []), (thumb_path, [])])
            self.assertEqual(
                output_specs[0][1], ["-map", "0", "-map", "1", "-c", "copy", "-disposition:2", "attached_pic"]
            )
            self.assertTrue(os.path.isfile(video_path))

    def test_removes_intermediate_file_when_ffmpeg_fails(self):
        """ffmpegが失敗した場合、部分書き込みされた中間ファイルを残さない。
        この経路は呼び出し元で握りつぶされて成功扱い(finished_ok)になり
        _cleanup_leftover_filesも走らないため、残骸に気づく手段がない"""
        ffpp = MagicMock()

        def fail_after_partial_write(input_specs, output_specs):
            open(output_specs[0][0], "w").close()
            raise RuntimeError("ffmpeg failed")

        ffpp.real_run_ffmpeg.side_effect = fail_after_partial_write

        with tempfile.TemporaryDirectory() as tmp:
            video_path = os.path.join(tmp, "clip.mp4")
            open(video_path, "w").close()
            thumb_path = os.path.join(tmp, "clip.thumb2.png")
            open(thumb_path, "w").close()

            with self.assertRaises(RuntimeError):
                clip_trimmer.reattach_thumbnails(ffpp, video_path, 2, [thumb_path])

            self.assertNotIn("clip.thumbmerge.mp4", os.listdir(tmp))
            self.assertTrue(os.path.isfile(video_path))


class TempFileNameCollisionTest(unittest.TestCase):
    """切り抜きの中間ファイル名(.clip / .thumbmerge / .thumbN)と同名のファイルが保存先に
    既にあっても、ffmpegの-yで上書きしたり、後片付けで削除したりしない"""

    USER_DATA = "user's own file"

    def _user_file(self, tmp, name):
        path = os.path.join(tmp, name)
        with open(path, "w") as f:
            f.write(self.USER_DATA)
        return path

    def _assert_untouched(self, path):
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            self.assertEqual(f.read(), self.USER_DATA)

    def test_trim_does_not_overwrite_existing_clip_named_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "My Video.mp4")
            open(video, "w").close()
            existing = self._user_file(tmp, "My Video.clip.mp4")

            with patch("clip_trimmer.FFmpegRunner", return_value=TrimClipTest._make_fake_ffpp()):
                trim_clip(video, None, 20.0, lambda _: None)

            self._assert_untouched(existing)

    def test_failed_trim_does_not_delete_existing_clip_named_file(self):
        ffpp = TrimClipTest._make_fake_ffpp()
        ffpp.real_run_ffmpeg.side_effect = RuntimeError("ffmpeg failed")
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "My Video.mp4")
            open(video, "w").close()
            existing = self._user_file(tmp, "My Video.clip.mp4")

            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(video, None, 20.0, lambda _: None)

            self._assert_untouched(existing)

    def test_reattach_does_not_touch_existing_thumbmerge_named_file(self):
        ffpp = MagicMock()
        ffpp.real_run_ffmpeg.side_effect = RuntimeError("ffmpeg failed")
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "clip.mp4")
            open(video, "w").close()
            existing = self._user_file(tmp, "clip.thumbmerge.mp4")

            with self.assertRaises(RuntimeError):
                clip_trimmer.reattach_thumbnails(ffpp, video, 1, [os.path.join(tmp, "t.png")])

            self._assert_untouched(existing)

    def test_extracted_thumbnail_does_not_reuse_existing_file_name(self):
        metadata = {"streams": [{"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}}]}
        with tempfile.TemporaryDirectory() as tmp:
            existing = self._user_file(tmp, "clip.thumb0.png")

            paths = clip_trimmer.extract_attached_pics(MagicMock(), os.path.join(tmp, "clip.mp4"), metadata, [0])

            self.assertNotIn(existing, paths)


class NearestKeyframeAtOrBeforeTest(unittest.TestCase):
    """nearest_keyframe_at_or_beforeは、入力側の高速-ssが実際に着地する時刻
    (targetを超えない最も近いキーフレーム)を求める。trim_clipはこれと
    targetの差分だけを出力側の正確シークに使うことで、動画終盤の切り抜きで差分を
    求めずtargetをそのまま2回指定してしまい入力範囲を飛び越える(出力が空になる)
    のを防ぐ"""

    @staticmethod
    def _fake_ffpp(keyframe_times: list[float]):
        ffpp = MagicMock()
        ffpp.get_metadata_object.return_value = {
            "frames": [{"key_frame": 1, "pts_time": str(t)} for t in keyframe_times]
        }
        return ffpp

    def test_returns_largest_keyframe_at_or_before_target(self):
        ffpp = self._fake_ffpp([0.0, 5.0, 10.0, 15.0, 20.0])
        self.assertEqual(
            clip_trimmer.nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 12.0), 10.0
        )

    def test_exact_match_returns_same_value(self):
        ffpp = self._fake_ffpp([0.0, 5.0, 10.0])
        self.assertEqual(
            clip_trimmer.nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 10.0), 10.0
        )

    def test_target_before_first_keyframe_returns_zero(self):
        ffpp = self._fake_ffpp([5.0, 10.0])
        self.assertEqual(
            clip_trimmer.nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 2.0), 0.0
        )

    def test_no_keyframes_returns_zero(self):
        ffpp = self._fake_ffpp([])
        self.assertEqual(
            clip_trimmer.nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 12.0), 0.0
        )

    def test_ffprobe_failure_returns_zero(self):
        ffpp = MagicMock()
        ffpp.get_metadata_object.side_effect = RuntimeError("ffprobe not found")
        self.assertEqual(
            clip_trimmer.nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 12.0), 0.0
        )

    def test_selects_requested_stream_and_scans_keyframes_only(self):
        ffpp = self._fake_ffpp([0.0])
        clip_trimmer.nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 3, 12.0)
        _, kwargs = ffpp.get_metadata_object.call_args
        self.assertEqual(
            kwargs["opts"],
            ["-select_streams", "3", "-read_intervals", "0.0%13.0", "-skip_frame", "nokey", "-show_frames"],
        )

    def test_reads_only_the_window_before_target(self):
        """ファイル全体のキーフレームを走査しないよう、目標時刻の手前だけを読む"""
        ffpp = self._fake_ffpp([0.0])
        clip_trimmer.nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 3600.0)
        _, kwargs = ffpp.get_metadata_object.call_args
        self.assertIn("3540.0%3601.0", kwargs["opts"])

    def test_converts_between_relative_target_and_absolute_pts(self):
        """ffprobeのpts_timeは開始時刻を含む絶対時刻、-ssは開始時刻からの相対時刻。
        開始時刻1.5秒のファイルで目標10秒なら、絶対11.5秒以前のキーフレーム(11.0)を探し、
        相対時刻(9.5)で返す"""
        ffpp = self._fake_ffpp([1.5, 6.5, 11.0, 11.6])
        result = clip_trimmer.nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 10.0, start_time=1.5)
        self.assertAlmostEqual(result, 9.5)

    def test_frames_with_broken_pts_time_are_skipped(self):
        ffpp = MagicMock()
        ffpp.get_metadata_object.return_value = {
            "frames": [{"key_frame": 1, "pts_time": "N/A"}, {"key_frame": 1}, {"key_frame": 1, "pts_time": "4.0"}]
        }
        self.assertEqual(clip_trimmer.nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 5.0), 4.0)


class TrimClipTest(unittest.TestCase):
    """クリップ範囲はyt-dlpのdownload_ranges(常にffmpeg直結のFFmpegFDへ切り替わり、
    進捗報告がないままYouTube側のスロットリングで無期限に停止しうる)には渡さず、
    通常ダウンロード完了後にローカルファイルへffmpegで切り出す。その切り出し処理を検証する"""

    @staticmethod
    def _make_fake_ffpp(vcodec: str | None = "h264"):
        """real_run_ffmpegの代わりに切り出し後ファイル(第2引数の出力パス)を実際に
        作成するフェイク。os.replaceでの差し替えが成立するようにするため。
        get_metadata_objectも合わせてスタブし、detect_vcodecが指定したコーデックを
        返すようにする"""
        ffpp = MagicMock()
        streams = [{"codec_type": "video", "codec_name": vcodec}] if vcodec else []
        ffpp.get_metadata_object.return_value = {"streams": streams}

        def fake_run(input_specs, output_specs):
            open(output_specs[0][0], "w").close()

        ffpp.real_run_ffmpeg.side_effect = fake_run
        return ffpp

    @staticmethod
    def _make_fake_ffpp_with_keyframes(vcodec: str, keyframe_times: list[float]):
        """_make_fake_ffppと異なり、get_metadata_objectの応答をstreams向け問い合わせ
        (streams/vcodec検出用)とframes向け問い合わせ(-show_framesを含む、
        nearest_keyframe_at_or_before用)とで出し分ける。入力側の高速シークが
        実際に着地するキーフレーム時刻と、出力側の正確シークに使う差分が
        正しく分割されることを検証するテスト専用"""
        ffpp = MagicMock()

        def fake_get_metadata_object(path, opts=()):
            if "-show_frames" in opts:
                return {"frames": [{"key_frame": 1, "pts_time": str(t)} for t in keyframe_times]}
            return {"streams": [{"codec_type": "video", "codec_name": vcodec}]}

        ffpp.get_metadata_object.side_effect = fake_get_metadata_object

        def fake_run(input_specs, output_specs):
            open(output_specs[0][0], "w").close()

        ffpp.real_run_ffmpeg.side_effect = fake_run
        return ffpp

    def test_splits_seek_into_coarse_keyframe_seek_and_accurate_remainder(self):
        """入力側の高速-ssはキーフレーム単位でしか正確に戻れないため、実際に着地する
        キーフレーム時刻をffprobeで求め、その差分だけを出力側の正確シークに回すことを
        確認する(音声等のストリームコピーもこの差分シークの対象になる)"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            ffpp = self._make_fake_ffpp_with_keyframes("h264", [0.0, 5.0, 10.0, 15.0, 20.0])
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(final_path, 12.0, 22.0, lambda msg: None)

            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(final_path, ["-ss", "10.0"])])
            output_opts = output_specs[0][1]
            self.assertEqual(output_opts[:2], ["-ss", "2.0"])

    def test_seek_near_end_of_video_does_not_duplicate_full_start_time(self):
        """回帰防止: 以前は入力側・出力側の両方に同じstart_timeをそのまま指定して
        いたため、動画終盤(残り時間が短い区間)を切り抜くと出力側のシークが
        入力範囲を飛び越えてしまい、出力が0バイトになるバグがあった
        (例: 全長283秒の動画で177秒地点から切り抜くと、177+177=354秒分探してしまい
        何も出力されない)。差分だけを出力側に渡すことで、残り時間に関わらず
        小さな値(ここでは2.0秒)に収まることを確認する"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            keyframe_times = [float(t) for t in range(0, 283, 5)]  # 0,5,10,...,280 (GOP=5s)
            ffpp = self._make_fake_ffpp_with_keyframes("h264", keyframe_times)
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(final_path, 177.0, 187.0, lambda msg: None)

            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(final_path, ["-ss", "175.0"])])
            output_opts = output_specs[0][1]
            self.assertEqual(output_opts[:2], ["-ss", "2.0"])

    def test_noop_when_final_filepath_missing(self):
        with patch("clip_trimmer.FFmpegRunner") as ffpp_cls:
            trim_clip(None, None, 20.0, lambda msg: None)
        ffpp_cls.assert_not_called()

    def test_noop_when_final_file_does_not_exist(self):
        with patch("clip_trimmer.FFmpegRunner") as ffpp_cls:
            trim_clip("C:/out/does_not_exist.mp4", None, 20.0, lambda msg: None)
        ffpp_cls.assert_not_called()

    def test_runs_ffmpeg_with_input_side_seek_and_output_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            ffpp = self._make_fake_ffpp("h264")
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp), \
                 patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=10.0):
                trim_clip(final_path, 10.0, 30.0, lambda msg: None)

            ffpp.real_run_ffmpeg.assert_called_once()
            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(final_path, ["-ss", "10.0"])])
            [(trimmed_path, output_opts)] = output_specs
            self.assertEqual(
                output_opts,
                ["-map", "0", "-c:a", "copy", "-c:t", "copy",
                 "-c:v:0", "libx264", "-crf", "18", "-t", "20.0"],
            )
            self.assertTrue(trimmed_path.startswith(os.path.join(tmp, "My Video")))

    def test_open_start_uses_no_seek_and_end_as_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            ffpp = self._make_fake_ffpp("h264")
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(final_path, None, 30.0, lambda msg: None)

            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(final_path, [])])
            self.assertEqual(
                output_specs,
                [
                    (
                        output_specs[0][0],
                        [
                            "-map", "0", "-c:a", "copy", "-c:t", "copy",
                            "-c:v:0", "libx264", "-crf", "18", "-t", "30.0",
                        ],
                    )
                ],
            )

    def test_open_end_omits_duration_arg(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            ffpp = self._make_fake_ffpp("h264")
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp), \
                 patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=10.0):
                trim_clip(final_path, 10.0, None, lambda msg: None)

            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(final_path, ["-ss", "10.0"])])
            self.assertEqual(
                output_specs,
                [
                    (
                        output_specs[0][0],
                        ["-map", "0", "-c:a", "copy", "-c:t", "copy",
                         "-c:v:0", "libx264", "-crf", "18"],
                    )
                ],
            )

    def test_audio_always_stream_copied_regardless_of_video_codec(self):
        """音声はキーフレーム制約がないため、映像コーデックの対応有無に関わらず
        常に無劣化のストリームコピーになることを確認する"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            ffpp = self._make_fake_ffpp(None)  # 音声のみダウンロード等、映像ストリームなし
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(final_path, 10.0, 30.0, lambda msg: None)

            (_, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            output_opts = output_specs[0][1]
            self.assertEqual(output_opts[:4], ["-map", "0", "-c:a", "copy"])
            self.assertNotIn("-c:v:0", output_opts)

    def test_vp9_uses_libvpx_vp9_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.webm")
            open(final_path, "w").close()

            ffpp = self._make_fake_ffpp("vp9")
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(final_path, 10.0, 30.0, lambda msg: None)

            (_, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            output_opts = output_specs[0][1]
            self.assertIn("libvpx-vp9", output_opts)

    def test_embedded_thumbnail_is_extracted_before_trim_and_reattached_after(self):
        """埋め込みサムネイル(attached_pic映像ストリーム)は、切り抜き本体の
        ffmpeg呼び出しには一切含めず(出力側の正確シークが低pts(通常0)の
        静止画コマも問答無用で切り捨ててしまうため)、事前に画像として抽出し、
        切り抜き完了後に(シークを伴わない)別passで単純に付け直すことを確認する"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            ffpp = MagicMock()
            ffpp.get_metadata_object.return_value = {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "disposition": {"attached_pic": 0}},
                    {"codec_type": "audio", "codec_name": "aac"},
                    {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                ],
            }

            def fake_run(input_specs, output_specs):
                open(output_specs[0][0], "w").close()

            ffpp.real_run_ffmpeg.side_effect = fake_run
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp), \
                 patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=10.0):
                trim_clip(final_path, 10.0, 30.0, lambda msg: None)

            calls = ffpp.real_run_ffmpeg.call_args_list
            self.assertEqual(len(calls), 3)

            # 1. 抽出: attached_pic(絶対インデックス2)だけをシーク無しで画像へ抽出する
            extract_input_specs, extract_output_specs = calls[0].args
            self.assertEqual(extract_input_specs, [(final_path, [])])
            extract_thumb_path, extract_opts = extract_output_specs[0]
            self.assertEqual(
                extract_opts, ["-map", "0:2", "-c", "copy", "-f", "image2", "-update", "1"]
            )
            self.assertTrue(extract_thumb_path.endswith(".jpg"))  # mjpeg -> jpg

            # 2. 切り抜き本体: attached_picを-map -0:2で除外し、本編映像のみ再エンコードする
            _, trim_output_specs = calls[1].args
            trim_opts = trim_output_specs[0][1]
            self.assertEqual(
                trim_opts,
                [
                    "-map", "0", "-map", "-0:2",
                    "-c:a", "copy", "-c:t", "copy",
                    "-c:v:0", "libx264", "-crf", "18",
                    "-t", "20.0",
                ],
            )

            # 3. 再添付: シーク無しで動画+抽出済み画像をコピーのみでマージする
            #    (映像+音声の2ストリームの後に付くため disposition:2)
            reattach_input_specs, reattach_output_specs = calls[2].args
            self.assertEqual(reattach_input_specs, [(final_path, []), (extract_thumb_path, [])])
            reattach_opts = reattach_output_specs[0][1]
            self.assertEqual(
                reattach_opts, ["-map", "0", "-map", "1", "-c", "copy", "-disposition:2", "attached_pic"]
            )

    def _trim_output_opts(self, vcodec, filename="My Video.mp4"):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, filename)
            open(final_path, "w").close()

            ffpp = self._make_fake_ffpp(vcodec)
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp), \
                 patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=10.0):
                trim_clip(final_path, 10.0, 30.0, lambda msg: None)

            (_, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            return output_specs[0][1]

    def test_unrecognized_codec_is_reencoded_with_explicit_quality(self):
        """未対応コーデック(例: HEVC/AV1)をffmpeg任せにすると、webmではlibvpx-vp9が画質指定なしの
        既定ビットレートで使われ大きく劣化する。出力コンテナに合うエンコーダへ画質付きで寄せる
        (本編映像を"-c copy"にするとキーフレーム単位でしか合わせられず音声とズレるため、コピーにはしない)"""
        self.assertEqual(
            self._trim_output_opts("hevc"),
            ["-map", "0", "-c:a", "copy", "-c:t", "copy", "-c:v:0", "libx264", "-crf", "18", "-t", "20.0"],
        )
        self.assertEqual(
            self._trim_output_opts("av1", "My Video.webm"),
            [
                "-map", "0", "-c:a", "copy", "-c:t", "copy",
                "-c:v:0", "libvpx-vp9", "-crf", "31", "-b:v", "0", "-t", "20.0",
            ],
        )

    def test_vp9_uses_constant_quality_mode(self):
        """libvpx-vp9は-b:v 0を付けないと-crfが固定画質として効かない"""
        opts = self._trim_output_opts("vp9", "My Video.webm")
        self.assertEqual(opts[opts.index("-c:v:0"):opts.index("-t")], ["-c:v:0", "libvpx-vp9", "-crf", "31", "-b:v", "0"])

    def test_invalid_encoder_setting_in_config_is_ignored(self):
        with patch.dict(clip_trimmer.CLIP_VIDEO_ENCODER_BY_CODEC_PREFIX, {"hevc": "libx265"}):
            opts = self._trim_output_opts("hevc")
        self.assertIn("libx264", opts)

    def test_detection_failure_omits_explicit_video_encoder(self):
        """ffprobeでのコーデック検出自体に失敗した場合も、既定エンコーダへ
        フォールバックして処理を続行できることを確認する(本編映像を"-c copy"に
        してしまわないことも合わせて確認する)"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            ffpp = self._make_fake_ffpp("h264")
            ffpp.get_metadata_object.side_effect = RuntimeError("ffprobe not found")
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(final_path, 10.0, 30.0, lambda msg: None)

            (_, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            output_opts = output_specs[0][1]
            self.assertNotIn("-c:v:0", output_opts)
            self.assertNotIn("-c", output_opts)
            self.assertEqual(
                output_opts,
                ["-map", "0", "-c:a", "copy", "-c:t", "copy", "-t", "20.0"],
            )

    def test_ffmpeg_failure_does_not_raise_and_keeps_full_video(self):
        """切り出し(ffmpeg)自体が失敗しても、既にダウンロード済みの動画全体は
        そのまま残し、例外を外へ伝播させない(ダウンロード全体を失敗扱いにしない)"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            with open(final_path, "w") as f:
                f.write("full video")

            logs = []

            ffpp = self._make_fake_ffpp("h264")
            ffpp.real_run_ffmpeg.side_effect = RuntimeError("ffmpeg crashed")
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(final_path, 10.0, 30.0, logs.append)  # 例外を送出しないことを確認

            self.assertTrue(os.path.isfile(final_path))
            with open(final_path) as f:
                self.assertEqual(f.read(), "full video")
            self.assertTrue(any("失敗" in msg for msg in logs))

    def test_ffmpeg_failure_removes_orphaned_temp_file(self):
        """ffmpegが出力ファイルを作成した後に失敗した場合、切り出し用の
        一時ファイルがout_dirに残り続けないことを確認する"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            def fake_run_then_fail(input_specs, output_specs):
                open(output_specs[0][0], "w").close()  # ffmpegが-yで作成する挙動を再現
                raise RuntimeError("ffmpeg crashed mid-encode")

            ffpp = self._make_fake_ffpp("h264")
            ffpp.real_run_ffmpeg.side_effect = fake_run_then_fail
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(final_path, 10.0, 30.0, lambda msg: None)

            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])

    def test_replaces_final_file_with_trimmed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()

            def fake_run(input_specs, output_specs):
                with open(output_specs[0][0], "w") as f:
                    f.write("trimmed")

            ffpp = MagicMock()
            ffpp.real_run_ffmpeg.side_effect = fake_run
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp):
                trim_clip(final_path, 10.0, 30.0, lambda msg: None)

            self.assertTrue(os.path.isfile(final_path))
            with open(final_path) as f:
                self.assertEqual(f.read(), "trimmed")
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])


class SeekOptionsTest(unittest.TestCase):
    def test_no_start_means_no_seek(self):
        ffpp = MagicMock()
        for start in (None, 0.0):
            self.assertEqual(clip_trimmer._seek_options(ffpp, "C:/v.mp4", {"streams": []}, start), ([], []))
        ffpp.get_metadata_object.assert_not_called()

    def test_audio_only_seeks_on_input_side_without_remainder(self):
        ffpp = MagicMock()
        metadata = {"streams": [{"codec_type": "audio", "codec_name": "aac"}]}
        self.assertEqual(
            clip_trimmer._seek_options(ffpp, "C:/v.m4a", metadata, 12.0),
            (["-ss", "12.0"], []),
        )

    def test_video_splits_into_keyframe_and_remainder(self):
        metadata = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
        with patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=10.0):
            self.assertEqual(
                clip_trimmer._seek_options(MagicMock(), "C:/v.mp4", metadata, 12.0),
                (["-ss", "10.0"], ["-ss", "2.0"]),
            )

    def test_exact_keyframe_has_no_remainder(self):
        metadata = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
        with patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=12.0):
            self.assertEqual(
                clip_trimmer._seek_options(MagicMock(), "C:/v.mp4", metadata, 12.0),
                (["-ss", "12.0"], []),
            )


class FfmpegTimeFormatTest(unittest.TestCase):
    """ffmpegの時刻指定は指数表記(9.9e-06 等)を受け付けない。str(float)は1e-4未満で
    指数表記になるため、ごく小さい余りや長さでも固定小数点で渡すこと"""

    def test_tiny_remainder_is_not_in_exponent_notation(self):
        metadata = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
        with patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=12.99999):
            _, accurate = clip_trimmer._seek_options(MagicMock(), "C:/v.mp4", metadata, 13.0)
        self.assertEqual(accurate[0], "-ss")
        self.assertNotIn("e", accurate[1])
        self.assertAlmostEqual(float(accurate[1]), 0.00001, places=6)

    def test_tiny_duration_is_not_in_exponent_notation(self):
        opts = clip_trimmer._trim_output_options({"streams": []}, [], None, 0.00005)
        duration = opts[opts.index("-t") + 1]
        self.assertNotIn("e", duration)
        self.assertAlmostEqual(float(duration), 0.00005, places=6)


if __name__ == "__main__":
    unittest.main()


class FFmpegRunnerTest(unittest.TestCase):
    """切り抜き中もキャンセル・アプリ終了ができるよう、ffmpeg/ffprobeを止められる形で実行する"""

    def test_cancel_stops_running_process(self):
        runner = clip_trimmer.FFmpegRunner(None, is_cancelled=lambda: time.monotonic() - started > 0.3)
        started = time.monotonic()
        with self.assertRaises(clip_trimmer.ClipCancelledError):
            runner._run([sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertLess(time.monotonic() - started, 5)

    def test_does_not_start_when_already_cancelled(self):
        runner = clip_trimmer.FFmpegRunner(None, is_cancelled=lambda: True)
        with patch("clip_trimmer.Popen") as popen_cls, self.assertRaises(clip_trimmer.ClipCancelledError):
            runner._run(["ffmpeg"])
        popen_cls.assert_not_called()

    def test_ffmpeg_command_prefixes_paths_and_reports_last_error_line(self):
        runner = clip_trimmer.FFmpegRunner(None)
        runner.executable = "ffmpeg"
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a:b.mp4")
            open(src, "w").close()
            with patch.object(runner, "_run", return_value=("", "line1\nInvalid argument\n", 1)) as run:
                with self.assertRaisesRegex(RuntimeError, "Invalid argument"):
                    runner.real_run_ffmpeg([(src, ["-ss", "1"])], [(os.path.join(tmp, "out.mp4"), ["-c", "copy"])])
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:4], ["ffmpeg", "-y", "-loglevel", "repeat+info"])
        self.assertIn(f"file:{src}", cmd)
        self.assertEqual(cmd[cmd.index("-i") - 2:cmd.index("-i")], ["-ss", "1"])

    def test_missing_executables_raise(self):
        with patch("clip_trimmer.shutil.which", return_value=None):
            runner = clip_trimmer.FFmpegRunner(None)
        with self.assertRaises(FileNotFoundError):
            runner.real_run_ffmpeg([("in.mp4", [])], [("out.mp4", [])])
        with self.assertRaises(FileNotFoundError):
            runner.get_metadata_object("in.mp4")

    def test_prefers_executables_in_given_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            suffix = ".exe" if sys.platform == "win32" else ""
            for name in ("ffmpeg", "ffprobe"):
                open(os.path.join(tmp, name + suffix), "w").close()
            runner = clip_trimmer.FFmpegRunner(tmp)
            self.assertEqual(runner.executable, os.path.join(tmp, "ffmpeg" + suffix))
            self.assertEqual(runner.probe_executable, os.path.join(tmp, "ffprobe" + suffix))


class TrimClipCancelTest(unittest.TestCase):
    def test_cancel_removes_intermediate_files_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            with open(final_path, "w") as f:
                f.write("original")
            ffpp = MagicMock()
            ffpp.get_metadata_object.return_value = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}

            def cancel_midway(input_specs, output_specs):
                open(output_specs[0][0], "w").close()
                raise clip_trimmer.ClipCancelledError()

            ffpp.real_run_ffmpeg.side_effect = cancel_midway
            with patch("clip_trimmer.FFmpegRunner", return_value=ffpp), \
                 patch("clip_trimmer.nearest_keyframe_at_or_before", return_value=0.0), \
                 self.assertRaises(clip_trimmer.ClipCancelledError):
                trim_clip(final_path, 10.0, 30.0, lambda msg: None)

            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])
            with open(final_path) as f:
                self.assertEqual(f.read(), "original")
