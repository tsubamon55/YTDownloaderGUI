"""workers.py の DownloadWorker のうち、実ダウンロードやQtイベントループに
依存しないロジック(進捗計算・ファイル名解決・後処理判定等)の単体テスト"""

import os
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yt_dlp
from yt_dlp.postprocessor import FFmpegPostProcessor

from formats import is_codec_container_mismatch
from workers import DownloadRequest, DownloadWorker, FormatListWorker, StoryboardFragmentWorker, _base_ydl_opts
from yt_dlp_selection import EXCLUDE_FORMATS_PP_KEY


def make_worker(**kwargs):
    defaults = dict(url="https://example.com/watch?v=x", out_dir="C:/out", format_spec="b")
    defaults.update(kwargs)
    return DownloadWorker(DownloadRequest(**defaults))


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

        with patch("workers.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
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
        worker = make_worker(clip_start=60.0, clip_end=120.0)
        self.assertEqual(worker._build_title({"title": "My Video"}), "My Video [1:00-2:00]")

    def test_open_ended_clip_range_leaves_end_side_empty(self):
        worker = make_worker(clip_start=60.0)
        self.assertEqual(worker._build_title({"title": "My Video"}), "My Video [1:00-]")

    def test_open_start_clip_range_leaves_start_side_empty(self):
        worker = make_worker(clip_end=120.0)
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


class PrepareOutputNameTest(unittest.TestCase):
    def test_audio_conversion_avoids_existing_source_file(self):
        """mp3変換時、yt-dlpは変換前の名前(Title.webm)が既にあるとダウンロード済みとみなして
        それを変換の入力に使い、変換後に削除する。既存のユーザーファイルを消さないよう、
        最終拡張子だけでなく変換前の拡張子の同名ファイルも衝突とみなす"""
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "My Video.webm"), "w").close()
            worker = make_worker(
                out_dir=tmp,
                postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
            )

            worker._prepare_output_name({"title": "My Video", "ext": "webm"})

            self.assertEqual(worker._unique_title, "My Video (1)")


class DownloadOutputTemplateTest(unittest.TestCase):
    """タイトルや保存先に含まれる"%("がyt-dlpの出力テンプレートとして解釈されると、
    実際の保存名が_unique_titleとずれて、衝突判定や後片付けが効かなくなる"""

    def test_percent_sequences_in_title_and_folder_are_kept_literally(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "dir %(id)s")
            worker = make_worker(out_dir=out_dir)
            worker._unique_title = "50%(half)s [a] 100%"

            opts = worker._build_download_opts("mp4", None)
            with yt_dlp.YoutubeDL(opts) as ydl:
                filename = ydl.prepare_filename({"id": "x", "title": "t", "ext": "mp4"})

            self.assertEqual(filename, os.path.join(out_dir, "50%(half)s [a] 100%.mp4"))


class ProgressHookTest(unittest.TestCase):
    def test_cancelled_raises_download_error(self):
        import yt_dlp
        worker = make_worker()
        worker._is_cancelled = True
        with self.assertRaises(yt_dlp.utils.DownloadError):
            worker._progress_hook({"status": "downloading"})

    def test_downloading_emits_progress_and_logs_format_once(self):
        worker = make_worker()
        worker._started_at = 0.0
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
        worker._started_at = 0.0
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

    def test_ignores_format_exclusion_preprocessor(self):
        """フォーマット除外の前処理はユーザーから見た後処理ではないため、ログに出さない"""
        worker = make_worker()
        logs = []
        worker.log.connect(logs.append)
        worker._postprocessor_hook({"status": "started", "postprocessor": EXCLUDE_FORMATS_PP_KEY, "info_dict": {}})
        worker._postprocessor_hook({"status": "finished", "postprocessor": EXCLUDE_FORMATS_PP_KEY, "info_dict": {}})
        self.assertEqual(logs, [])
        self.assertEqual(worker._active_postprocessors, {})


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


class FormatExclusionRegistrationTest(unittest.TestCase):
    """自動設定ではprobe用・ダウンロード用の両方のYoutubeDLに非推奨フォーマットの除外を登録し、
    手動設定では登録しない。formatには常に文字列のformat_specを渡す"""

    def _run(self, exclude_mismatched):
        created = []

        def factory(opts):
            ydl = MagicMock()
            ydl.__enter__.return_value = ydl
            ydl.__exit__.return_value = False
            ydl.extract_info.return_value = {"title": "My Video", "ext": "mp4"}
            created.append((opts, ydl))
            return ydl

        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, format_spec="bv*+ba/b", exclude_mismatched=exclude_mismatched)
            with patch("workers.get_ffmpeg_location", return_value=None), \
                 patch("workers.yt_dlp.YoutubeDL", side_effect=factory), \
                 patch("workers.add_format_exclusion") as add_mock:
                worker.run()
        return created, add_mock

    def test_auto_mode_registers_exclusion_on_probe_and_download(self):
        created, add_mock = self._run(exclude_mismatched=True)
        self.assertEqual(len(created), 2)
        self.assertEqual(
            [c.args for c in add_mock.call_args_list],
            [(ydl, is_codec_container_mismatch) for _, ydl in created],
        )

    def test_manual_mode_does_not_register_exclusion(self):
        _, add_mock = self._run(exclude_mismatched=False)
        add_mock.assert_not_called()

    def test_format_option_is_plain_spec_string(self):
        for exclude_mismatched in (True, False):
            created, _ = self._run(exclude_mismatched=exclude_mismatched)
            self.assertEqual([opts["format"] for opts, _ in created], ["bv*+ba/b", "bv*+ba/b"])


class TrimClipLocallyDelegatesTest(unittest.TestCase):
    def test_passes_final_file_and_clip_range_to_trim_clip(self):
        worker = make_worker(clip_start=10.0, clip_end=30.0)
        worker._final_filepath = "C:/out/My Video.mp4"
        with patch("workers.trim_clip") as trim_mock:
            worker._trim_clip_locally()
        trim_mock.assert_called_once()
        filepath, clip_start, clip_end, log = trim_mock.call_args.args
        self.assertEqual((filepath, clip_start, clip_end), ("C:/out/My Video.mp4", 10.0, 30.0))
        # 切り抜き中の進捗はワーカーのlogシグナルへ流れる
        logs = []
        worker.log.connect(logs.append)
        log("切り出し中")
        self.assertEqual(logs, ["切り出し中"])


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
            errors, _ = self._run_with(tmp, TimeoutError("timed out"))
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
            errors, _ = self._run_with(tmp, TimeoutError("timed out"), worker=worker)
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


class DownloadRequestTest(unittest.TestCase):
    def test_has_clip(self):
        base = dict(url="u", out_dir="o", format_spec="b")
        self.assertFalse(DownloadRequest(**base).has_clip)
        self.assertTrue(DownloadRequest(**base, clip_start=1.0).has_clip)
        self.assertTrue(DownloadRequest(**base, clip_end=2.0).has_clip)


class BaseYdlOptsTest(unittest.TestCase):
    def test_minimal(self):
        self.assertEqual(_base_ydl_opts(), {"quiet": True, "no_warnings": True, "noplaylist": True})

    def test_optional_keys(self):
        opts = _base_ydl_opts(["res"], "C:/ffmpeg")
        self.assertEqual(opts["format_sort"], ["res"])
        self.assertEqual(opts["ffmpeg_location"], "C:/ffmpeg")


class CancelDuringPostprocessKeepsFinalFileTest(unittest.TestCase):
    """Review Focus 2: download()が例外なく戻った後にキャンセル済みだった場合
    (後処理中のキャンセル)、完成済みファイルは残し.partだけを消す"""

    def test_keeps_final_and_removes_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            final_path = os.path.join(tmp, "My Video.mp4")

            def fake_download(urls):
                open(final_path, "w").close()
                open(os.path.join(tmp, "My Video.f137.mp4.part"), "w").close()
                worker._final_filepath = final_path
                worker.cancel()

            def factory(opts):
                ydl = MagicMock()
                ydl.__enter__.return_value = ydl
                ydl.__exit__.return_value = False
                ydl.extract_info.return_value = {"title": "My Video", "ext": "mp4"}
                ydl.download.side_effect = fake_download
                return ydl

            errors = []
            worker.finished_error.connect(errors.append)
            with patch("workers.get_ffmpeg_location", return_value=None), \
                 patch("workers.yt_dlp.YoutubeDL", side_effect=factory):
                worker.run()

            self.assertEqual(errors, ["キャンセルされました"])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])


if __name__ == "__main__":
    unittest.main()
