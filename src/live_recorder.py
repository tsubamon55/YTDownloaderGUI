"""「今から」のライブ録画(設計: docs/superpowers/specs/2026-10-02-youtube-live-recording-design.md)。

yt-dlpはライブのHLSをffmpegに渡して終わるまで待つだけで、進捗も途中での停止もできないため、
yt-dlpからは録画するURLだけを受け取り、ffmpegをここで直接動かす。録画中は、途中で途切れても
再生できるmpegts(.ts)に書き、終わってからmp4等へ入れ替える"""

import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from yt_dlp.utils import Popen

from clip_trimmer import FFmpegRunner
from config import CONFIG
from formats import Format
from paths import log_debug


@dataclass(frozen=True)
class LiveInput:
    """ffmpegに入力する1本の配信(YouTubeのライブは映像と音声が別々のことが多い)"""

    url: str
    http_headers: dict[str, str] = field(default_factory=dict)


def live_inputs(probe_info: Format) -> list[LiveInput]:
    """yt-dlpが選んだフォーマットから、ffmpegに入力する配信の一覧を作る"""
    formats = probe_info.get("requested_formats") or [probe_info]
    return [LiveInput(f["url"], dict(f.get("http_headers") or {})) for f in formats]


def build_record_command(ffmpeg: str, inputs: Sequence[LiveInput], output_path: str) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y"]
    for item in inputs:
        if item.http_headers:
            # YouTubeはyt-dlpが抽出時に使ったUser-Agent等と揃っていないと配信を返さないことがある
            cmd += ["-headers", "".join(f"{key}: {value}\r\n" for key, value in item.http_headers.items())]
        cmd += ["-i", item.url]
    for index in range(len(inputs)):
        cmd += ["-map", str(index)]
    cmd += ["-c", "copy", "-f", "mpegts", output_path]
    return cmd


@dataclass
class RecordingProgress:
    seconds: float = 0.0
    size_bytes: int = 0


def apply_progress_line(progress: RecordingProgress, line: str) -> bool:
    """ffmpegの-progress出力の1行を反映する。1回分の報告の区切り(progress=...)の行ならTrue。
    開始直後は値が"N/A"のことがあるため、数値でない値は無視して直前の値を保つ"""
    key, _, value = line.strip().partition("=")
    if key == "total_size" and value.isdigit():
        progress.size_bytes = int(value)
    elif key == "out_time_us" and value.isdigit():
        progress.seconds = int(value) / 1_000_000
    return key == "progress"


class LiveRecorder:
    """ffmpegで配信を録画する。配信が終わるか、should_stopがTrueを返すまで続ける"""

    # 標準入力に"q"を書いてから、ffmpegが書き終えて終了するのを待つ上限。
    # 通常は数秒で終わる(検証では約2秒)。超えたら強制終了する(.tsはそれまでの分が再生できる)
    STOP_TIMEOUT_SECONDS: float = 30
    _POLL_SECONDS = 0.2

    def __init__(
        self,
        ffmpeg_location: str | None,
        inputs: Sequence[LiveInput],
        output_path: str,
        on_progress: Callable[[RecordingProgress], None],
        should_stop: Callable[[], bool],
    ) -> None:
        self._executable = FFmpegRunner(ffmpeg_location).executable
        self._inputs = list(inputs)
        self._output_path = output_path
        self._on_progress = on_progress
        self._should_stop = should_stop
        self._stderr_tail: list[str] = []

    def record(self) -> int:
        """録画が終わるまで待ち、ffmpegの終了コードを返す"""
        if self._executable is None:
            raise FileNotFoundError("ffmpegが見つかりません")
        cmd = build_record_command(self._executable, self._inputs, self._output_path)
        # yt-dlpのPopenは、Windowsでコンソール窓を出さない設定とPyInstaller環境向けの環境変数の補正を行う
        with Popen(
            cmd, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as proc:
            # 標準エラーを読まずにいると、パイプが詰まってffmpegが止まるため、別のスレッドで読み続ける
            stderr_thread = threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True)
            stop_thread = threading.Thread(target=self._watch_stop, args=(proc,), daemon=True)
            stderr_thread.start()
            stop_thread.start()
            progress = RecordingProgress()
            assert proc.stdout is not None
            for line in proc.stdout:
                if apply_progress_line(progress, line):
                    self._on_progress(progress)
            returncode = proc.wait()
            stop_thread.join()
            stderr_thread.join()
        if returncode != 0:
            log_debug(f"LiveRecorder: ffmpegが終了コード{returncode}で終了 ({' / '.join(self._stderr_tail)})")
        return returncode

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            self._stderr_tail = [*self._stderr_tail[-4:], line.strip()]

    def _watch_stop(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None:
            if self._should_stop():
                self._stop(proc)
                return
            time.sleep(self._POLL_SECONDS)

    def _stop(self, proc: subprocess.Popen) -> None:
        """"q"でffmpegに書き終えて終わるよう頼む。強制終了すると最後の区切りが壊れることがあるため、
        応答がない場合に限る"""
        assert proc.stdin is not None
        try:
            proc.stdin.write("q")
            proc.stdin.flush()
        except OSError:
            # 既に終わり際でパイプが閉じている
            return
        try:
            proc.wait(timeout=self.STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            log_debug(f"LiveRecorder: ffmpegが{self.STOP_TIMEOUT_SECONDS}秒以内に終了しないため強制終了します")
            proc.kill()


def recording_extension(extract_audio_codec: str | None) -> str:
    """録画を入れ替える先の拡張子。音声のみの形式では音声だけを取り出す"""
    if extract_audio_codec == "mp3":
        return "mp3"
    if extract_audio_codec is not None:
        # YouTubeのライブの音声はAACのため、再エンコードせずm4aに入れられる
        return "m4a"
    return "mp4"


def build_remux_command(ffmpeg: str, ts_path: str, output_path: str, extract_audio_codec: str | None) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", ts_path]
    if extract_audio_codec == "mp3":
        cmd += ["-map", "0:a", "-c:a", "libmp3lame", "-b:a", f"{CONFIG.mp3_quality}k"]
    elif extract_audio_codec is not None:
        cmd += ["-map", "0:a", "-c", "copy", "-movflags", "+faststart"]
    else:
        cmd += ["-map", "0", "-c", "copy", "-movflags", "+faststart"]
    cmd.append(output_path)
    return cmd


def remux_recording(ffmpeg_location: str | None, ts_path: str, output_path: str, extract_audio_codec: str | None) -> None:
    """録画した.tsを、選ばれた形式のファイルへ入れ替える(映像・音声とも再エンコードしない。mp3のみ変換)"""
    executable = FFmpegRunner(ffmpeg_location).executable
    if executable is None:
        raise FileNotFoundError("ffmpegが見つかりません")
    cmd = build_remux_command(executable, ts_path, output_path, extract_audio_codec)
    _, stderr, returncode = Popen.run(
        cmd, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if returncode != 0:
        lines = (stderr or "").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"ffmpegが終了コード{returncode}で失敗しました")
