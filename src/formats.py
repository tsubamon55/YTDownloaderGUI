"""フォーマット選択肢の定義と、フォーマット情報の整形ロジック"""

from PyQt6.QtCore import Qt

FORMAT_OPTIONS = {
    "動画 (最高画質 mp4)": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
    "動画 (最高画質)": "bv*+ba/b",
    "音声のみ (mp3)": "audio_mp3",
    "音声のみ (最高音質)": "audio_best",
}

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


FORMAT_COLUMN_LABELS = ["ID", "形式", "種別", "画質/音質", "fps", "サイズ", "備考"]
FORMAT_COLUMN_WIDTHS = [65, 55, 70, 90, 55, 70, 150]
FORMAT_ROW_HEIGHT = 26

FORMAT_COLUMN_ROLE = Qt.ItemDataRole.UserRole + 1


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
        info2 = f"{fps}fps" if fps else ""
    elif has_audio:
        abr = fmt.get("abr")
        info1 = f"{abr:.0f}kbps" if abr else ""

    size = format_size(fmt.get("filesize") or fmt.get("filesize_approx"))
    note = fmt.get("format_note") or ""

    return [f"[{format_id}]", ext, kind, info1, info2, size, note]


def describe_format_plain(fmt: dict) -> str:
    return " | ".join(c for c in format_columns(fmt) if c)
