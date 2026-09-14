"""ダウンロード範囲(開始・終了時刻)の解析・検証を行う純粋関数群。

QtやUIに依存しないため、単体テストがそのまま実行できる。
"""


def parse_clip_time(text: str) -> float | None:
    """"HH:MM:SS" / "MM:SS" / "SS" 形式の文字列を秒数に変換する。

    前後の空白のみ、または空文字列の場合は範囲指定なしとしてNoneを返す。
    形式が不正な場合はValueErrorを送出する。
    """
    stripped = text.strip()
    if not stripped:
        return None

    parts = stripped.split(":")
    if len(parts) > 3:
        raise ValueError(f"時刻の形式が正しくありません: {text}")

    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        raise ValueError(f"時刻の形式が正しくありません: {text}") from None

    if any(n < 0 for n in numbers):
        raise ValueError(f"時刻の形式が正しくありません: {text}")

    seconds = 0.0
    for n in numbers:
        seconds = seconds * 60 + n
    return seconds


def format_clip_time(seconds: float) -> str:
    """秒数を "H:MM:SS" (1時間未満は "M:SS") 形式の文字列に変換する。
    スライダー操作の結果をテキスト入力欄へ反映する際に使う。"""
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def resolve_clip_range(start_text: str, end_text: str) -> tuple[float | None, float | None]:
    """開始・終了時刻のテキストを検証し、(start_seconds, end_seconds) を返す。

    どちらも空欄の場合はクリップなしとして (None, None) を返す。
    終了時刻が開始時刻以下の場合はValueErrorを送出する。
    """
    start = parse_clip_time(start_text)
    end = parse_clip_time(end_text)
    if start is not None and end is not None and end <= start:
        raise ValueError("終了時刻は開始時刻より後にしてください")
    return start, end
