"""workers.py の DownloadWorker のうち、実ダウンロードやQtイベントループに
依存しないロジック(進捗計算・ファイル名解決・後処理判定等)の単体テスト"""

import os
import socket
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yt_dlp
from yt_dlp.postprocessor import FFmpegPostProcessor

from workers import DownloadWorker, FormatListWorker, StoryboardFragmentWorker


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

    def test_uses_short_timeout_per_candidate(self):
        """サムネイルはGoogleのCDNから小さな画像を取得するだけの軽い処理で、
        正常時は1秒未満で応答が返るため、通信が詰まった異常系での待ち時間を
        抑えるべく一般的なWeb APIより短いタイムアウトを使う(候補数分だけ
        積み重なるため、1候補あたりの秒数を抑えることが特に重要)"""
        info = {
            "formats": [],
            "title": "Sample",
            "duration": 12.0,
            "thumbnail": "https://example.com/maxresdefault.webp",
            "thumbnails": [],
        }

        seen_timeouts = []

        def fake_urlopen(url, timeout=None):
            seen_timeouts.append(timeout)
            resp = MagicMock()
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = False
            resp.read.return_value = b"bytes"
            return resp

        worker = FormatListWorker("https://example.com/watch?v=x")
        with patch("workers.yt_dlp.YoutubeDL", return_value=self._make_fake_ydl(info)), \
             patch("workers.urllib.request.urlopen", side_effect=fake_urlopen):
            worker.run()

        self.assertEqual(seen_timeouts, [FormatListWorker.THUMBNAIL_FETCH_TIMEOUT_SECONDS])
        self.assertEqual(FormatListWorker.THUMBNAIL_FETCH_TIMEOUT_SECONDS, 5)

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

    def test_network_error_during_extract_info_yields_friendly_message(self):
        """フォーマット一覧取得(extract_info)自体が通信エラーで失敗した場合も、
        ダウンロード時と同じ親切なネットワークエラーメッセージになることを確認する"""
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.__exit__.return_value = False
        ydl.extract_info.side_effect = urllib.error.URLError("getaddrinfo failed")

        errors = []
        worker = FormatListWorker("https://example.com/watch?v=x")
        worker.finished_error.connect(errors.append)

        with patch("workers.yt_dlp.YoutubeDL", return_value=ydl):
            worker.run()

        self.assertEqual(len(errors), 1)
        self.assertIn("ネットワーク接続が切断されたため、動画情報の取得を中断しました", errors[0])

    def test_non_network_error_during_extract_info_yields_raw_message(self):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.__exit__.return_value = False
        ydl.extract_info.side_effect = ValueError("Unsupported URL")

        errors = []
        worker = FormatListWorker("https://example.com/watch?v=x")
        worker.finished_error.connect(errors.append)

        with patch("workers.yt_dlp.YoutubeDL", return_value=ydl):
            worker.run()

        self.assertEqual(errors, ["Unsupported URL"])


class StoryboardFragmentWorkerRunTest(unittest.TestCase):
    def test_success_emits_data(self):
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        resp.read.return_value = b"sprite-bytes"

        results = []
        worker = StoryboardFragmentWorker("https://example.com/storyboard.jpg")
        worker.finished_ok.connect(results.append)

        with patch("workers.urllib.request.urlopen", return_value=resp):
            worker.run()

        self.assertEqual(results, [b"sprite-bytes"])

    def test_network_error_yields_friendly_message(self):
        errors = []
        worker = StoryboardFragmentWorker("https://example.com/storyboard.jpg")
        worker.finished_error.connect(errors.append)

        with patch("workers.urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            worker.run()

        self.assertEqual(len(errors), 1)
        self.assertIn("ネットワーク接続が切断されたため、ストーリーボードの取得を中断しました", errors[0])


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


class DetectVcodecTest(unittest.TestCase):
    """_detect_vcodecは、ffprobeで得たメタデータ(streams)から、実際に書き出された
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
        self.assertEqual(DownloadWorker._detect_vcodec(metadata), "h264")

    def test_audio_only_file_returns_none(self):
        metadata = {"streams": [{"codec_type": "audio", "codec_name": "aac"}]}
        self.assertIsNone(DownloadWorker._detect_vcodec(metadata))

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
        self.assertEqual(DownloadWorker._detect_vcodec(metadata), "h264")

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
        self.assertIsNone(DownloadWorker._detect_vcodec(metadata))


class ProbeVideoStreamsTest(unittest.TestCase):
    """_probe_video_streamsは、_detect_vcodecと_attached_pic_absolute_indicesの両方が
    使う生のffprobeメタデータを1回の呼び出しでまとめて取得する。失敗時は空の
    メタデータを返し、呼び出し元が通常のフォールバック動作を続けられるようにする"""

    def test_returns_metadata_from_ffprobe(self):
        ffpp = MagicMock()
        ffpp.get_metadata_object.return_value = {"streams": [{"codec_type": "video"}]}
        self.assertEqual(
            DownloadWorker._probe_video_streams(ffpp, "C:/out/video.mp4"),
            {"streams": [{"codec_type": "video"}]},
        )
        ffpp.get_metadata_object.assert_called_once_with("C:/out/video.mp4")

    def test_ffprobe_failure_returns_empty_metadata(self):
        ffpp = MagicMock()
        ffpp.get_metadata_object.side_effect = RuntimeError("ffprobe not found")
        self.assertEqual(DownloadWorker._probe_video_streams(ffpp, "C:/out/video.mp4"), {})


class MainVideoStreamAbsoluteIndexTest(unittest.TestCase):
    def test_returns_index_of_first_non_attached_pic_video_stream(self):
        metadata = {
            "streams": [
                {"codec_type": "audio"},
                {"codec_type": "video", "disposition": {"attached_pic": 0}},
            ],
        }
        self.assertEqual(DownloadWorker._main_video_stream_absolute_index(metadata), 1)

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
        self.assertEqual(DownloadWorker._main_video_stream_absolute_index(metadata), 2)

    def test_no_video_stream_returns_none(self):
        metadata = {"streams": [{"codec_type": "audio"}]}
        self.assertIsNone(DownloadWorker._main_video_stream_absolute_index(metadata))


class AttachedPicAbsoluteIndicesTest(unittest.TestCase):
    def test_returns_absolute_indices_of_attached_pic_streams(self):
        metadata = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            ],
        }
        self.assertEqual(DownloadWorker._attached_pic_absolute_indices(metadata), [2])

    def test_no_attached_pic_returns_empty_list(self):
        metadata = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
        self.assertEqual(DownloadWorker._attached_pic_absolute_indices(metadata), [])


class ExtractAttachedPicsTest(unittest.TestCase):
    """_extract_attached_picsは、attached_picストリームをシークを伴わない単発の
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
        worker = make_worker()
        paths = worker._extract_attached_pics(ffpp, "C:/out/video.mp4", metadata, [2])
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
        worker = make_worker()
        paths = worker._extract_attached_pics(ffpp, "C:/out/video.mp4", metadata, [0])
        self.assertTrue(paths[0].endswith(".jpg"))

    def test_extraction_failure_is_skipped_not_raised(self):
        ffpp = MagicMock()
        ffpp.real_run_ffmpeg.side_effect = RuntimeError("ffmpeg crashed")
        metadata = {"streams": [{"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}}]}
        worker = make_worker()
        paths = worker._extract_attached_pics(ffpp, "C:/out/video.mp4", metadata, [0])
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

            DownloadWorker._reattach_thumbnails(ffpp, video_path, 2, [thumb_path])

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
                DownloadWorker._reattach_thumbnails(ffpp, video_path, 2, [thumb_path])

            self.assertNotIn("clip.thumbmerge.mp4", os.listdir(tmp))
            self.assertTrue(os.path.isfile(video_path))


class NearestKeyframeAtOrBeforeTest(unittest.TestCase):
    """_nearest_keyframe_at_or_beforeは、入力側の高速-ssが実際に着地する時刻
    (targetを超えない最も近いキーフレーム)を求める。_trim_clip_locallyはこれと
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
            DownloadWorker._nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 12.0), 10.0
        )

    def test_exact_match_returns_same_value(self):
        ffpp = self._fake_ffpp([0.0, 5.0, 10.0])
        self.assertEqual(
            DownloadWorker._nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 10.0), 10.0
        )

    def test_target_before_first_keyframe_returns_zero(self):
        ffpp = self._fake_ffpp([5.0, 10.0])
        self.assertEqual(
            DownloadWorker._nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 2.0), 0.0
        )

    def test_no_keyframes_returns_zero(self):
        ffpp = self._fake_ffpp([])
        self.assertEqual(
            DownloadWorker._nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 12.0), 0.0
        )

    def test_ffprobe_failure_returns_zero(self):
        ffpp = MagicMock()
        ffpp.get_metadata_object.side_effect = RuntimeError("ffprobe not found")
        self.assertEqual(
            DownloadWorker._nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 0, 12.0), 0.0
        )

    def test_selects_requested_stream_and_scans_keyframes_only(self):
        ffpp = self._fake_ffpp([0.0])
        DownloadWorker._nearest_keyframe_at_or_before(ffpp, "C:/out/video.mp4", 3, 12.0)
        _, kwargs = ffpp.get_metadata_object.call_args
        self.assertEqual(kwargs["opts"], ["-select_streams", "3", "-skip_frame", "nokey", "-show_frames"])


class TrimClipLocallyTest(unittest.TestCase):
    """クリップ範囲はyt-dlpのdownload_ranges(常にffmpeg直結のFFmpegFDへ切り替わり、
    進捗報告がないままYouTube側のスロットリングで無期限に停止しうる)には渡さず、
    通常ダウンロード完了後にローカルファイルへffmpegで切り出す。その切り出し処理を検証する"""

    @staticmethod
    def _make_fake_ffpp(vcodec: str | None = "h264"):
        """real_run_ffmpegの代わりに切り出し後ファイル(第2引数の出力パス)を実際に
        作成するフェイク。os.replaceでの差し替えが成立するようにするため。
        get_metadata_objectも合わせてスタブし、_detect_vcodecが指定したコーデックを
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
        _nearest_keyframe_at_or_before用)とで出し分ける。入力側の高速シークが
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
            worker = make_worker(start_time=12.0, end_time=22.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp_with_keyframes("h264", [0.0, 5.0, 10.0, 15.0, 20.0])
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally()

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
            worker = make_worker(start_time=177.0, end_time=187.0)
            worker._final_filepath = final_path

            keyframe_times = [float(t) for t in range(0, 283, 5)]  # 0,5,10,...,280 (GOP=5s)
            ffpp = self._make_fake_ffpp_with_keyframes("h264", keyframe_times)
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally()

            (input_specs, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            self.assertEqual(input_specs, [(final_path, ["-ss", "175.0"])])
            output_opts = output_specs[0][1]
            self.assertEqual(output_opts[:2], ["-ss", "2.0"])

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

            ffpp = self._make_fake_ffpp("h264")
            with patch("workers.FFmpegPostProcessor", return_value=ffpp), \
                 patch.object(DownloadWorker, "_nearest_keyframe_at_or_before", return_value=10.0):
                worker._trim_clip_locally()

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
            worker = make_worker(end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp("h264")
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally()

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
            worker = make_worker(start_time=10.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp("h264")
            with patch("workers.FFmpegPostProcessor", return_value=ffpp), \
                 patch.object(DownloadWorker, "_nearest_keyframe_at_or_before", return_value=10.0):
                worker._trim_clip_locally()

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
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp(None)  # 音声のみダウンロード等、映像ストリームなし
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally()

            (_, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            output_opts = output_specs[0][1]
            self.assertEqual(output_opts[:4], ["-map", "0", "-c:a", "copy"])
            self.assertNotIn("-c:v:0", output_opts)

    def test_vp9_uses_libvpx_vp9_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.webm")
            open(final_path, "w").close()
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp("vp9")
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally()

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
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

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
            with patch("workers.FFmpegPostProcessor", return_value=ffpp), \
                 patch.object(DownloadWorker, "_nearest_keyframe_at_or_before", return_value=10.0):
                worker._trim_clip_locally()

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

    def test_unrecognized_codec_omits_explicit_video_encoder(self):
        """未対応コーデック(例: HEVC/AV1)は"-c:v:0"を一切指定せずffmpegの既定エンコーダに
        フォールバックする(音声は引き続きコピーされる)。ここで本編映像まで"-c copy"に
        してしまうと、キーフレーム単位でしか正確な時刻に合わせられず音声とズレるため、
        "-c:v"/"-c"自体を一切出さないことを確認する"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp("hevc")
            with patch("workers.FFmpegPostProcessor", return_value=ffpp), \
                 patch.object(DownloadWorker, "_nearest_keyframe_at_or_before", return_value=10.0):
                worker._trim_clip_locally()

            (_, output_specs), _ = ffpp.real_run_ffmpeg.call_args
            output_opts = output_specs[0][1]
            self.assertNotIn("-c:v:0", output_opts)
            self.assertNotIn("-c", output_opts)
            self.assertEqual(
                output_opts,
                ["-map", "0", "-c:a", "copy", "-c:t", "copy", "-t", "20.0"],
            )

    def test_detection_failure_omits_explicit_video_encoder(self):
        """ffprobeでのコーデック検出自体に失敗した場合も、既定エンコーダへ
        フォールバックして処理を続行できることを確認する(本編映像を"-c copy"に
        してしまわないことも合わせて確認する)"""
        with tempfile.TemporaryDirectory() as tmp:
            final_path = os.path.join(tmp, "My Video.mp4")
            open(final_path, "w").close()
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            ffpp = self._make_fake_ffpp("h264")
            ffpp.get_metadata_object.side_effect = RuntimeError("ffprobe not found")
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally()

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
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            logs = []
            worker.log.connect(lambda msg: logs.append(msg))

            ffpp = self._make_fake_ffpp("h264")
            ffpp.real_run_ffmpeg.side_effect = RuntimeError("ffmpeg crashed")
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally()  # 例外を送出しないことを確認

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
            worker = make_worker(start_time=10.0, end_time=30.0)
            worker._final_filepath = final_path

            def fake_run_then_fail(input_specs, output_specs):
                open(output_specs[0][0], "w").close()  # ffmpegが-yで作成する挙動を再現
                raise RuntimeError("ffmpeg crashed mid-encode")

            ffpp = self._make_fake_ffpp("h264")
            ffpp.real_run_ffmpeg.side_effect = fake_run_then_fail
            with patch("workers.FFmpegPostProcessor", return_value=ffpp):
                worker._trim_clip_locally()

            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])

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


class RunErrorHandlingTest(unittest.TestCase):
    """run()が失敗した際、キャンセル・ネットワーク切断・その他のいずれの原因でも
    未完成ファイルを必ず削除すること、およびネットワーク切断時は分かりやすい
    日本語メッセージへ変換されることを検証する"""

    @staticmethod
    def _make_ydl_factory(probe_info, download_side_effect):
        def factory(opts):
            ydl = MagicMock()
            ydl.__enter__.return_value = ydl
            ydl.__exit__.return_value = False
            ydl.extract_info.return_value = probe_info
            ydl.download.side_effect = download_side_effect
            return ydl
        return factory

    def _run_with(self, tmp, download_side_effect, worker=None):
        probe_info = {"title": "My Video", "ext": "mp4"}
        worker = worker or make_worker(out_dir=tmp)
        open(os.path.join(tmp, "My Video.mp4.part"), "w").close()

        errors = []
        logs = []
        worker.finished_error.connect(lambda msg: errors.append(msg))
        worker.log.connect(lambda msg: logs.append(msg))

        with patch("workers.get_ffmpeg_location", return_value=None), \
             patch("workers.yt_dlp.YoutubeDL", side_effect=self._make_ydl_factory(probe_info, download_side_effect)):
            worker.run()

        return errors, logs

    def test_network_error_removes_leftover_part_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors, _ = self._run_with(tmp, urllib.error.URLError("no route to host"))
            self.assertEqual(len(errors), 1)
            self.assertNotIn("My Video.mp4.part", os.listdir(tmp))

    def test_network_error_message_is_user_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors, _ = self._run_with(tmp, socket.timeout("timed out"))
            self.assertEqual(len(errors), 1)
            self.assertIn("ネットワーク接続が切断されたため", errors[0])

    def test_non_network_error_still_removes_leftover_part_file(self):
        """ネットワーク切断に限らず、失敗理由を問わず未完成ファイルを削除する"""
        with tempfile.TemporaryDirectory() as tmp:
            errors, _ = self._run_with(tmp, RuntimeError("unsupported format"))
            self.assertNotIn("My Video.mp4.part", os.listdir(tmp))
            self.assertEqual(errors, ["unsupported format"])

    def test_non_network_error_message_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors, _ = self._run_with(tmp, RuntimeError("unsupported format"))
            self.assertEqual(errors, ["unsupported format"])

    def test_cancelled_download_removes_leftover_part_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            worker._is_cancelled = True
            errors, _ = self._run_with(
                tmp, yt_dlp.utils.DownloadError("ユーザーによりキャンセルされました"), worker=worker,
            )
            self.assertNotIn("My Video.mp4.part", os.listdir(tmp))
            self.assertEqual(errors, ["ユーザーによりキャンセルされました"])

    def test_cancellation_takes_priority_over_network_wording(self):
        """キャンセル中に(たまたま)ネットワーク系の例外が飛んできても、
        メッセージをネットワーク切断用の文言で上書きしない"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            worker._is_cancelled = True
            errors, _ = self._run_with(tmp, socket.timeout("timed out"), worker=worker)
            self.assertEqual(errors, ["timed out"])

    def test_cancel_after_download_completes_keeps_final_file(self):
        """後処理中にキャンセルするとdownload()は例外を投げずに正常終了し、
        完成した最終ファイルが出来ている。これをクリーンアップで消してはいけない
        (未完成の中間ファイルだけを削除する)"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            final_path = os.path.join(tmp, "My Video.mp4")

            def complete_then_cancel(urls):
                open(final_path, "w").close()
                worker._final_filepath = final_path
                worker._is_cancelled = True

            errors, logs = self._run_with(tmp, complete_then_cancel, worker=worker)

            self.assertEqual(errors, ["キャンセルされました"])
            self.assertIn("My Video.mp4", os.listdir(tmp))
            self.assertNotIn("My Video.mp4.part", os.listdir(tmp))
            self.assertTrue(any("完成済みのファイルは残しました" in log for log in logs))

    def test_cleanup_keeps_preexisting_file_with_other_extension(self):
        """同じタイトルを別拡張子で再ダウンロードして失敗しても、開始前から
        存在していた過去の完成ファイル(拡張子違い)は削除しない。
        _resolve_unique_titleは拡張子が違えば衝突とみなさないため、
        前方一致のクリーンアップが無関係なファイルを巻き込みうる"""
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.path.join(tmp, "My Video.mp3")
            open(previous, "w").close()

            errors, _ = self._run_with(tmp, RuntimeError("unsupported format"))

            self.assertEqual(errors, ["unsupported format"])
            self.assertTrue(os.path.isfile(previous))
            self.assertNotIn("My Video.mp4.part", os.listdir(tmp))

    def test_cleanup_still_removes_preexisting_part_file(self):
        """開始前から残っていても、.part等の未完成ファイルは前回の失敗の残骸なので削除する"""
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "My Video.f137.mp4"), "w").close()
            open(os.path.join(tmp, "My Video.mp4.ytdl"), "w").close()

            self._run_with(tmp, RuntimeError("boom"))

            self.assertEqual(os.listdir(tmp), [])


if __name__ == "__main__":
    unittest.main()
