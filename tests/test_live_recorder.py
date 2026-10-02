"""live_recorder.py(今からのライブ録画をffmpegで行う処理)の単体テスト。

ffmpegの代わりに、同じ形の-progress出力を書いて標準入力の"q"で終わるPythonスクリプトを
実際に子プロセスとして動かし、停止の流れ(q → 終了、応答がなければ強制終了)を確かめる"""

import os
import sys
import textwrap
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import live_recorder
from live_recorder import (
    LiveInput,
    LiveRecorder,
    RecordingProgress,
    apply_progress_line,
    build_record_command,
    build_remux_command,
    live_inputs,
    recording_extension,
)

# -progress pipe:1 と同じ形で進捗を書き、標準入力に"q"が来たら終了コード0で終わる偽のffmpeg
FAKE_FFMPEG_OBEYS_Q = textwrap.dedent("""
    import sys, threading, time
    def report():
        n = 0
        while True:
            n += 1
            print(f"total_size={n * 1000}", flush=True)
            print(f"out_time_us={n * 1000000}", flush=True)
            print("progress=continue", flush=True)
            time.sleep(0.05)
    threading.Thread(target=report, daemon=True).start()
    while sys.stdin.read(1) != "q":
        pass
    print("progress=end", flush=True)
""")

# 標準入力を無視して止まらない偽のffmpeg(強制終了の確認用)
FAKE_FFMPEG_IGNORES_Q = textwrap.dedent("""
    import time
    print("progress=continue", flush=True)
    time.sleep(60)
""")

# 配信が終わったときのように、進捗を数回書いて自分から終わる偽のffmpeg
FAKE_FFMPEG_STREAM_ENDS = textwrap.dedent("""
    for n in range(1, 4):
        print(f"total_size={n * 1000}", flush=True)
        print(f"out_time_us={n * 2000000}", flush=True)
        print("progress=continue", flush=True)
    print("progress=end", flush=True)
""")


def _recorder(script, should_stop, progress_events):
    recorder = LiveRecorder(
        None, [LiveInput("https://example.com/v.m3u8", {})], "unused.ts",
        on_progress=lambda p: progress_events.append((p.seconds, p.size_bytes)),
        should_stop=should_stop,
    )
    recorder._executable = sys.executable
    return recorder, patch.object(live_recorder, "build_record_command",
                                  return_value=[sys.executable, "-c", script])


class LiveInputsTest(unittest.TestCase):
    def test_separate_video_and_audio(self):
        info = {"requested_formats": [
            {"url": "https://v.m3u8", "http_headers": {"User-Agent": "UA"}},
            {"url": "https://a.m3u8"},
        ]}
        self.assertEqual(live_inputs(info), [
            LiveInput("https://v.m3u8", {"User-Agent": "UA"}), LiveInput("https://a.m3u8", {}),
        ])

    def test_single_muxed_format(self):
        self.assertEqual(live_inputs({"url": "https://av.m3u8"}), [LiveInput("https://av.m3u8", {})])


class BuildRecordCommandTest(unittest.TestCase):
    def test_two_inputs_with_headers_are_mapped_and_copied_to_mpegts(self):
        cmd = build_record_command("ffmpeg", [
            LiveInput("https://v.m3u8", {"User-Agent": "UA", "Referer": "https://youtube.com"}),
            LiveInput("https://a.m3u8", {}),
        ], "/work/rec.ts")
        self.assertEqual(cmd, [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y",
            "-headers", "User-Agent: UA\r\nReferer: https://youtube.com\r\n", "-i", "https://v.m3u8",
            "-i", "https://a.m3u8",
            "-map", "0", "-map", "1", "-c", "copy", "-f", "mpegts", "/work/rec.ts",
        ])


class ApplyProgressLineTest(unittest.TestCase):
    def test_reads_size_and_time_and_detects_block_end(self):
        progress = RecordingProgress()
        self.assertFalse(apply_progress_line(progress, "total_size=2097152\n"))
        self.assertFalse(apply_progress_line(progress, "out_time_us=12500000\n"))
        self.assertTrue(apply_progress_line(progress, "progress=continue\n"))
        self.assertEqual((progress.seconds, progress.size_bytes), (12.5, 2097152))

    def test_ignores_not_available_values(self):
        progress = RecordingProgress(seconds=3.0, size_bytes=10)
        apply_progress_line(progress, "out_time_us=N/A\n")
        apply_progress_line(progress, "total_size=N/A\n")
        self.assertEqual((progress.seconds, progress.size_bytes), (3.0, 10))


class LiveRecorderRecordTest(unittest.TestCase):
    def test_stop_sends_q_and_returns_exit_code(self):
        events = []
        started = time.monotonic()
        recorder, command_patch = _recorder(FAKE_FFMPEG_OBEYS_Q, lambda: len(events) >= 2, events)
        with command_patch:
            returncode = recorder.record()
        self.assertEqual(returncode, 0)
        self.assertGreaterEqual(len(events), 2)
        self.assertLess(time.monotonic() - started, 10)

    def test_ffmpeg_ignoring_q_is_killed_after_timeout(self):
        events = []
        recorder, command_patch = _recorder(FAKE_FFMPEG_IGNORES_Q, lambda: True, events)
        with command_patch, patch.object(LiveRecorder, "STOP_TIMEOUT_SECONDS", 0.5):
            started = time.monotonic()
            returncode = recorder.record()
        self.assertNotEqual(returncode, 0)
        self.assertLess(time.monotonic() - started, 10)

    def test_stream_end_finishes_without_stop(self):
        events = []
        recorder, command_patch = _recorder(FAKE_FFMPEG_STREAM_ENDS, lambda: False, events)
        with command_patch:
            returncode = recorder.record()
        self.assertEqual(returncode, 0)
        self.assertEqual(events[-1], (6.0, 3000))


class RemuxTest(unittest.TestCase):
    def test_extension_by_audio_codec(self):
        self.assertEqual(recording_extension(None), "mp4")
        self.assertEqual(recording_extension("best"), "m4a")
        self.assertEqual(recording_extension("mp3"), "mp3")

    def test_video_is_copied_to_mp4(self):
        self.assertEqual(build_remux_command("ffmpeg", "r.ts", "T.mp4", None), [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "r.ts",
            "-map", "0", "-c", "copy", "-movflags", "+faststart", "T.mp4",
        ])

    def test_audio_only_keeps_audio_without_reencoding(self):
        self.assertEqual(build_remux_command("ffmpeg", "r.ts", "T.m4a", "best"), [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "r.ts",
            "-map", "0:a", "-c", "copy", "-movflags", "+faststart", "T.m4a",
        ])

    def test_mp3_is_encoded_with_configured_bitrate(self):
        self.assertEqual(build_remux_command("ffmpeg", "r.ts", "T.mp3", "mp3"), [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "r.ts",
            "-map", "0:a", "-c:a", "libmp3lame", "-b:a", "192k", "T.mp3",
        ])


class LiveRecorderStopRaceTest(unittest.TestCase):
    def test_stop_after_ffmpeg_already_exited_does_not_break_cleanup(self):
        """停止を押したのと同時にffmpegが終わった(配信終了・通信断)場合、"q"の書き込みが失敗する。
        書けなかった"q"が残ったままだと、Popenの後片付けで標準入力を閉じる際に再び書こうとして
        BrokenPipeErrorになり、録画全体が失敗扱いになる"""
        import subprocess

        from yt_dlp.utils import Popen

        recorder = LiveRecorder(None, [], "unused.ts", on_progress=lambda p: None, should_stop=lambda: True)
        with Popen([sys.executable, "-c", "pass"], text=True,
                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            proc.wait()
            recorder._stop(proc)


if __name__ == "__main__":
    unittest.main()
