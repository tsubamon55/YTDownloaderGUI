"""例外がネットワーク切断に由来するかの判定と、ユーザー向けエラーメッセージの組み立て"""

import http.client
import socket
import ssl
import urllib.error

from yt_dlp.networking.exceptions import CertificateVerifyError, TransportError

# ネットワーク切断・タイムアウト等を表す例外型。yt-dlpは内部でurllib/http.client/sslの
# 例外を捕まえてDownloadError等でラップし直すため、直接の型だけでなく原因チェーン
# (__cause__/__context__、およびyt-dlp独自のexc_info属性)も辿って判定する。
# ConnectionErrorは意図的に含めない。そのサブクラスのBrokenPipeErrorは、ffmpeg
# 等のサブプロセスとのパイプが切れた場合(ディスク容量不足やクラッシュ等、
# ネットワークとは無関係のローカル要因)でも発生するため、丸ごと含めると誤診断になる
_NETWORK_ERROR_TYPES = (
    urllib.error.URLError,
    socket.timeout,
    TimeoutError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    http.client.HTTPException,
    ssl.SSLError,
    TransportError,
)


# 証明書の検証失敗。接続自体はできており、原因は時計のずれ・プロキシやセキュリティソフトによる
# 通信の検査・サーバー側の設定など切断とは別のため、ネットワーク切断とは分けて案内する
_CERTIFICATE_ERROR_TYPES = (ssl.SSLCertVerificationError, CertificateVerifyError)


def _exception_chain(exc: BaseException):
    """例外と、その原因として連なる例外を重複なく列挙する"""
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        # yt_dlp.utils.DownloadErrorはsys.exc_info()のタプルを保持しており、
        # 暗黙の例外チェーン(__context__)が働かないケースがあるため明示的にも辿る
        exc_info = getattr(current, "exc_info", None)
        if exc_info and len(exc_info) > 1:
            pending.append(exc_info[1])
        # urllibのURLErrorは元の例外(ssl.SSLError等)を原因チェーンではなくreasonに持つ
        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException):
            pending.append(reason)
        pending.append(current.__cause__)
        pending.append(current.__context__)


def is_certificate_error(exc: BaseException) -> bool:
    """例外(またはその原因チェーン)にSSL証明書の検証失敗が含まれるかを調べる"""
    return any(isinstance(current, _CERTIFICATE_ERROR_TYPES) for current in _exception_chain(exc))


def is_network_error(exc: BaseException) -> bool:
    """例外(またはその原因チェーン)にネットワーク関連の例外が含まれるかを調べる"""
    for current in _exception_chain(exc):
        if isinstance(current, urllib.error.HTTPError):
            # HTTPErrorはURLErrorのサブクラスだが、これはサーバーから正常にHTTP応答が
            # 返ってきた場合(404/403/429等)であり、ネットワーク切断とは別の問題なので除外する
            continue
        if isinstance(current, _NETWORK_ERROR_TYPES):
            return True
    return False


def describe_error(exc: Exception, action: str) -> str:
    """例外からユーザー向けのエラーメッセージを組み立てる。ネットワーク切断が
    原因と判定できる場合は、原因を明示した文言にする(actionは「ダウンロード」
    「動画情報の取得」等、中断された処理を表す名詞)"""
    # str()が空になる例外(引数なしで送出されたもの等)では、何も書かれていないダイアログに
    # ならないよう例外の型名を代わりに出す
    detail = str(exc) or type(exc).__name__
    if is_certificate_error(exc):
        return (
            f"サーバーの証明書を検証できなかったため、{action}を中断しました。"
            "パソコンの日付・時刻が正しいか、プロキシやセキュリティソフトが通信を検査していないかを"
            f"確認してください。(詳細: {detail})"
        )
    if is_network_error(exc):
        return (
            f"ネットワーク接続が切断されたため、{action}を中断しました。"
            f"接続を確認してから再度お試しください。(詳細: {detail})"
        )
    return detail
