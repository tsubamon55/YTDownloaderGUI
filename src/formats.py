"""フォーマット選択肢の定義と、フォーマット情報の整形ロジック"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from PyQt6.QtCore import Qt

# yt-dlpが返すフォーマット/動画情報の辞書。キーは動的なためdictのまま扱う
Format = dict[str, Any]


def has_video(fmt: Format) -> bool:
    """映像ストリームを含むか(vcodecが未設定・"none"なら含まない)"""
    vcodec = fmt.get("vcodec")
    return bool(vcodec and vcodec != "none")


def has_audio(fmt: Format) -> bool:
    """音声ストリームを含むか(acodecが未設定・"none"なら含まない)"""
    acodec = fmt.get("acodec")
    return bool(acodec and acodec != "none")


def format_filesize(fmt: Format) -> int | None:
    """確定サイズ(filesize)、無ければ推定サイズ(filesize_approx)。どちらも不明ならNone"""
    return fmt.get("filesize") or fmt.get("filesize_approx") or None


# 解像度を最優先しつつ、同じ解像度の中では最も互換性の高いコーデック(h264/aac)を選ぶ
BEST_QUALITY_COMPATIBLE_SORT = ["res", "codec:avc:m4a"]

# 音声ビットレートを最優先しつつ、同じビットレートの中では最も互換性の高いコーデック(aac/m4a)を選ぶ
BEST_AUDIO_COMPATIBLE_SORT = ["abr", "acodec:m4a"]


class FormatKey(Enum):
    """自動設定の「形式」の識別子。処理の分岐はラベルではなくこれで行う"""

    VIDEO_BEST_MP4 = "video_best_mp4"
    VIDEO_BEST = "video_best"
    AUDIO_BEST_M4A = "audio_best_m4a"
    AUDIO_BEST = "audio_best"
    AUDIO_MP3 = "audio_mp3"


@dataclass(frozen=True)
class FormatOption:
    """自動設定の「形式」コンボの1項目。labelは画面表示専用で、処理の判定にはkeyを使う"""

    key: FormatKey
    label: str
    # 「互換重視」であることをラベルに詰め込まず、ホバー時のツールチップで補足する
    tooltip: str
    spec: str
    sort: list[str] | None = None
    # 音声トラックだけを取り出す後処理(FFmpegExtractAudio)の変換先。Noneなら後処理なし
    extract_audio_codec: str | None = None
    # 最高画質が1080pを超える場合に、ダウンロード前に確認ダイアログを出すか
    confirm_high_resolution: bool = False


FORMAT_OPTIONS: tuple[FormatOption, ...] = (
    FormatOption(
        key=FormatKey.VIDEO_BEST_MP4,
        label="動画 (最高画質 mp4)",
        tooltip="【推奨】 互換性重視でmp4に限定します。動画によっては本来の最高画質(webm/av1等)より画質が下がる場合があります。",
        # ext=mp4/m4aだけではコーデックまでは保証されない(高解像度ではYouTubeがH.264を提供せず、
        # VP9がmp4コンテナのHLSバリアントとして出てくることがある)ため、
        # H.264(avc1)・AAC(mp4a)であることも明示的に条件にする
        spec=(
            "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]"
            "/bv*[ext=mp4][vcodec^=avc1]+ba*[acodec^=mp4a]"
            "/b[ext=mp4][vcodec^=avc1]"
            "/b"
        ),
        confirm_high_resolution=True,
    ),
    FormatOption(
        key=FormatKey.VIDEO_BEST,
        label="動画 (最高画質)",
        tooltip="コンテナ・コーデックを問わず本来の最高画質を選びます。webm/av1等になる場合があり、再生環境によっては再生できないことがあります。",
        spec="bv*+ba/b",
        sort=BEST_QUALITY_COMPATIBLE_SORT,
        confirm_high_resolution=True,
    ),
    FormatOption(
        key=FormatKey.AUDIO_BEST_M4A,
        label="音声のみ (最高音質 m4a)",
        tooltip="【推奨】 互換性重視でm4aに限定します。再エンコードは行いません。",
        spec="ba[ext=m4a]/ba[acodec^=mp4a]/ba",
        # 音声のみに限定できない場合の"ba"フォールバックで動画結合フォーマットが
        # 選ばれてしまう事態に備え、常に音声トラックのみを取り出す後処理を付ける
        # (対象が既に音声のみ・良コーデックならffmpegは何もせずスキップする)
        extract_audio_codec="best",
    ),
    FormatOption(
        key=FormatKey.AUDIO_BEST,
        label="音声のみ (最高音質)",
        tooltip="コーデックを問わず本来の最高音質を選びます。opus等になる場合があり、再生環境によっては再生できないことがあります。",
        spec="ba/b",
        sort=BEST_AUDIO_COMPATIBLE_SORT,
        # "ba"に一致するフォーマットが無い場合の"/b"フォールバックで動画結合
        # フォーマットが選ばれてしまう事態に備え、音声トラックのみを取り出す
        extract_audio_codec="best",
    ),
    FormatOption(
        key=FormatKey.AUDIO_MP3,
        label="音声のみ (mp3)",
        tooltip="再生互換性は最も高い形式ですが、非可逆で192kbpsに変換されるためm4a版より音質は劣化します。",
        spec="ba/b",
        extract_audio_codec="mp3",
    ),
)

_OPTIONS_BY_LABEL = {option.label: option for option in FORMAT_OPTIONS}
_OPTIONS_BY_KEY = {option.key: option for option in FORMAT_OPTIONS}


def find_format_option(label: str) -> FormatOption | None:
    """コンボに表示しているラベルから形式を引く(未知のラベルならNone)"""
    return _OPTIONS_BY_LABEL.get(label)


def format_option(key: FormatKey) -> FormatOption:
    return _OPTIONS_BY_KEY[key]


def format_spec_1080p(format_label: str, portrait: bool) -> str:
    """自動設定の「最高画質」系オプションで、1080p相当に画質を制限する代替セレクタを作る。

    縦型動画はwidth/heightがlandscapeと逆転する(例: 1080pの縦動画は1080x1920)。
    yt-dlpのフォーマットフィルタは width/height を独立に比較するだけで
    「width>=heightなら横型」のような向き判定はできないため
    (例: [width<=1920][height<=1080] は縦型480p相当の608x1080も素通りしてしまう)、
    呼び出し側で実際に選ばれた最高画質フォーマットの向きを判定してから、
    横長/縦長どちらの上限を使うかをここで決める。
    """
    width_cap, height_cap = (1080, 1920) if portrait else (1920, 1080)
    # 最後の"/b"は本当に候補が皆無だった場合の最終手段であり、解像度上限を守れないため、
    # その手前に「コンテナ/コーデック条件は緩めるが上限は維持する」段階を挟んでおく
    option = find_format_option(format_label)
    if option is not None and option.key is FormatKey.VIDEO_BEST_MP4:
        return (
            f"bv*[ext=mp4][vcodec^=avc1][width<={width_cap}][height<={height_cap}]+ba[ext=m4a]"
            f"/bv*[ext=mp4][vcodec^=avc1][width<={width_cap}][height<={height_cap}]+ba*[acodec^=mp4a]"
            f"/b[ext=mp4][vcodec^=avc1][width<={width_cap}][height<={height_cap}]"
            f"/b[width<={width_cap}][height<={height_cap}]"
            "/b"
        )
    return (
        f"bv*[width<={width_cap}][height<={height_cap}]+ba"
        f"/b[width<={width_cap}][height<={height_cap}]"
        "/b"
    )

def format_size(num_bytes) -> str:
    if not num_bytes:
        return "不明"
    mb = num_bytes / (1024 * 1024)
    # 単位の切り替えは表示丸めの後の値で判定する。丸める前の値で判定すると、
    # 1024MB未満でも小数第1位への丸めで繰り上がる境界(1023.95MiB以上)が
    # "1024.0MB"という桁のおかしい表示になってしまう
    if round(mb, 1) >= 1024:
        return f"{mb / 1024:.2f}GB"
    return f"{mb:.1f}MB"


FORMAT_COLUMN_LABELS = ["ID", "形式", "種別", "画質/音質", "fps", "コーデック", "配信", "サイズ", "備考"]
FORMAT_COLUMN_WIDTHS = [65, 55, 70, 90, 55, 75, 60, 70, 150]
FORMAT_ROW_HEIGHT = 26

FORMAT_COLUMN_ROLE = Qt.ItemDataRole.UserRole + 1
FORMAT_MISMATCH_ROLE = Qt.ItemDataRole.UserRole + 2

# コーデックIDの先頭部分(ドット区切りの最初)からユーザーに分かりやすい名称への対応表。
# 例: "avc1.640028" -> "avc1" -> "H.264"
CODEC_LABELS = {
    "avc1": "H.264",
    "h264": "H.264",
    "av01": "AV1",
    "vp9": "VP9",
    "vp09": "VP9",
    "vp8": "VP8",
    "mp4a": "AAC",
    "aac": "AAC",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "ac-3": "AC3",
    "ec-3": "EAC3",
    "flac": "FLAC",
    "alac": "ALAC",
}


def codec_prefix(codec: str | None) -> str:
    if not codec or codec == "none":
        return ""
    return codec.split(".")[0].lower()


def _codec_label(codec: str | None) -> str:
    prefix = codec_prefix(codec)
    if not prefix:
        return ""
    return CODEC_LABELS.get(prefix, prefix.upper())


def format_codec(fmt: dict) -> str:
    """映像/音声コーデックの短いラベル。同じ解像度/fps/配信方式でも
    コーデックが違えば別物(例: H.264 vs AV1)なので見分けられるようにする"""
    labels = [_codec_label(fmt.get("vcodec")), _codec_label(fmt.get("acodec"))]
    return "+".join(label for label in labels if label)


# コンテナが実質的に前提としているコーデックの組み合わせ。YouTubeでは高解像度で
# H.264のmp4が存在しない場合に、webm版VP9をそのままmp4タグのHLSバリアントとして
# 配信することがあり、サイズ不明・進捗不正確な上に一部の再生環境(Apple製品や
# 簡易ハードウェアプレイヤー等)では再生できないことがある「見た目だけmp4」になる。
_MISMATCHED_VCODEC_BY_EXT = {
    "mp4": ("vp9", "vp09", "vp8", "vp08"),
    "webm": ("avc1", "h264"),
}
_MISMATCHED_ACODEC_BY_EXT = {
    "mp4": ("opus", "vorbis"),
    "m4a": ("opus", "vorbis"),
    "webm": ("mp4a", "aac"),
}


def is_codec_container_mismatch(fmt: dict) -> bool:
    """コンテナ(ext)と実際のコーデックが一致しない、実質的に劣化コピーでしかない
    フォーマットかどうかを判定する"""
    ext = fmt.get("ext")
    vcodec_prefix = codec_prefix(fmt.get("vcodec"))
    acodec_prefix = codec_prefix(fmt.get("acodec"))

    if vcodec_prefix and vcodec_prefix in _MISMATCHED_VCODEC_BY_EXT.get(ext, ()):
        return True
    if acodec_prefix and acodec_prefix in _MISMATCHED_ACODEC_BY_EXT.get(ext, ()):
        return True
    return False


def filter_mismatched_formats(formats: list[dict]) -> list[dict]:
    """自動設定の選択候補からコンテナ/コーデック不一致のフォーマットを完全に除外する"""
    return [f for f in formats if not is_codec_container_mismatch(f)]


def format_protocol(fmt: dict) -> str:
    """配信方式を表す短いラベル。HLS(m3u8)配信はContent-Lengthが分からずサイズが
    不明になりやすいなど、進捗表示の挙動に関わるためユーザーに区別できるようにする"""
    protocol = fmt.get("protocol") or ""
    if "m3u8" in protocol:
        return "HLS"
    if "dash" in protocol:
        return "DASH"
    if protocol == "https":
        return "HTTPS"
    if protocol == "http":
        return "HTTP"
    return protocol


# 同じ解像度・コーデックのHLS/DASH版はHTTPS/HTTP版よりファイルサイズ不明・進捗不正確になりやすいため、
# 一覧の並び順ではHTTPS/HTTP版を優先する(自動選択側はyt-dlp自身のproto優先度で既に対応済み)
_PROTOCOL_RANK = {"HTTPS": 2, "HTTP": 2, "DASH": 1, "HLS": 0}


def protocol_rank(fmt: dict) -> int:
    return _PROTOCOL_RANK.get(format_protocol(fmt), 1)


def format_columns(fmt: Format) -> list[str]:
    format_id = fmt.get("format_id", "?")
    ext = fmt.get("ext", "?")
    video = has_video(fmt)
    audio = has_audio(fmt)

    if video and audio:
        kind = "動画+音声"
    elif video:
        kind = "動画のみ"
    elif audio:
        kind = "音声のみ"
    else:
        kind = "不明"

    quality_text = ""
    fps_text = ""
    if video:
        resolution = fmt.get("resolution") or (
            f"{fmt.get('width')}x{fmt.get('height')}" if fmt.get("height") else None
        )
        quality_text = resolution or ""
        fps = fmt.get("fps")
        if fps:
            fps_display = int(fps) if float(fps).is_integer() else fps
            fps_text = f"{fps_display}fps"
    elif audio:
        abr = fmt.get("abr")
        quality_text = f"{abr:.0f}kbps" if abr else ""

    size = format_size(format_filesize(fmt))
    note = fmt.get("format_note") or ""
    if is_codec_container_mismatch(fmt):
        note = f"⚠非推奨 {note}".strip()

    return [f"[{format_id}]", ext, kind, quality_text, fps_text, format_codec(fmt), format_protocol(fmt), size, note]


def describe_format_plain(fmt: dict) -> str:
    return " | ".join(c for c in format_columns(fmt) if c)
