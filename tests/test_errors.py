"""errors.py のネットワークエラー判定とユーザー向けメッセージ組み立ての単体テスト"""

import http.client
import os
import ssl
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yt_dlp
from yt_dlp.networking.exceptions import TransportError

from errors import describe_error, is_network_error


class DescribeErrorTest(unittest.TestCase):
    def test_network_error_includes_action_and_detail(self):
        message = describe_error(urllib.error.URLError("no route to host"), "テスト処理")
        self.assertIn("ネットワーク接続が切断されたため、テスト処理を中断しました", message)
        self.assertIn("no route to host", message)

    def test_non_network_error_returns_str(self):
        self.assertEqual(describe_error(ValueError("bad value"), "テスト処理"), "bad value")


class IsNetworkErrorTest(unittest.TestCase):
    """is_network_error()が、直接の例外型だけでなくyt-dlpがラップし直した
    DownloadError等の原因チェーンも辿ってネットワーク関連の例外を検出できることを確認する"""

    def test_url_error_is_network_error(self):
        self.assertTrue(is_network_error(urllib.error.URLError("no route to host")))

    def test_socket_timeout_is_network_error(self):
        self.assertTrue(is_network_error(TimeoutError("timed out")))

    def test_connection_reset_is_network_error(self):
        self.assertTrue(is_network_error(ConnectionResetError("接続がリセットされました")))

    def test_ssl_error_is_network_error(self):
        self.assertTrue(is_network_error(ssl.SSLError("decryption failed")))

    def test_incomplete_read_is_network_error(self):
        self.assertTrue(is_network_error(http.client.IncompleteRead(b"")))

    def test_yt_dlp_transport_error_is_network_error(self):
        self.assertTrue(is_network_error(TransportError("connection closed")))

    def test_unrelated_error_is_not_network_error(self):
        self.assertFalse(is_network_error(ValueError("invalid format spec")))

    def test_broken_pipe_is_not_network_error(self):
        """BrokenPipeErrorはConnectionErrorのサブクラスだが、ffmpeg等のサブプロセスとの
        パイプが切れた場合(ディスク容量不足等、ネットワークと無関係のローカル要因)でも
        発生するため、ネットワークエラーとは判定しないことを確認する"""
        self.assertFalse(is_network_error(BrokenPipeError("pipe closed")))

    def test_http_error_is_not_network_error(self):
        """HTTPErrorはURLErrorのサブクラスだが、サーバーから正常にHTTP応答が
        返ってきた場合(404/403/429等)であり、ネットワーク切断とは別の問題のため
        ネットワークエラーとは判定しないことを確認する"""
        http_error = urllib.error.HTTPError("http://example.com", 429, "Too Many Requests", {}, None)
        self.assertFalse(is_network_error(http_error))

    def test_download_error_wrapping_url_error_via_implicit_context(self):
        """yt-dlpはexcept節の中でDownloadErrorを送出するため、明示的なfromが
        無くても__context__に元のネットワーク例外が残ることを再現する"""
        try:
            try:
                raise urllib.error.URLError("getaddrinfo failed")
            except urllib.error.URLError:
                # 暗黙の例外チェーン(__context__)を辿れることを確かめるため、あえてfromを付けない
                raise yt_dlp.utils.DownloadError("Unable to download webpage")  # noqa: B904
        except yt_dlp.utils.DownloadError as wrapped:
            self.assertTrue(is_network_error(wrapped))

    def test_download_error_wrapping_via_explicit_cause(self):
        original = TimeoutError("timed out")
        wrapped = yt_dlp.utils.DownloadError("timed out while downloading")
        wrapped.__cause__ = original
        self.assertTrue(is_network_error(wrapped))

    def test_download_error_wrapping_via_exc_info_attribute(self):
        """DownloadErrorはsys.exc_info()由来のexc_infoタプルも保持するため、
        暗黙の例外チェーンが失われるケースに備えてそちらも辿る"""
        original = ConnectionResetError("接続がリセットされました")
        wrapped = yt_dlp.utils.DownloadError("failed", exc_info=(type(original), original, None))
        self.assertTrue(is_network_error(wrapped))

    def test_download_error_without_network_cause_is_not_network_error(self):
        wrapped = yt_dlp.utils.DownloadError("Unsupported URL")
        self.assertFalse(is_network_error(wrapped))

    def test_does_not_infinite_loop_on_self_referential_chain(self):
        """__context__が自分自身を指すような壊れた例外チェーンでも無限ループしない"""
        exc = ValueError("boom")
        exc.__context__ = exc
        self.assertFalse(is_network_error(exc))


if __name__ == "__main__":
    unittest.main()
