"""動作を調整するための定数を config.json (実行ファイル/プロジェクト直下) から読み込む。

ファイルが存在しない場合や、一部のキーを欠く場合は既定値を使う。JSON構文エラー等で
読み込みに失敗した場合もアプリ全体を落とさず、すべて既定値にフォールバックする。
"""

import json
import os
from dataclasses import dataclass, field, fields

from paths import get_base_dir, log_debug

CONFIG_FILE_NAME = "config.json"


@dataclass
class AppConfig:
    # サムネイル取得(FormatListWorker)で試す候補URLの最大数
    thumbnail_max_candidates: int = 5
    # サムネイル1候補あたりの取得タイムアウト(秒)
    thumbnail_fetch_timeout_seconds: float = 5
    # クリップ範囲スライダーのプレビュー用ストーリーボード取得のタイムアウト(秒)
    storyboard_fetch_timeout_seconds: float = 10
    # URL入力後、自動でフォーマット取得を始めるまでのデバウンス時間(ミリ秒)
    info_fetch_debounce_ms: int = 700
    # 「音声のみ (mp3)」選択時のmp3変換ビットレート(kbps)
    mp3_quality: str = "192"
    # クリップ切り出し時、映像コーデック(先頭部分)ごとの再エンコード設定。
    # 値は [ffmpegエンコーダ名, CRF値] の組。表に無いコーデックはffmpegの既定設定にフォールバックする
    clip_video_encoder_by_codec_prefix: dict = field(default_factory=lambda: {
        "avc1": ["libx264", "18"],
        "h264": ["libx264", "18"],
        "vp9": ["libvpx-vp9", "31"],
        "vp09": ["libvpx-vp9", "31"],
        "vp8": ["libvpx", "10"],
        "vp08": ["libvpx", "10"],
    })


def _config_file_path() -> str:
    return os.path.join(get_base_dir(), CONFIG_FILE_NAME)


def load_config() -> AppConfig:
    config = AppConfig()
    path = _config_file_path()
    if not os.path.isfile(path):
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        log_debug(f"load_config: {path} の読み込みに失敗したため既定値を使用します ({e!r})")
        return config

    if not isinstance(data, dict):
        log_debug(f"load_config: {path} の内容がオブジェクトではないため既定値を使用します")
        return config

    valid_keys = {f.name for f in fields(AppConfig)}
    for key, value in data.items():
        if key not in valid_keys:
            log_debug(f"load_config: 未知の設定キーを無視しました ({key!r})")
            continue
        current = getattr(config, key)
        if isinstance(current, dict) and isinstance(value, dict):
            # 辞書型の設定は、指定されたキーだけ上書きし残りは既定値のまま保つ
            merged = dict(current)
            merged.update(value)
            setattr(config, key, merged)
        else:
            setattr(config, key, value)

    return config


# アプリ起動時に一度だけ読み込む(実行中にconfig.jsonを書き換えても反映されるのは次回起動時)
CONFIG = load_config()
