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

from clip_trimmer import ClipCancelledError
from formats import is_codec_container_mismatch
from live import LiveInfo
from workers import (
    DownloadRequest,
    DownloadWorker,
    FormatListWorker,
    StoryboardFragmentWorker,
    _base_ydl_opts,
    fetch_image_bytes,
)
from yt_dlp_selection import EXCLUDE_FORMATS_PP_KEY

# テストでキャンセル通知(cancelledシグナル)を受け取ったことを、失敗メッセージと同じリストで表す印
CANCELLED = "<cancelled>"


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
        _, _, thumbnail_bytes, _, _ = results[0]
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
        _, _, thumbnail_bytes, _, _ = results[0]
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


class MoveToOutputTest(unittest.TestCase):
    def _completed(self, work_dir, name="My Video.mp4", content="new"):
        path = os.path.join(work_dir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_moves_file_with_original_name(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as work:
            worker = make_worker(out_dir=tmp)
            dest = worker._move_to_output(self._completed(work))
            self.assertEqual(dest, os.path.join(tmp, "My Video.mp4"))
            self.assertEqual(os.listdir(work), [])

    def test_existing_file_is_kept_and_counter_is_appended(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as work:
            for name in ("My Video.mp4", "My Video (1).mp4"):
                with open(os.path.join(tmp, name), "w") as f:
                    f.write("old")
            worker = make_worker(out_dir=tmp)

            dest = worker._move_to_output(self._completed(work))

            self.assertEqual(dest, os.path.join(tmp, "My Video (2).mp4"))
            with open(os.path.join(tmp, "My Video.mp4")) as f:
                self.assertEqual(f.read(), "old")
            with open(dest) as f:
                self.assertEqual(f.read(), "new")

    def test_other_extension_is_not_a_conflict(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as work:
            open(os.path.join(tmp, "My Video.mp3"), "w").close()
            worker = make_worker(out_dir=tmp)
            dest = worker._move_to_output(self._completed(work))
            self.assertEqual(dest, os.path.join(tmp, "My Video.mp4"))

    def test_reserved_placeholder_is_removed_when_move_fails(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as work:
            worker = make_worker(out_dir=tmp)
            with patch("workers.os.replace", side_effect=PermissionError("locked")), \
                 self.assertRaises(PermissionError):
                worker._move_to_output(self._completed(work))
            self.assertEqual(os.listdir(tmp), [])


class PrepareOutputNameTest(unittest.TestCase):
    def test_existing_file_does_not_change_name_inside_work_dir(self):
        """作業フォルダの中は空なので、保存先の既存ファイルとの重複は移すときに避ける"""
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "My Video.webm"), "w").close()
            worker = make_worker(
                out_dir=tmp,
                postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
            )

            worker._prepare_output_name({"title": "My Video", "ext": "webm"})

            self.assertEqual(worker._file_stem, "My Video")

    def test_title_is_sanitized_for_file_name(self):
        worker = make_worker()
        worker._prepare_output_name({"title": "a/b", "ext": "mp4"})
        self.assertNotIn("/", worker._file_stem)


class DownloadOutputTemplateTest(unittest.TestCase):
    """タイトルや保存先に含まれる"%("がyt-dlpの出力テンプレートとして解釈されると、
    実際の保存名がずれてしまう"""

    def test_percent_sequences_in_title_and_folder_are_kept_literally(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = os.path.join(tmp, "dir %(id)s")
            worker = make_worker(out_dir=tmp)
            worker._work_dir = work_dir
            worker._file_stem = "50%(half)s [a] 100%"

            opts = worker._build_download_opts("mp4", None)
            with yt_dlp.YoutubeDL(opts) as ydl:
                filename = ydl.prepare_filename({"id": "x", "title": "t", "ext": "mp4"})

            self.assertEqual(filename, os.path.join(work_dir, "50%(half)s [a] 100%.mp4"))


class WorkDirTest(unittest.TestCase):
    def test_work_dir_is_created_inside_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=os.path.join(tmp, "new"))
            worker._create_work_dir()
            self.assertEqual(os.path.dirname(worker._work_dir), os.path.join(tmp, "new"))
            self.assertTrue(os.path.basename(worker._work_dir).startswith(DownloadWorker.WORK_DIR_PREFIX))

    def test_discard_removes_work_dir_with_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            worker._create_work_dir()
            open(os.path.join(worker._work_dir, "My Video.mp4.part"), "w").close()
            worker._discard_work_dir()
            self.assertEqual(os.listdir(tmp), [])
            self.assertIsNone(worker._work_dir)

    def test_guess_ignores_incomplete_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            worker._create_work_dir()
            for name in ("My Video.mp4", "My Video.f137.mp4", "My Video.mp4.part"):
                open(os.path.join(worker._work_dir, name), "w").close()
            self.assertEqual(
                worker._completed_files(allow_guess=True), [os.path.join(worker._work_dir, "My Video.mp4")]
            )
            self.assertEqual(worker._completed_files(allow_guess=False), [])


class ProgressTextTest(unittest.TestCase):
    def test_short_text_for_download_list(self):
        self.assertEqual(
            DownloadWorker._format_progress_text(42.5, "  1.20MiB/s", "01:00"), "42% 1.20MiB/s 残り01:00"
        )

    def test_unknown_speed_is_omitted(self):
        self.assertEqual(DownloadWorker._format_progress_text(0.0, " Unknown B/s", "--:--"), "0% 残り--:--")
        self.assertEqual(DownloadWorker._format_progress_text(10.0, "", "02:00"), "10% 残り02:00")


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
            worker._trim_clip_locally("C:/ffmpeg")
        trim_mock.assert_called_once()
        filepath, clip_start, clip_end, log = trim_mock.call_args.args
        self.assertEqual((filepath, clip_start, clip_end), ("C:/out/My Video.mp4", 10.0, 30.0))
        # ffmpegの場所を明示的に渡し、キャンセルを切り抜き中のffmpegまで伝える
        kwargs = trim_mock.call_args.kwargs
        self.assertEqual(kwargs["ffmpeg_location"], "C:/ffmpeg")
        self.assertFalse(kwargs["is_cancelled"]())
        worker.cancel()
        self.assertTrue(kwargs["is_cancelled"]())
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


def _make_ydl_factory(probe_info, download_side_effect):
    def factory(opts):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.__exit__.return_value = False
        ydl.extract_info.return_value = probe_info
        ydl.download.side_effect = download_side_effect
        return ydl
    return factory


def run_worker(worker, download_side_effect, write_part=True):
    """yt-dlpをモックしてworker.run()を同期実行し、(失敗/キャンセルの通知, ログ)を返す。
    write_part=Trueなら、ダウンロード開始時に作業フォルダへ未完成の.partを書き出す"""
    probe_info = {"title": "My Video", "ext": "mp4"}

    def download(urls):
        if write_part:
            open(os.path.join(worker._work_dir, "My Video.mp4.part"), "w").close()
        if isinstance(download_side_effect, BaseException):
            raise download_side_effect
        return download_side_effect(urls)

    errors = []
    logs = []
    worker.finished_error.connect(lambda msg: errors.append(msg))
    # キャンセルは失敗(finished_error)とは別のシグナルで通知される
    worker.cancelled.connect(lambda: errors.append(CANCELLED))
    worker.log.connect(lambda msg: logs.append(msg))

    with patch("workers.get_ffmpeg_location", return_value=None), \
         patch("workers.yt_dlp.YoutubeDL", side_effect=_make_ydl_factory(probe_info, download)):
        worker.run()

    return errors, logs


def write_final(worker, name="My Video.mp4"):
    """後処理フックが最終ファイルを報告した状態を再現する"""
    path = os.path.join(worker._work_dir, name)
    open(path, "w").close()
    worker._final_filepath = path


class RunErrorHandlingTest(unittest.TestCase):
    """run()が失敗した際、キャンセル・ネットワーク切断・その他のいずれの原因でも
    作業フォルダごと未完成ファイルを必ず削除すること、およびネットワーク切断時は分かりやすい
    日本語メッセージへ変換されることを検証する"""

    def _run_with(self, tmp, download_side_effect, worker=None):
        worker = worker or make_worker(out_dir=tmp)
        return run_worker(worker, download_side_effect)

    def test_network_error_removes_work_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors, _ = self._run_with(tmp, urllib.error.URLError("no route to host"))
            self.assertEqual(len(errors), 1)
            self.assertEqual(os.listdir(tmp), [])

    def test_error_is_reported_even_if_work_dir_cannot_be_removed(self):
        """作業フォルダを消せなくても(ウイルス対策ソフトによるロック等)、finished_errorは必ず送る。
        送られないとUIがダウンロード中のまま固まる"""
        with tempfile.TemporaryDirectory() as tmp, \
             patch("workers.shutil.rmtree"), \
             patch("workers.log_debug") as log_mock:
            errors, _ = self._run_with(tmp, RuntimeError("unsupported format"))
        self.assertEqual(errors, ["unsupported format"])
        self.assertTrue(any("作業フォルダを削除できませんでした" in c.args[0] for c in log_mock.call_args_list))

    def test_network_error_message_is_user_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors, _ = self._run_with(tmp, TimeoutError("timed out"))
            self.assertEqual(len(errors), 1)
            self.assertIn("ネットワーク接続が切断されたため", errors[0])

    def test_non_network_error_still_removes_work_dir(self):
        """ネットワーク切断に限らず、失敗理由を問わず未完成ファイルを削除する"""
        with tempfile.TemporaryDirectory() as tmp:
            errors, _ = self._run_with(tmp, RuntimeError("unsupported format"))
            self.assertEqual(os.listdir(tmp), [])
            self.assertEqual(errors, ["unsupported format"])

    def test_cancelled_download_removes_work_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            worker._is_cancelled = True
            errors, _ = self._run_with(
                tmp, yt_dlp.utils.DownloadError("ユーザーによりキャンセルされました"), worker=worker,
            )
            self.assertEqual(os.listdir(tmp), [])
            self.assertEqual(errors, [CANCELLED])

    def test_cancellation_takes_priority_over_network_wording(self):
        """キャンセル中に(たまたま)ネットワーク系の例外が飛んできても、
        ネットワーク切断の失敗としてではなくキャンセルとして通知する"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)

            def cancel_then_time_out(urls):
                worker.cancel()
                raise TimeoutError("timed out")

            errors, _ = self._run_with(tmp, cancel_then_time_out, worker=worker)
            self.assertEqual(errors, [CANCELLED])

    def test_cancel_during_probe_stops_before_download(self):
        """情報取得の通信中は止められないが、終わった時点でダウンロードを始めずに止める"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            worker.cancel()
            download = MagicMock()
            errors, _ = self._run_with(tmp, download, worker=worker)
            self.assertEqual(errors, [CANCELLED])
            download.assert_not_called()
            self.assertEqual(os.listdir(tmp), [])

    def test_cancel_during_clip_trim_removes_untrimmed_file(self):
        """切り抜き中のキャンセルでは、要求範囲になっていない動画全体を完成品として残さない"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, clip_start=10.0, clip_end=20.0)
            with patch("workers.trim_clip", side_effect=ClipCancelledError()):
                errors, _ = self._run_with(tmp, lambda urls: write_final(worker), worker=worker)
            self.assertEqual(errors, [CANCELLED])
            self.assertEqual(os.listdir(tmp), [])

    def test_failure_records_traceback_in_log(self):
        with tempfile.TemporaryDirectory() as tmp, patch("workers.log_debug") as log_mock:
            self._run_with(tmp, RuntimeError("unsupported format"))
        self.assertTrue(any("Traceback" in call.args[0] for call in log_mock.call_args_list))

    def test_cancel_after_download_completes_keeps_final_file(self):
        """後処理中にキャンセルするとdownload()は例外を投げずに正常終了し、
        完成した最終ファイルが出来ている。これは消さずに保存先へ移す
        (未完成の中間ファイルだけを削除する)"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)

            def complete_then_cancel(urls):
                write_final(worker)
                worker._is_cancelled = True

            errors, logs = self._run_with(tmp, complete_then_cancel, worker=worker)

            self.assertEqual(errors, [CANCELLED])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])
            self.assertTrue(any("完成済みのファイルは残しました" in log for log in logs))

    def test_postprocess_failure_is_a_complete_failure(self):
        """本編は完成し、サムネイル埋め込み等の後処理だけが失敗した場合も、要求どおりの
        ファイルにはなっていないため完全な失敗とみなし、保存先には何も残さない"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)

            def complete_then_fail(urls):
                write_final(worker)
                raise RuntimeError("embed thumbnail failed")

            errors, _ = self._run_with(tmp, complete_then_fail, worker=worker)

            self.assertEqual(errors, ["embed thumbnail failed"])
            self.assertEqual(os.listdir(tmp), [])

    def test_cancel_during_postprocess_discards_file(self):
        """後処理の途中で止まった(例外で抜けた)場合も、要求どおりのファイルではないため残さない"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)

            def complete_then_cancel_midway(urls):
                write_final(worker)
                worker.cancel()
                raise yt_dlp.utils.DownloadError("ユーザーによりキャンセルされました")

            errors, _ = self._run_with(tmp, complete_then_cancel_midway, worker=worker)

            self.assertEqual(errors, [CANCELLED])
            self.assertEqual(os.listdir(tmp), [])

    def test_failure_never_touches_files_already_in_out_dir(self):
        """保存先にもともとある同名のファイルは、完成品も未完成ファイルも消さない
        (作業フォルダの外には一切触れない)"""
        with tempfile.TemporaryDirectory() as tmp:
            names = ["My Video.mp3", "My Video.mp4", "My Video.mp4.part", "My Video.f137.mp4"]
            for name in names:
                open(os.path.join(tmp, name), "w").close()

            errors, _ = self._run_with(tmp, RuntimeError("unsupported format"))

            self.assertEqual(errors, ["unsupported format"])
            self.assertEqual(sorted(os.listdir(tmp)), sorted(names))


class RunSuccessTest(unittest.TestCase):
    def _run(self, worker, download_side_effect):
        finished = []
        worker.finished_ok.connect(finished.append)
        errors, logs = run_worker(worker, download_side_effect)
        self.saved_paths = finished
        return bool(finished), errors, logs

    def test_completed_file_is_moved_to_out_dir_and_work_dir_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            ok, errors, logs = self._run(worker, lambda urls: write_final(worker))
            self.assertTrue(ok)
            self.assertEqual(errors, [])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])
            # 画面から選択表示できるよう、保存先のパスを通知する
            self.assertEqual(self.saved_paths, [os.path.join(tmp, "My Video.mp4")])
            self.assertTrue(any(log.startswith(f"保存先: {os.path.join(tmp, 'My Video.mp4')}") for log in logs))

    def test_existing_file_with_same_name_gets_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "My Video.mp4"), "w").close()
            worker = make_worker(out_dir=tmp)
            ok, _, _ = self._run(worker, lambda urls: write_final(worker))
            self.assertTrue(ok)
            self.assertEqual(sorted(os.listdir(tmp)), ["My Video (1).mp4", "My Video.mp4"])

    def test_file_is_found_without_postprocessor_report(self):
        """後処理が1つも走らない形式では最終ファイルが報告されないため、作業フォルダから探す"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)

            def complete(urls):
                os.remove(os.path.join(worker._work_dir, "My Video.mp4.part"))
                open(os.path.join(worker._work_dir, "My Video.mp4"), "w").close()

            ok, _, _ = self._run(worker, complete)
            self.assertTrue(ok)
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])

    def test_failure_after_delivery_does_not_move_delivered_file_again(self):
        """保存先へ移した後に失敗しても、移した完成品を "(1)" 付きへ移し直さない"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            with patch("workers.format_size", side_effect=RuntimeError("boom")):
                ok, errors, _ = self._run(worker, lambda urls: write_final(worker))
            self.assertFalse(ok)
            self.assertEqual(errors, ["boom"])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])

    def test_missing_output_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp)
            ok, errors, _ = self._run(worker, lambda urls: None)
            self.assertFalse(ok)
            self.assertEqual(len(errors), 1)
            self.assertEqual(os.listdir(tmp), [])


class ParallelSameTitleTest(unittest.TestCase):
    """タイトルが同じ別々の動画を同じ保存先へ同時にダウンロードしても、互いのファイルを
    上書き・削除しない。もう一方のダウンロード全体を、こちらのダウンロード中に実行して再現する"""

    def test_both_succeed_with_distinct_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = make_worker(out_dir=tmp, url="https://example.com/watch?v=a")
            second = make_worker(out_dir=tmp, url="https://example.com/watch?v=b")

            def first_download(urls):
                run_worker(second, lambda urls: write_final(second))
                write_final(first)

            errors, _ = run_worker(first, first_download)

            self.assertEqual(errors, [])
            self.assertEqual(sorted(os.listdir(tmp)), ["My Video (1).mp4", "My Video.mp4"])

    def test_failure_of_one_keeps_the_others_completed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = make_worker(out_dir=tmp, url="https://example.com/watch?v=a")
            second = make_worker(out_dir=tmp, url="https://example.com/watch?v=b")

            def first_download(urls):
                run_worker(second, lambda urls: write_final(second))
                raise RuntimeError("network down")

            errors, _ = run_worker(first, first_download)

            self.assertEqual(errors, ["network down"])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])

    def test_failure_of_one_keeps_the_others_work_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = make_worker(out_dir=tmp, url="https://example.com/watch?v=a")
            second = make_worker(out_dir=tmp, url="https://example.com/watch?v=b")
            observed = {}

            def first_download(urls):
                run_worker(second, RuntimeError("network down"))
                observed["part"] = os.listdir(first._work_dir)
                write_final(first)

            errors, _ = run_worker(first, first_download)

            self.assertEqual(errors, [])
            self.assertEqual(observed["part"], ["My Video.mp4.part"])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])


class FetchImageBytesTest(unittest.TestCase):
    """サムネイル等のURLは動画サイトの応答に由来するため、ローカル資源の読み出しや
    巨大な応答の読み込みを防ぐ"""

    def test_rejects_non_http_schemes(self):
        for url in ("file:///etc/passwd", "ftp://example.com/a.jpg", "data:image/png;base64,AA=="):
            with self.subTest(url=url), patch("workers.urllib.request.urlopen") as urlopen, \
                    self.assertRaises(ValueError):
                fetch_image_bytes(url, 5)
            urlopen.assert_not_called()

    def test_rejects_responses_over_limit(self):
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.read.side_effect = lambda n: b"x" * n
        with patch("workers.urllib.request.urlopen", return_value=resp), self.assertRaises(ValueError):
            fetch_image_bytes("https://example.com/a.jpg", 5, max_bytes=10)

    def test_returns_data_within_limit(self):
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = b"image"
        with patch("workers.urllib.request.urlopen", return_value=resp):
            self.assertEqual(fetch_image_bytes("HTTPS://example.com/a.jpg", 5, max_bytes=10), b"image")


class ProgressEstimateTest(unittest.TestCase):
    def test_component_progress_is_capped_when_estimate_is_exceeded(self):
        """推定サイズを実際の受信量が上回っても、次のコンポーネントの分まで進んだ表示にしない"""
        worker = make_worker()
        worker._component_ids = ["137", "140"]
        worker._component_weights = [0.5, 0.5]
        progress = []
        worker.progress.connect(lambda percent, _text: progress.append(percent))
        worker._on_component_downloading({
            "info_dict": {"format_id": "137"}, "downloaded_bytes": 300, "total_bytes_estimate": 100,
        })
        self.assertEqual(progress, [50.0])


class DownloadRequestTest(unittest.TestCase):
    def test_has_clip(self):
        base = dict(url="u", out_dir="o", format_spec="b")
        self.assertFalse(DownloadRequest(**base).has_clip)
        self.assertTrue(DownloadRequest(**base, clip_start=1.0).has_clip)
        self.assertTrue(DownloadRequest(**base, clip_end=2.0).has_clip)


class BaseYdlOptsTest(unittest.TestCase):
    def test_minimal(self):
        self.assertEqual(
            _base_ydl_opts(),
            {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "color": "no_color"},
        )

    def test_optional_keys(self):
        opts = _base_ydl_opts(["res"], "C:/ffmpeg")
        self.assertEqual(opts["format_sort"], ["res"])
        self.assertEqual(opts["ffmpeg_location"], "C:/ffmpeg")


if __name__ == "__main__":
    unittest.main()


def _live_ydl_factory(normal_info, from_start_info=None, from_start_error=None):
    """live_from_startの有無で結果を変えるYoutubeDLの代わり。生成時のoptsを記録する"""
    created_opts = []

    def factory(opts):
        created_opts.append(opts)
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.__exit__.return_value = False
        if opts.get("live_from_start"):
            if from_start_error is not None:
                ydl.extract_info.side_effect = from_start_error
            else:
                ydl.extract_info.return_value = from_start_info
        else:
            ydl.extract_info.return_value = normal_info
        return ydl

    return factory, created_opts


class FormatListWorkerLiveTest(unittest.TestCase):
    URL = "https://www.youtube.com/live/abc"

    def _run(self, factory):
        results, errors = [], []
        worker = FormatListWorker(self.URL)
        worker.finished_ok.connect(lambda *args: results.append(args))
        worker.finished_error.connect(errors.append)
        with patch("workers.get_ffmpeg_location", return_value=None), \
             patch("workers.yt_dlp.YoutubeDL", side_effect=factory):
            worker.run()
        return results, errors

    def test_normal_video_has_no_live_info_and_is_extracted_once(self):
        factory, created = _live_ydl_factory({"title": "Video", "duration": 60, "formats": []})
        results, _ = self._run(factory)
        self.assertIsNone(results[0][4])
        self.assertEqual(len(created), 1)

    def test_live_that_can_be_recorded_from_start(self):
        normal = {"title": "Stream 2026-10-02 19:06", "live_status": "is_live",
                  "release_timestamp": 1790928042, "formats": []}
        factory, created = _live_ydl_factory(normal, from_start_info={"title": "Stream", "formats": []})
        results, _ = self._run(factory)
        _, title, _, duration, live_info = results[0]
        self.assertEqual(live_info, LiveInfo(started_at=1790928042, can_record_from_start=True))
        self.assertEqual(title, "Stream")
        self.assertIsNone(duration)
        self.assertTrue(created[1]["live_from_start"])

    def test_live_without_from_start_formats(self):
        normal = {"title": "Stream 2026-10-02 19:06", "live_status": "is_live",
                  "release_timestamp": 1790928042, "formats": []}
        error = yt_dlp.utils.ExtractorError("--live-from-start is passed, but there are no formats")
        factory, _ = _live_ydl_factory(normal, from_start_error=error)
        results, errors = self._run(factory)
        self.assertEqual(errors, [])
        _, title, _, _, live_info = results[0]
        self.assertEqual(live_info, LiveInfo(started_at=1790928042, can_record_from_start=False))
        self.assertEqual(title, "Stream")

    def test_upcoming_live_reports_japanese_message(self):
        factory, _ = _live_ydl_factory({"title": "Soon", "live_status": "is_upcoming", "formats": []})
        results, errors = self._run(factory)
        self.assertEqual(results, [])
        self.assertEqual(errors, [FormatListWorker.UPCOMING_LIVE_ERROR])

    def test_yt_dlp_upcoming_error_is_replaced_with_japanese_message(self):
        """配信開始前のライブでは、yt-dlpはinfoを返さずに英語のエラーを送出することが多い"""
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.__exit__.return_value = False
        ydl.extract_info.side_effect = yt_dlp.utils.ExtractorError("This live event will begin in 3 hours.")
        results, errors = self._run(lambda opts: ydl)
        self.assertEqual(errors, [FormatListWorker.UPCOMING_LIVE_ERROR])


class LiveFromStartProgressHookTest(unittest.TestCase):
    def _worker(self, component_count=2, started_at=None):
        worker = make_worker(live_mode="from_start")
        worker._start_live_state({
            "requested_formats": [{"format_id": "298"}, {"format_id": "140"}][:component_count],
            "release_timestamp": started_at,
        })
        return worker

    @staticmethod
    def _hook_info(fmt_id, index, count, downloaded=1000):
        return {
            "status": "downloading",
            "info_dict": {"format_id": fmt_id, "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
            "fragment_index": index,
            "fragment_count": count,
            "downloaded_bytes": downloaded,
        }

    def test_stop_raises_keyboard_interrupt_on_every_downloading_call(self):
        """yt-dlpはKeyboardInterruptをライブの正常な終わりとして扱い、結合まで行う。
        映像・音声の両スレッドを止めるため、1回きりではなく毎回送出する"""
        worker = self._worker()
        worker.cancel()
        for fmt_id in ("298", "140", "298"):
            with self.assertRaises(KeyboardInterrupt):
                worker._progress_hook(self._hook_info(fmt_id, 10, 4000))

    def test_stop_does_not_interrupt_finished_status(self):
        """結合前の後始末(finished)の報告までは止めない"""
        worker = self._worker()
        worker.cancel()
        worker._progress_hook({"status": "finished", "info_dict": {"format_id": "298"}, "filename": "x.mp4"})

    def test_normal_download_cancel_still_raises_download_error(self):
        worker = make_worker()
        worker.cancel()
        with self.assertRaises(yt_dlp.utils.DownloadError):
            worker._progress_hook({"status": "downloading"})

    def test_catch_up_progress_follows_slowest_component(self):
        worker = self._worker()
        events = []
        worker.progress.connect(lambda pct, text: events.append((pct, text)))
        worker._progress_hook(self._hook_info("140", 3000, 4000))
        worker._progress_hook(self._hook_info("298", 1000, 4000))
        self.assertEqual(events[-1], (25.0, "さかのぼり取得中 25%"))

    def test_after_catching_up_shows_recording_time_and_total_size(self):
        worker = self._worker(component_count=1, started_at=1000.0)
        events = []
        worker.progress.connect(lambda pct, text: events.append((pct, text)))
        with patch("workers.time.time", return_value=1000.0 + 754):
            worker._progress_hook(self._hook_info("298", 4000, 4000, downloaded=345 * 1024 * 1024))
        self.assertEqual(events[-1], (-1.0, "録画中 12:34 · 345.0MB"))

    def test_finished_component_ends_live_state_once_with_saving_text(self):
        worker = self._worker()
        states, events = [], []
        worker.live_state_changed.connect(states.append)
        worker.progress.connect(lambda pct, text: events.append(text))
        worker.cancel()
        for fmt_id in ("298", "140"):
            worker._progress_hook({"status": "finished", "info_dict": {"format_id": fmt_id}, "filename": "x"})
        self.assertEqual(states, [False])
        self.assertEqual(events, ["保存しています..."])

    def test_stream_end_shows_ended_text(self):
        worker = self._worker()
        events = []
        worker.progress.connect(lambda pct, text: events.append(text))
        worker._progress_hook({"status": "finished", "info_dict": {"format_id": "298"}, "filename": "x"})
        self.assertEqual(events, ["配信終了・保存しています..."])


class LiveFromStartRunTest(unittest.TestCase):
    def test_download_opts_request_live_from_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, live_mode="from_start")
            worker._file_stem = "Stream"
            worker._create_work_dir()
            self.addCleanup(worker._discard_work_dir)
            opts = worker._build_download_opts("mp4", None)
        self.assertTrue(opts["live_from_start"])
        self.assertNotIn("concurrent_fragment_downloads", opts)

    def test_stopped_recording_is_saved_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, live_mode="from_start")
            oks, states = [], []
            worker.finished_ok.connect(oks.append)
            worker.live_state_changed.connect(states.append)

            def download(urls):
                write_final(worker)
                worker.cancel()  # 録画中に停止ボタンが押された
                return 0

            errors, _ = run_worker(worker, download)

            self.assertEqual(errors, [])
            self.assertEqual(oks, [os.path.join(tmp, "My Video.mp4")])
            self.assertEqual(states, [True, False])
            self.assertEqual(os.listdir(tmp), ["My Video.mp4"])

    def test_stop_before_any_data_is_reported_as_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, live_mode="from_start")

            def download(urls):
                worker.cancel()  # .partしか無いうちに停止
                return 0

            errors, _ = run_worker(worker, download)
            self.assertEqual(errors, ["録画できたデータがありません"])
            self.assertEqual(os.listdir(tmp), [])

    def test_keyboard_interrupt_escaping_yt_dlp_is_reported_as_error(self):
        """yt-dlpがKeyboardInterruptをライブの終わりとして扱わずにそのまま送出した場合も、
        スレッドが例外で落ちず、失敗として知らせる"""
        with tempfile.TemporaryDirectory() as tmp:
            worker = make_worker(out_dir=tmp, live_mode="from_start")
            errors, _ = run_worker(worker, KeyboardInterrupt())
            self.assertEqual(errors, ["録画できたデータがありません"])
            self.assertEqual(os.listdir(tmp), [])
