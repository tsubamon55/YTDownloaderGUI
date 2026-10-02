"""ダウンロード範囲(開始・終了時刻)の解析・検証を行う純粋関数群。

QtやUIに依存しないため、単体テストがそのまま実行できる。
"""

import math
import re

# 時・分の欄は整数のみ、最後(秒)の欄だけ小数を許す。float()に任せると"1_0"(=10)・
# 全角数字・"1e3"なども受理してしまい、入力ミスがそのまま別の時刻として通ってしまうため、
# ASCIIの数字と小数点だけを明示的に受け付ける
_INTEGER_PART = re.compile(r"[0-9]+")
_SECONDS_PART = re.compile(r"[0-9]+(?:\.[0-9]*)?|\.[0-9]+")


def parse_clip_time(text: str) -> float | None:
    """"HH:MM:SS" / "MM:SS" / "SS" 形式の文字列を秒数に変換する。

    前後の空白のみ、または空文字列の場合は範囲指定なしとしてNoneを返す。
    形式が不正な場合はValueErrorを送出する。
    """
    stripped = text.strip()
    if not stripped:
        return None

    parts = [part.strip() for part in stripped.split(":")]
    if len(parts) > 3:
        raise ValueError(f"時刻の形式が正しくありません: {text}")
    *upper_parts, seconds_part = parts
    if not all(_INTEGER_PART.fullmatch(part) for part in upper_parts) or not _SECONDS_PART.fullmatch(seconds_part):
        raise ValueError(f"時刻の形式が正しくありません: {text}")
    numbers = [float(part) for part in parts]

    # 先頭の欄は繰り上げずに書けるよう上限を設けない("90:00"は90分、"90"は90秒)が、
    # 2番目以降の分・秒の欄が60以上なのは書き間違いとみなす("1:75"等)
    if any(n >= 60 for n in numbers[1:]):
        raise ValueError(f"時刻の形式が正しくありません: {text}")

    # 桁数の多すぎる数字はfloat()で無限大になる。素通りさせるとformat_clip_timeのround()が
    # OverflowErrorで落ち、動画長との比較も意味をなさないため、有限値であることを確かめる
    if any(not math.isfinite(n) for n in numbers):
        raise ValueError(f"時刻の形式が正しくありません: {text}")

    seconds = 0.0
    for n in numbers:
        seconds = seconds * 60 + n
    # 各要素が有限でも、時・分の繰り上げで合計が無限大に溢れることがある("1e308:0:0"等)
    if not math.isfinite(seconds):
        raise ValueError(f"時刻の形式が正しくありません: {text}")
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


def clip_range_label(start: float | None, end: float | None) -> str | None:
    """クリップ範囲(開始・終了時刻)をファイル名に添える短いラベルにする。

    フル動画(start/endとも指定なし)と保存先が重複しないよう、ダウンロード時の
    ファイル名にこのラベルを付記して区別する。範囲指定がない場合はNoneを返す。
    開始/終了が未指定(先頭から/末尾まで)の側は、動画の長さを知らなくても
    表せるよう「2:33-」(2:33から末尾まで)「-2:33」(先頭から2:33まで)のように
    範囲記法の慣習(Pythonのスライス等)に倣い、その側を単に空にする。
    """
    if start is None and end is None:
        return None
    start_label = _format_label_time(start) if start is not None else ""
    end_label = _format_label_time(end) if end is not None else ""
    return f"{start_label}-{end_label}"


def _format_label_time(seconds: float) -> str:
    """ファイル名用の時刻表記。format_clip_timeは秒単位に丸めるため、"1:00.6"から
    切り抜いたファイルが"1:01"と実際と異なる範囲を名乗らないよう、端数はミリ秒まで残す"""
    millis = round(seconds * 1000)
    whole, fraction = divmod(millis, 1000)
    label = format_clip_time(whole)
    if fraction:
        label += f".{fraction:03d}".rstrip("0")
    return label


def format_clip_digits(digits: str) -> str:
    """数字だけの文字列を、末尾から2桁ずつ区切って"HH:MM:SS"形式にする
    (電卓・ストップウォッチ入力のように、新しく打った数字は常に末尾(秒側)に
    積み上がっていく右詰め方式)。7桁目以降は最も古い(先頭の)桁があふれて消える。

    例: "130" -> "1:30" (1分30秒), "10203" -> "1:02:03" (1時間2分3秒)
    """
    if len(digits) > 6:
        digits = digits[-6:]
    if len(digits) <= 2:
        return digits
    if len(digits) <= 4:
        return f"{digits[:-2]}:{digits[-2:]}"
    return f"{digits[:-4]}:{digits[-4:-2]}:{digits[-2:]}"


def auto_format_clip_input(text: str) -> str:
    """クリップ範囲欄の現在のテキストから、右詰め方式で振り直したコロン区切りの
    表示を返す。数字とコロンだけで構成されている場合にのみ働き、そうでない場合
    (端数秒を指定する"."など、自分で細かく書式を制御したい入力)はそのまま返す。
    テキスト全体から数字だけを毎回抜き出して組み直すため、新しく数字を打った場合も
    バックスペースで消した場合も、常に「末尾に数字が積み上がる」動作で一貫する。
    """
    if any(ch not in "0123456789:" for ch in text):
        return text
    digits = text.replace(":", "")
    return format_clip_digits(digits)


def resolve_clip_range(start_text: str, end_text: str) -> tuple[float | None, float | None]:
    """開始・終了時刻のテキストを検証し、(start_seconds, end_seconds) を返す。

    どちらも空欄の場合はクリップなしとして (None, None) を返す。
    終了時刻が開始時刻以下の場合はValueErrorを送出する。開始時刻が未指定(先頭から)の
    場合は0として比較するため、例えば開始欄が空欄のまま終了時刻に"0"を指定した場合も
    (長さ0の無意味なクリップになってしまうため)エラーになる。
    """
    start = parse_clip_time(start_text)
    end = parse_clip_time(end_text)
    if end is not None and end <= (start if start is not None else 0.0):
        raise ValueError("終了時刻は開始時刻より後にしてください")
    return start, end
