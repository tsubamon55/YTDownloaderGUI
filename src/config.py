"""動作を調整するための定数を config.json (実行ファイル/プロジェクト直下) から読み込む。

ファイルが存在しない場合や、一部のキーを欠く場合は既定値を使う。JSON構文エラー等で
読み込みに失敗した場合もアプリ全体を落とさず、すべて既定値にフォールバックする。
キーは正しいが値の型が既定値と異なる場合や、値が許される範囲外(タイムアウト0秒・
負の件数・NaN等)の場合も、そのキーだけ既定値のままにする
(不正な値をそのまま通すと、下流で原因の分からないエラーになるため)。
"""

import json
import math
from dataclasses import dataclass, field, fields
from typing import get_args, get_origin

from paths import find_bundled_file, get_base_dir, log_debug

CONFIG_FILE_NAME = "config.json"


def _setting(default, minimum=None, *, exclusive=False):
    """AppConfigの数値設定。minimum以上(exclusive=Trueなら超)でなければ既定値を使う"""
    return field(default=default, metadata={"minimum": minimum, "exclusive": exclusive})


@dataclass
class AppConfig:
    # サムネイル取得(FormatListWorker)で試す候補URLの最大数
    thumbnail_max_candidates: int = _setting(5, minimum=1)
    # サムネイル1候補あたりの取得タイムアウト(秒)
    thumbnail_fetch_timeout_seconds: float = _setting(5, minimum=0, exclusive=True)
    # クリップ範囲スライダーのプレビュー用ストーリーボード取得のタイムアウト(秒)
    storyboard_fetch_timeout_seconds: float = _setting(10, minimum=0, exclusive=True)
    # URL入力後、自動でフォーマット取得を始めるまでのデバウンス時間(ミリ秒)
    info_fetch_debounce_ms: int = _setting(700, minimum=0)
    # 「音声のみ (mp3)」選択時のmp3変換ビットレート(kbps)
    mp3_quality: str = "192"
    # クリップ切り出し時、本編映像のコーデック(ffprobeのcodec_name)ごとの再エンコード設定。
    # 値は [ffmpegエンコーダ名, CRF値] の組。表に無いコーデックは出力コンテナに合わせて
    # h264(webmはvp9)の設定で再エンコードする(clip_trimmer参照)
    clip_video_encoder_by_codec_prefix: dict[str, list[str]] = field(default_factory=lambda: {
        "h264": ["libx264", "18"],
        "vp9": ["libvpx-vp9", "31"],
        "vp8": ["libvpx", "10"],
    })
    # 起動時にGitHub Releasesへ新バージョンの有無を問い合わせるかどうか。
    # ソースから実行している開発中はネットワーク越しの確認自体が不要なため、falseで無効化できる
    auto_update_enabled: bool = True
    # アップデート確認(GitHub API)・アップデート本体のダウンロード、それぞれ1回あたりのタイムアウト(秒)
    update_check_timeout_seconds: float = _setting(5, minimum=0, exclusive=True)


def _config_file_path() -> str | None:
    """config.jsonの実体パスを返す(どこにも無ければNone)"""
    return find_bundled_file(CONFIG_FILE_NAME)


def _validated_value(expected_type, current, value):
    """config.jsonから読んだ値を、AppConfigで宣言された型に合うか確かめたうえで返す。

    戻り値は (採用してよいか, 実際に設定する値)。キー名が正しく JSON としても正当でも、
    値の型が違えば(例: "thumbnail_max_candidates": "3")そのまま設定すると、下流の
    スライス操作や文字列結合で TypeError / AttributeError になり、config.jsonの型ミスが
    原因だと分からない無関係なエラー(「動画情報の取得に失敗しました」等)に化けてしまう。

    判定の基準に既定値の実際の型ではなく宣言された型を使うのは、`x: float = 5` のように
    既定値だけ整数で書かれている項目で、正当な小数の指定まで弾いてしまわないため。
    dict[str, list[str]] のような総称型の注釈は、元の型(dict)で判定する。
    """
    declared_type = expected_type
    expected_type = get_origin(expected_type) or expected_type
    # boolはintのサブクラスであり、件数や時間の設定として意図した値ではないため明示的に弾く
    if isinstance(value, bool) is not (expected_type is bool):
        return False, current

    if expected_type is float:
        # JSONに 5 と整数で書かれていても、秒数のような実数設定には受け入れる
        if isinstance(value, (int, float)):
            return True, float(value)
        return False, current

    if expected_type is dict and isinstance(value, dict):
        # 辞書型の設定は、指定されたキーだけ上書きし残りは既定値のまま保つ。
        # 型注釈(dict[str, list[str]]等)に合わない項目だけは取り込まない
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update({k: v for k, v in value.items() if _matches_type(k, v, declared_type)})
        return True, merged

    if isinstance(expected_type, type) and isinstance(value, expected_type):
        return True, value
    return False, current


def _matches_type(key, value, declared_type) -> bool:
    """dict[K, V]型の設定の1項目が、K・V(list[str]のような総称型なら要素の型も)に合うか"""
    args = get_args(declared_type)
    if len(args) != 2:
        return True
    key_type, value_type = args
    if not isinstance(key, key_type):
        return False
    origin = get_origin(value_type)
    if origin is None:
        return isinstance(value, value_type)
    if not isinstance(value, origin):
        return False
    item_types = get_args(value_type)
    return not item_types or all(isinstance(item, item_types[0]) for item in value)


def _within_range(value, metadata) -> bool:
    """数値設定がNaN/無限大でなく、宣言された下限を満たすか"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    if not math.isfinite(value):
        return False
    minimum = metadata.get("minimum")
    if minimum is None:
        return True
    return value > minimum if metadata.get("exclusive") else value >= minimum


def load_config() -> AppConfig:
    config = AppConfig()
    path = _config_file_path()
    if path is None:
        # config.jsonは常に同梱されるファイルなので、見つからない場合はビルド/配置の不備
        log_debug(f"load_config: {CONFIG_FILE_NAME} が見つからないため既定値を使用します (探索先: {get_base_dir()})")
        return config

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        log_debug(f"load_config: {path} の読み込みに失敗したため既定値を使用します ({e!r})")
        return config

    if not isinstance(data, dict):
        log_debug(f"load_config: {path} の内容がオブジェクトではないため既定値を使用します")
        return config

    config_fields = {f.name: f for f in fields(AppConfig)}
    for key, value in data.items():
        if key not in config_fields:
            log_debug(f"load_config: 未知の設定キーを無視しました ({key!r})")
            continue
        expected_type = config_fields[key].type
        accepted, resolved = _validated_value(expected_type, getattr(config, key), value)
        if not accepted:
            expected_name = getattr(expected_type, "__name__", expected_type)
            log_debug(
                f"load_config: 設定 {key!r} の値の型が不正なため既定値を使用します "
                f"(期待: {expected_name}, 実際: {type(value).__name__})"
            )
            continue
        if not _within_range(resolved, config_fields[key].metadata):
            log_debug(f"load_config: 設定 {key!r} の値が範囲外のため既定値を使用します ({value!r})")
            continue
        setattr(config, key, resolved)

    return config


# アプリ起動時に一度だけ読み込む(実行中にconfig.jsonを書き換えても反映されるのは次回起動時)
CONFIG = load_config()
