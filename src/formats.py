"""フォーマット選択肢の定義と、フォーマット情報の整形ロジック"""

from PyQt6.QtCore import Qt

FORMAT_OPTIONS = {
    # ext=mp4/m4aだけではコーデックまでは保証されない(高解像度ではYouTubeがH.264を提供せず、
    # VP9がmp4コンテナのHLSバリアントとして出てくることがある)ため、
    # H.264(avc1)・AAC(mp4a)であることも明示的に条件にする
    "動画 (最高画質 mp4)": (
        "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]"
        "/bv*[ext=mp4][vcodec^=avc1]+ba*[acodec^=mp4a]"
        "/b[ext=mp4][vcodec^=avc1]"
        "/b"
    ),
    "動画 (最高画質)": "bv*+ba/b",
    "音声のみ (最高音質 m4a)": "audio_m4a",
    "音声のみ (最高音質)": "audio_best",
    "音声のみ (mp3)": "audio_mp3",
}

# 「互換重視」であることをラベルに詰め込まず、ホバー時のツールチップで補足するための対応表
FORMAT_OPTION_TOOLTIPS = {
    "動画 (最高画質 mp4)": "【推奨】 互換性重視でmp4に限定します。動画によっては本来の最高画質(webm/av1等)より画質が下がる場合があります。",
    "動画 (最高画質)": "コンテナ・コーデックを問わず本来の最高画質を選びます。webm/av1等になる場合があり、再生環境によっては再生できないことがあります。",
    "音声のみ (最高音質 m4a)": "【推奨】 互換性重視でm4aに限定します。再エンコードは行いません。",
    "音声のみ (最高音質)": "コーデックを問わず本来の最高音質を選びます。opus等になる場合があり、再生環境によっては再生できないことがあります。",
    "音声のみ (mp3)": "再生互換性は最も高い形式ですが、非可逆で192kbpsに変換されるためm4a版より音質は劣化します。",
}

def format_spec_1080p(format_label: str, portrait: bool) -> str:
    """簡易設定の「最高画質」系オプションで、1080p相当に画質を制限する代替セレクタを作る。

    縦型動画はwidth/heightがlandscapeと逆転する(例: 1080pの縦動画は1080x1920)。
    yt-dlpのフォーマットフィルタは width/height を独立に比較するだけで
    「width>=heightなら横型」のような向き判定はできないため
    (例: [width<=1920][height<=1080] は縦型480p相当の608x1080も素通りしてしまう)、
    呼び出し側で実際に選ばれた最高画質フォーマットの向きを判定してから、
    横長/縦長どちらの上限を使うかをここで決める。
    """
    width_cap, height_cap = (1080, 1920) if portrait else (1920, 1080)
    if format_label == "動画 (最高画質 mp4)":
        return (
            f"bv*[ext=mp4][vcodec^=avc1][width<={width_cap}][height<={height_cap}]+ba[ext=m4a]"
            f"/bv*[ext=mp4][vcodec^=avc1][width<={width_cap}][height<={height_cap}]+ba*[acodec^=mp4a]"
            f"/b[ext=mp4][vcodec^=avc1][width<={width_cap}][height<={height_cap}]"
            "/b"
        )
    return (
        f"bv*[width<={width_cap}][height<={height_cap}]+ba"
        f"/b[width<={width_cap}][height<={height_cap}]"
        "/b"
    )

# 簡易設定で解像度確認ダイアログの対象となる「最高画質」系オプション
HIGH_RESOLUTION_CHECK_LABELS = ("動画 (最高画質 mp4)", "動画 (最高画質)")

# 解像度を最優先しつつ、同じ解像度の中では最も互換性の高いコーデック(h264/aac)を選ぶ
BEST_QUALITY_COMPATIBLE_SORT = ["res", "codec:avc:m4a"]

# 音声ビットレートを最優先しつつ、同じビットレートの中では最も互換性の高いコーデック(aac/m4a)を選ぶ
BEST_AUDIO_COMPATIBLE_SORT = ["abr", "acodec:m4a"]


def format_size(num_bytes) -> str:
    if not num_bytes:
        return "不明"
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f}GB"
    return f"{mb:.1f}MB"


FORMAT_COLUMN_LABELS = ["ID", "形式", "種別", "画質/音質", "fps", "コーデック", "配信", "サイズ", "備考"]
FORMAT_COLUMN_WIDTHS = [65, 55, 70, 90, 55, 75, 60, 70, 150]
FORMAT_ROW_HEIGHT = 26

FORMAT_COLUMN_ROLE = Qt.ItemDataRole.UserRole + 1

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


def _codec_prefix(codec: str | None) -> str:
    if not codec or codec == "none":
        return ""
    return codec.split(".")[0].lower()


def _codec_label(codec: str | None) -> str:
    prefix = _codec_prefix(codec)
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
    vcodec_prefix = _codec_prefix(fmt.get("vcodec"))
    acodec_prefix = _codec_prefix(fmt.get("acodec"))

    if vcodec_prefix and vcodec_prefix in _MISMATCHED_VCODEC_BY_EXT.get(ext, ()):
        return True
    if acodec_prefix and acodec_prefix in _MISMATCHED_ACODEC_BY_EXT.get(ext, ()):
        return True
    return False


def filter_mismatched_formats(formats: list[dict]) -> list[dict]:
    """簡易設定の選択候補からコンテナ/コーデック不一致のフォーマットを完全に除外する"""
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


def format_columns(fmt: dict) -> list[str]:
    format_id = fmt.get("format_id", "?")
    ext = fmt.get("ext", "?")
    vcodec = fmt.get("vcodec", "none")
    acodec = fmt.get("acodec", "none")
    has_video = bool(vcodec and vcodec != "none")
    has_audio = bool(acodec and acodec != "none")

    if has_video and has_audio:
        kind = "動画+音声"
    elif has_video:
        kind = "動画のみ"
    elif has_audio:
        kind = "音声のみ"
    else:
        kind = "不明"

    info1 = ""
    info2 = ""
    if has_video:
        resolution = fmt.get("resolution") or (
            f"{fmt.get('width')}x{fmt.get('height')}" if fmt.get("height") else None
        )
        info1 = resolution or ""
        fps = fmt.get("fps")
        if fps:
            fps_display = int(fps) if float(fps).is_integer() else fps
            info2 = f"{fps_display}fps"
        else:
            info2 = ""
    elif has_audio:
        abr = fmt.get("abr")
        info1 = f"{abr:.0f}kbps" if abr else ""

    size = format_size(fmt.get("filesize") or fmt.get("filesize_approx"))
    note = fmt.get("format_note") or ""
    if is_codec_container_mismatch(fmt):
        note = f"⚠非推奨 {note}".strip()

    return [f"[{format_id}]", ext, kind, info1, info2, format_codec(fmt), format_protocol(fmt), size, note]


def describe_format_plain(fmt: dict) -> str:
    return " | ".join(c for c in format_columns(fmt) if c)
