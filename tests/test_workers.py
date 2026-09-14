"""workers.py の DownloadWorker のうち、実ダウンロードやQtイベントループに
依存しないロジック(進捗計算・ファイル名解決・後処理判定等)の単体テスト"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yt_dlp
from yt_dlp.postprocessor import FFmpegPostProcessor

from workers import DownloadWorker, FormatListWorker


def make_worker(**kwargs):
    defaults = dict(url="https://example.com/watch?v=x", out_dir="C:/out", format_spec="b")
    defaults.update(kwargs)
    return DownloadWorker(**defaults)


class ThumbnailUrlCandidatesTest(unittest.TestCase):
    def test_prefers_declared_thumbnail_first(self):
        info = {
            "thumbnail": "https://example.com/maxresdefault.webp",
            "thumbnails": [
                {"url": "https://example.com/default.jpg", "width": 120, "height": 90},
                {"url": "https://example.com/hqdefault.jpg", "width": 480, "height": 360},
            ],
        }
        candidates = FormatListWorker._thumbnail_url_candidates(info)
        self.assertEqual(candidates[0], "https://example.com/maxresdefault.webp")

    def test_orders_remaining_by_resolution_descending(self):
        info = {
            "thumbnail": None,
            "thumbnails": [
                {"url": "https://example.com/small.jpg", "width": 120, "height": 90},
                {"url": "https://example.com/large.jpg", "width": 1280, "height": 720},
                {"url": "https://example.com/medium.jpg", "width": 480, "height": 360},
            ],
        }
        candidates = FormatListWorker._thumbnail_url_candidates(info)
        self.assertEqual(
            candidates,
            [
                "https://example.com/large.jpg",
                "https://example.com/medium.jpg",
                "https://example.com/small.jpg",
            ],
        )

    def test_deduplicates_urls(self):
        info = {
            "thumbnail": "https://example.com/same.jpg",
            "thumbnails": [
                {"url": "https://example.com/same.jpg", "width": 1280, "height": 720},
                {"url": "https://example.com/other.jpg", "width": 480, "height": 360},
            ],
        }
        candidates = FormatListWorker._thumbnail_url_candidates(info)
        self.assertEqual(
            candidates,
            ["https://example.com/same.jpg", "https://example.com/other.jpg"],
        )

    def test_handles_missing_thumbnails(self):
        self.assertEqual(FormatListWorker._thumbnail_url_candidates({}), [])


class FormatListWorkerRunTest(unittest.TestCase):
    """run()が候補URLを順に試し、失敗した候補をスキップして次点に
    フォールバックすることを検証する(存在しない推測URLに当たった際の対策)"""

    @staticmethod
    def _make_fake_ydl(info):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.__exit__.return_value = False
        ydl.extract_info.return_value = info
        return ydl

    def test_falls_back_to_next_candidate_when_first_fails(self):
        info = {
            "formats": [],
            "title": "Sample",
            "duration": 12.0,
            "thumbnail": "https://example.com/maxresdefault.webp",
            "thumbnails": [
                {"url": "https://example.com/maxresdefault.webp", "width": 1920, "height": 1080},
                {"url": "https://example.com/hqdefault.jpg", "width": 480, "height": 360},
            ],
        }

        def fake_urlopen(url, timeout=10):
            if url == "https://example.com/maxresdefault.webp":
                raise OSError("404")
            resp = MagicMock()
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = False
            resp.read.return_value = b"fallback-bytes"
            return resp

        results = []
        worker = FormatListWorker("https://example.com/watch?v=x")
        worker.finished_ok.connect(lambda *args: results.append(args))

        with patch("workers.yt_dlp.YoutubeDL", return_value=self._make_fake_ydl(info)), \
             patch("workers.urllib.request.urlopen", side_effect=fake_urlopen):
            worker.run()

        self.assertEqual(len(results), 1)
        _, _, thumbnail_bytes, _ = results[0]
        self.assertEqual(thumbnail_bytes, b"fallback-bytes")

    def test_stops_at_first_successful_candidate(self):
        info = {
            "formats": [],
            "title": "Sample",
            "duration": 12.0,
            "thumbnail": "https://example.com/maxresdefault.webp",
            "thumbnails": [
                {"url": "https://example.com/maxresdefault.webp", "width": 1920, "height": 1080},
                {"url": "https://example.com/hqdefault.jpg", "width": 480, "height": 360},
            ],
        }

        attempted_urls = []

        def fake_urlopen(url, timeout=10):
            attempted_urls.append(url)
            resp = MagicMock()
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = False
            resp.read.return_value = b"best-bytes"
            return resp

        worker = FormatListWorker("https://example.com/watch?v=x")

        with patch("workers.yt_dlp.YoutubeDL", return_value=self._make_fake_ydl(info)), \
             patch("workers.urllib.request.urlopen", side_effect=fake_urlopen):
            worker.run()

        self.assertEqual(attempted_urls, ["https://example.com/maxresdefault.webp"])

    def test_all_candidates_failing_yields_empty_thumbnail(self):
        info = {
            "formats": [],
            "title": "Sample",
            "duration": None,
            "thumbnail": "https://example.com/maxresdefault.webp",
            "thumbnails": [],
        }

        results = []
        worker = FormatListWorker("https://example.com/watch?v=x")
        worker.finished_ok.connect(lambda *args: results.append(args))

        with patch("workers.yt_dlp.YoutubeDL", return_value=self._make_fake_ydl(info)), \
             patch("workers.urllib.request.urlopen", side_effect=OSError("network down")):
            worker.run()

        self.assertEqual(len(results), 1)
        _, _, thumbnail_bytes, _ = results[0]
        self.assertEqual(thumbnail_bytes, b"")


class FormatEtaTest(unittest.TestCase):
    def test_seconds_only(self):
        self.assertEqual(DownloadWorker._format_eta(45), "00:45")

    def test_minutes_and_seconds(self):
        self.assertEqual(DownloadWorker._format_eta(125), "02:05")

    def test_hours_minutes_seconds(self):
        self.assertEqual(DownloadWorker._format_eta(3725), "1:02:05")

    def test_negative_is_unknown(self):
        self.assertEqual(DownloadWorker._format_eta(-1), "--:--")

    def test_nan_is_unknown(self):
        nan = float("nan")
        self.assertEqual(DownloadWorker._format_eta(nan), "--:--")


class DescribeSelectedFormatTest(unittest.TestCase):
    def test_video_and_audio_combined(self):
        info = {
            "format_id": "137+140",
            "ext": "mp4",
            "vcodec": "avc1.640028",
            "acodec": "mp4a.40.2",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "abr": 128,
            "filesize": 1024 * 1024,
        }
        text = DownloadWorker._describe_selected_format(info)
        self.assertIn("映像+音声", text)
        self.assertIn("解像度:1920x1080 30fps", text)
        self.assertIn("音声コーデック:mp4a.40.2 約128kbps", text)
        self.assertIn("サイズ:1.0MB", text)

    def test_video_only(self):
        info = {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none"}
        text = DownloadWorker._describe_selected_format(info)
        self.assertIn("映像 (mp4)", text)
        self.assertNotIn("音声コーデック", text)

    def test_audio_only(self):
        info = {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"}
        text = DownloadWorker._describe_selected_format(info)
        self.assertIn("音声 (m4a)", text)
        self.assertNotIn("解像度", text)


class InitComponentWeightsTest(unittest.TestCase):
    def test_weights_proportional_to_known_sizes(self):
        worker = make_worker()
        probe_info = {
            "requested_formats": [
                {"format_id": "137", "filesize": 30},
                {"format_id": "140", "filesize": 10},
            ]
        }
        worker._init_component_weights(probe_info)
        self.assertEqual(worker._component_ids, ["137", "140"])
        self.assertAlmostEqual(worker._component_weights[0], 0.75)
        self.assertAlmostEqual(worker._component_weights[1], 0.25)

    def test_equal_split_when_sizes_unknown(self):
        worker = make_worker()
        probe_info = {
            "requested_formats": [
                {"format_id": "137", "filesize": None},
                {"format_id": "140", "filesize": None},
            ]
        }
        worker._init_component_weights(probe_info)
        self.assertEqual(worker._component_weights, [0.5, 0.5])

    def test_single_format_without_requested_formats(self):
        worker = make_worker()
        probe_info = {"format_id": "22", "filesize": 100}
        worker._init_component_weights(probe_info)
        self.assertEqual(worker._component_ids, ["22"])
        self.assertEqual(worker._component_weights, [1.0])


class ExpectedExtTest(unittest.TestCase):
    def test_mp3_postprocessor_forces_mp3_ext(self):
        worker = make_worker(postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}])
        self.assertEqual(worker._expected_ext({"ext": "webm"}), "mp3")

    def test_best_preferredcodec_falls_back_to_probe_ext(self):
        worker = make_worker(postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "best"}])
        self.assertEqual(worker._expected_ext({"ext": "m4a"}), "m4a")

    def test_webm_with_thumbnail_and_merge_becomes_mkv(self):
        worker = make_worker()
        probe_info = {"ext": "webm", "requested_formats": [{}], "thumbnails": [{"url": "x"}]}
        self.assertEqual(worker._expected_ext(probe_info), "mkv")

    def test_webm_without_merge_stays_webm(self):
        worker = make_worker()
        probe_info = {"ext": "webm", "thumbnails": [{"url": "x"}]}
        self.assertEqual(worker._expected_ext(probe_info), "webm")

    def test_no_postprocessor_returns_probe_ext(self):
        worker = make_worker()
        self.assertEqual(worker._expected_ext({"ext": "mp4"}), "mp4")


class BuildTitleTest(unittest.TestCase):
    def test_no_clip_range_returns_title_unchanged(self):
        worker = make_worker()
        self.assertEqual(worker._build_title({"title": "My Video"}), "My Video")

    def test_missing_title_falls_back_to_video(self):
        worker = make_worker()
        self.assertEqual(worker._build_title({}), "video")

    def test_clip_range_appends_label_to_distinguish_from_full_video(self):
        worker = make_worker(start_time=60.0, end_time=120.0)
        self.assertEqual(worker._build_title({"title": "My Video"}), "My Video [1:00-2:00]")

    def test_open_ended_clip_range_leaves_end_side_empty(self):
        worker = make_worker(start_time=60.0)
        self.assertEqual(worker._build_title({"title": "My Video"}), "My Video [1:00-]")

    def test_open_start_clip_range_leaves_start_side_empty(self):
        worker = make_worker(end_time=120.0)
        self.assertEqual(worker._build_title({"title": "My Video"}), "My Video [-2:00]")


class ResolveUniqueTitleTest(unittest.TestCase):
    def test_no_conflict_returns_sanitized_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            title = worker._resolve_unique_title("My Video", "mp4")
            self.assertEqual(title, "My Video")

    def test_conflict_with_known_ext_appends_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "My Video.mp4"), "w").close()
            worker = make_worker(out_dir=tmp)
            title = worker._resolve_unique_title("My Video", "mp4")
            self.assertEqual(title, "My Video (1)")

    def test_conflict_increments_until_free_name_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "My Video.mp4"), "w").close()
            open(os.path.join(tmp, "My Video (1).mp4"), "w").close()
            worker = make_worker(out_dir=tmp)
            title = worker._resolve_unique_title("My Video", "mp4")
            self.assertEqual(title, "My Video (2)")

    def test_unknown_ext_treats_any_extension_as_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "My Video.mkv"), "w").close()
            worker = make_worker(out_dir=tmp)
            title = worker._resolve_unique_title("My Video", None)
            self.assertEqual(title, "My Video (1)")

    def test_nonexistent_out_dir_returns_sanitized_title_without_error(self):
        worker = make_worker(out_dir="C:/definitely/does/not/exist/xyz")
        title = worker._resolve_unique_title("My Video", "mp4")
        self.assertEqual(title, "My Video")


class ProgressHookTest(unittest.TestCase):
    def test_cancelled_raises_download_error(self):
        import yt_dlp
        worker = make_worker()
        worker._is_cancelled = True
        with self.assertRaises(yt_dlp.utils.DownloadError):
            worker._progress_hook({"status": "downloading"})

    def test_downloading_emits_progress_and_logs_format_once(self):
        worker = make_worker()
        worker._start_time = 0.0
        worker._component_ids = ["137"]
        worker._component_weights = [1.0]
        progress_events = []
        log_events = []
        worker.progress.connect(lambda pct, text: progress_events.append((pct, text)))
        worker.log.connect(lambda msg: log_events.append(msg))

        data = {
            "status": "downloading",
            "info_dict": {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none"},
            "total_bytes": 100,
            "downloaded_bytes": 50,
            "_speed_str": "1MiB/s",
        }
        worker._progress_hook(data)
        worker._progress_hook(data)

        self.assertEqual(len(log_events), 1)  # 同じformat_idは1回しかログしない
        self.assertEqual(len(progress_events), 2)
        self.assertAlmostEqual(progress_events[0][0], 50.0)

    def test_finished_status_advances_completed_weight_and_component_index(self):
        worker = make_worker()
        worker._start_time = 0.0
        worker._component_ids = ["137", "140"]
        worker._component_weights = [0.7, 0.3]
        progress_events = []
        worker.progress.connect(lambda pct, text: progress_events.append((pct, text)))

        worker._progress_hook({
            "status": "finished",
            "filename": "video.f137.mp4",
            "info_dict": {"format_id": "137"},
        })
        self.assertAlmostEqual(worker._completed_weight, 0.7)
        self.assertEqual(worker._current_component_index, 1)
        self.assertAlmostEqual(progress_events[-1][0], 70.0)

        worker._progress_hook({
            "status": "finished",
            "filename": "audio.f140.m4a",
            "info_dict": {"format_id": "140"},
        })
        self.assertAlmostEqual(worker._completed_weight, 1.0)
        self.assertEqual(progress_events[-1], (100.0, "ダウンロード完了、後処理中..."))


class PostprocessorHookTest(unittest.TestCase):
    def test_started_and_finished_log_once_for_overlapping_calls(self):
        worker = make_worker()
        logs = []
        worker.log.connect(lambda msg: logs.append(msg))

        worker._postprocessor_hook({"status": "started", "postprocessor": "Merger"})
        worker._postprocessor_hook({"status": "started", "postprocessor": "Merger"})
        worker._postprocessor_hook({"status": "finished", "postprocessor": "Merger", "info_dict": {}})
        # まだ1件残っているため完了ログは出ない
        self.assertEqual(logs, ["後処理開始: Merger"])

        worker._postprocessor_hook({
            "status": "finished", "postprocessor": "Merger",
            "info_dict": {"filepath": "C:/out/video.mp4"},
        })
        self.assertEqual(logs, ["後処理開始: Merger", "後処理完了: Merger"])
        self.assertEqual(worker._final_filepath, "C:/out/video.mp4")


class CleanupLeftoverFilesTest(unittest.TestCase):
    def test_removes_files_matching_title_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            worker._unique_title = "My Video"
            open(os.path.join(tmp, "My Video.mp4.part"), "w").close()
            open(os.path.join(tmp, "Other.mp4"), "w").close()

            worker._cleanup_leftover_files()

            remaining = os.listdir(tmp)
            self.assertEqual(remaining, ["Other.mp4"])

    def test_noop_when_title_not_resolved_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            open(os.path.join(tmp, "Other.mp4"), "w").close()
            worker._cleanup_leftover_files()
            self.assertEqual(os.listdir(tmp), ["Other.mp4"])


class BuildFormatSelectorTest(unittest.TestCase):
    def test_returns_raw_spec_when_not_excluding_mismatched(self):
        worker = make_worker(format_spec="137+140", exclude_mismatched=False)
        self.assertEqual(worker._build_format_selector(), "137+140")

    def test_returns_callable_selector_when_excluding_mismatched(self):
        worker = make_worker(format_spec="b", exclude_mismatched=True)
        selector = worker._build_format_selector()
        self.assertTrue(callable(selector))


class SelectedVcodecTest(unittest.TestCase):
    def test_single_format_returns_its_vcodec(self):
        worker = make_worker()
        self.assertEqual(worker._selected_vcodec({"vcodec": "avc1.640028"}), "avc1.640028")

    def test_audio_only_format_returns_none(self):
        worker = make_worker()
        self.assertIsNone(worker._selected_vcodec({"vcodec": "none"}))

    def test_requested_formats_picks_the_video_entry(self):
        worker = make_worker()
        probe_info = {
            "requested_formats": [
                {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none"},
                {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2"},
            ],
        }
        self.assertEqual(worker._selected_vcodec(probe_info), "avc1.640028")


class TrimClipLocallyTest(unittest.TestCase):
    """クリップ範囲はyt-dlpのdownload_ranges(常にffmpeg直結のFFmpegFDへ切り替わり、
    進捗報告がないままYouTube側のスロットリングで無期限に停止しうる)には渡さず、
    通常ダウンロード完了後にローカルファイルへffmpegで切り出す。その切り出し処理を検証する"""

    @staticmethod
    def _make_fake_ffpp():
        """real_run_ffmpegの代わりに切り出し後ファイル(第2引数の出力パス)を実際に
        作成するフェイク。os.replaceでの差し替えが成立するようにするため"""
        ffpp = MagicMock()

        def fake_run(input_specs, output_specs):
            open(output_specs[0][0], "w").close()

        ffpp.real_run_ffmpeg.side_effect = fake_run
        return ffpp

    def test_noop_when_final_filepath_missing(self):
        worker = make_worker(end_time=20.0)
        with patch("workers.FFmpegPostProcessor") as ffpp_cls:
            worker._trim_clip_locally()
        ffpp_cls.assert_not_called()

    def test_noop_when_final_file_does_not_exist(self):
        worker = make_worker(end_time=20.0)
        worker._final_filepath = "C:/out/does_not_exist.mp4"
        with patch("workers.FFmpegPostProcessor") as ffpp_cls:
            worker._trim_clip_locally()
        ffpp_cls.assert_not_called()

    def test_runs_ffmpeg_with_input_side_seek_and_output_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp()
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally("avc1.640028")

            ffpp.real_run_ffmpeg.assert_called_once()
            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(final_path, ["-ss", "10.0"])])
            [(trimmed_path, output_opts)] = output_specs
            self.assertEqual(output_opts, ["-c:a", "copy", "-c:v", "libx264", "-crf", "18", "-t", "20.0"])
            self.assertTrue(trimmed_path.startswith(os.path.join(tmp, "My Video")))

    def test_open_start_uses_no_seek_and_end_as_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()
            worker = make_worker(end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp()
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally("avc1.640028")

            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(final_path, [])])
            self.assertEqual(
                output_specs,
                [(output_specs[0][0], ["-c:a", "copy", "-c:v", "libx264", "-crf", "18", "-t", "30.0"])],
            )

    def test_open_end_omits_duration_arg(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()
            worker = make_worker(start_time=10.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp()
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally("avc1.640028")

            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(final_path, ["-ss", "10.0"])])
            self.assertEqual(
                output_specs,
                [(output_specs[0][0], ["-c:a", "copy", "-c:v", "libx264", "-crf", "18"])],
            )

    def test_audio_always_stream_copied_regardless_of_video_codec(self):
        """音声はキーフレーム制約がないため、映像コーデックの対応有無に関わらず
        常に無劣化のストリームコピーになることを確認する"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp()
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally(None)  # 音声のみダウンロード等、映像コーデック不明

            (_, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            output_opts = output_specs[0][1]
            self.assertEqual(output_opts[:2], ["-c:a", "copy"])
            self.assertNotIn("-c:v", output_opts)

    def test_vp9_uses_libvpx_vp9_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.webm")
            open(final_path, "w").close()
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp()
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally("vp09.00.10.08")

            (_, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            output_opts = output_specs[0][1]
            self.assertIn("libvpx-vp9", output_opts)

    def test_unrecognized_codec_omits_explicit_video_encoder(self):
        """未対応コーデック(例: HEVC/AV1)はffmpegの既定エンコーダにフォールバックする
        (音声は引き続きコピーされる)"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp()
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally("hev1.1.6.L93.B0")

            (_, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            output_opts = output_specs[0][1]
            self.assertNotIn("-c:v", output_opts)
            self.assertEqual(output_opts, ["-c:a", "copy", "-t", "20.0"])

    def test_replaces_final_file_with_trimmed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            def fake_run(input_specs, output_specs):
                with open(output_specs[0][0], "w") as f:
                    f.write("trimmed")

            ffpp = MagicMock()
            ffpp.real_run_ffmpeg.side_effect = fake_run
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally()

            self.assertTrue(os.path.isfile(final_path))
            with open(final_path) as f:
                self.assertEqual(f.read(), "trimmed")
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])


class RunRegistersFfmpegLocationTest(unittest.TestCase):
    """FFmpegFD.available()等の内部チェックはFFmpegPostProcessor()を無引数生成し
    ydl_optsではなくcontextvarを見るため、run()がそれを設定しているか検証する
    (未設定だと、クリップ区間指定時にffmpeg未検出と誤判定されダウンロードが失敗する)"""

    def tearDown(self):
        FFmpegPostProcessor._ffmpeg_location.set(None)

    def test_run_sets_ffmpeg_location_contextvar(self):
        worker = make_worker()
        with patch("workers.get_ffmpeg_location", return_value="C:/bundled/ffmpeg"), \
             patch.object(yt_dlp, "YoutubeDL", side_effect=RuntimeError("stop before network access")):
            worker.run()
        self.assertEqual(FFmpegPostProcessor._ffmpeg_location.get(), "C:/bundled/ffmpeg")

    def test_run_does_not_touch_contextvar_when_ffmpeg_not_found(self):
        FFmpegPostProcessor._ffmpeg_location.set("C:/previous/ffmpeg")
        worker = make_worker()
        with patch("workers.get_ffmpeg_location", return_value=None), \
             patch.object(yt_dlp, "YoutubeDL", side_effect=RuntimeError("stop before network access")):
            worker.run()
        self.assertEqual(FFmpegPostProcessor._ffmpeg_location.get(), "C:/previous/ffmpeg")


if __name__ == "__main__":
    unittest.main()
