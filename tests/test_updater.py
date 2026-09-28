"""updater.py(起動時のアップデート確認・ダウンロード・適用)の単体テスト。

実際のGitHub API通信・ネットワークダウンロード・インストーラー実行は行わず、
urllib.request.urlopenやsubprocess.Popen等をモックして検証する。
QThreadはworkers.py側のテストと同じく、.start()ではなく.run()を直接呼んで
同期的に実行する(イベントループ無しでも直接接続でシグナルが即時に届くため)。
"""

import os
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import updater
from updater import (
    UpdateCheckWorker,
    UpdateDownloadWorker,
    _parse_version,
    _select_asset,
    apply_downloaded_update,
    get_current_version,
    is_newer_version,
)


class ParseAndCompareVersionTest(unittest.TestCase):
    def test_parses_plain_dotted_version(self):
        self.assertEqual(_parse_version("1.2.10"), (1, 2, 10))

    def test_strips_leading_v_prefix(self):
        self.assertEqual(_parse_version("v1.2.10"), (1, 2, 10))

    def test_unparsable_string_falls_back_to_zero(self):
        self.assertEqual(_parse_version("not-a-version"), (0,))

    def test_newer_patch_version_is_detected(self):
        self.assertTrue(is_newer_version("v1.2.1", "1.2.0"))

    def test_equal_version_is_not_newer(self):
        self.assertFalse(is_newer_version("1.2.0", "v1.2.0"))

    def test_older_version_is_not_newer(self):
        self.assertFalse(is_newer_version("1.1.9", "1.2.0"))

    def test_different_length_compares_numerically_not_lexically(self):
        # 桁数が違っても文字列比較にならないこと(例: "1.10.0" と "1.9.9" の大小関係)
        self.assertTrue(is_newer_version("1.10.0", "1.9.9"))
        self.assertFalse(is_newer_version("1.2", "1.2.0"))


class SelectAssetTest(unittest.TestCase):
    def test_windows_prefers_asset_with_setup_in_name(self):
        assets = [
            {"name": "YTDownloaderGUI-debug.exe"},
            {"name": "YTDownloaderGUI-Setup-1.2.1.exe"},
            {"name": "YTDownloaderGUI-1.2.1.dmg"},
        ]
        with patch.object(updater.sys, "platform", "win32"):
            asset = _select_asset(assets)
        self.assertEqual(asset["name"], "YTDownloaderGUI-Setup-1.2.1.exe")

    def test_windows_falls_back_to_any_exe_without_setup_in_name(self):
        assets = [{"name": "YTDownloaderGUI-portable.exe"}]
        with patch.object(updater.sys, "platform", "win32"):
            asset = _select_asset(assets)
        self.assertEqual(asset["name"], "YTDownloaderGUI-portable.exe")

    def test_macos_selects_dmg(self):
        assets = [{"name": "YTDownloaderGUI-Setup-1.2.1.exe"}, {"name": "YTDownloaderGUI-1.2.1.dmg"}]
        with patch.object(updater.sys, "platform", "darwin"):
            asset = _select_asset(assets)
        self.assertEqual(asset["name"], "YTDownloaderGUI-1.2.1.dmg")

    def test_no_matching_asset_returns_none(self):
        assets = [{"name": "README.md"}]
        with patch.object(updater.sys, "platform", "win32"):
            self.assertIsNone(_select_asset(assets))

    def test_unsupported_platform_returns_none(self):
        with patch.object(updater.sys, "platform", "linux"):
            self.assertIsNone(_select_asset([{"name": "YTDownloaderGUI-Setup-1.2.1.exe"}]))


class GetCurrentVersionTest(unittest.TestCase):
    def test_reads_and_strips_bundled_version_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "VERSION")
            with open(path, "w", encoding="utf-8") as f:
                f.write("1.2.1\n")
            with patch.object(updater, "find_bundled_file", return_value=path):
                self.assertEqual(get_current_version(), "1.2.1")

    def test_missing_file_returns_none(self):
        with patch.object(updater, "find_bundled_file", return_value=None):
            self.assertIsNone(get_current_version())

    def test_unreadable_file_returns_none(self):
        with patch.object(updater, "find_bundled_file", return_value="C:/nope/VERSION"), \
             patch("builtins.open", side_effect=OSError("denied")):
            self.assertIsNone(get_current_version())

    def test_empty_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "VERSION")
            with open(path, "w", encoding="utf-8") as f:
                f.write("   \n")
            with patch.object(updater, "find_bundled_file", return_value=path):
                self.assertIsNone(get_current_version())


def _fake_response(payload_bytes=None, json_data=None, headers=None):
    """urllib.request.urlopen()の戻り値(with文で使うレスポンス)を模したMagicMockを作る"""
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.headers = headers or {}
    if json_data is not None:
        import json as json_module
        resp.read.return_value = json_module.dumps(json_data).encode("utf-8")
    else:
        resp.read.return_value = payload_bytes or b""
    return resp


class UpdateCheckWorkerTest(unittest.TestCase):
    def _run(self, urlopen_side_effect, current_version="1.2.0"):
        results = {"update_available": [], "up_to_date": [], "check_failed": []}
        worker = UpdateCheckWorker()
        worker.update_available.connect(lambda *a: results["update_available"].append(a))
        worker.up_to_date.connect(lambda: results["up_to_date"].append(True))
        worker.check_failed.connect(lambda msg: results["check_failed"].append(msg))

        with patch.object(updater, "get_current_version", return_value=current_version), \
             patch("updater.urllib.request.urlopen", side_effect=urlopen_side_effect):
            worker.run()
        return results

    def test_missing_local_version_skips_network_call_entirely(self):
        urlopen_mock = MagicMock()
        results = self._run(urlopen_mock, current_version=None)
        urlopen_mock.assert_not_called()
        self.assertEqual(len(results["up_to_date"]), 1)

    def test_404_is_treated_as_up_to_date(self):
        def raise_404(*args, **kwargs):
            raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

        results = self._run(raise_404)
        self.assertEqual(len(results["up_to_date"]), 1)
        self.assertEqual(results["check_failed"], [])

    def test_other_http_error_is_reported_as_check_failed(self):
        def raise_500(*args, **kwargs):
            raise urllib.error.HTTPError("url", 500, "Server Error", {}, None)

        results = self._run(raise_500)
        self.assertEqual(len(results["check_failed"]), 1)
        self.assertEqual(results["up_to_date"], [])

    def test_network_error_is_reported_as_check_failed(self):
        results = self._run(OSError("network down"))
        self.assertEqual(len(results["check_failed"]), 1)

    def test_same_or_older_remote_version_is_up_to_date(self):
        resp = _fake_response(json_data={"tag_name": "v1.2.0", "assets": []})
        results = self._run(lambda *a, **k: resp, current_version="1.2.0")
        self.assertEqual(len(results["up_to_date"]), 1)
        self.assertEqual(results["update_available"], [])

    def test_newer_version_without_matching_asset_is_up_to_date(self):
        resp = _fake_response(json_data={
            "tag_name": "v1.2.1",
            "assets": [{"name": "README.md", "browser_download_url": "https://example.com/README.md"}],
        })
        with patch.object(updater.sys, "platform", "win32"):
            results = self._run(lambda *a, **k: resp, current_version="1.2.0")
        self.assertEqual(len(results["up_to_date"]), 1)
        self.assertEqual(results["update_available"], [])

    def test_newer_version_with_matching_asset_emits_update_available(self):
        resp = _fake_response(json_data={
            "tag_name": "v1.2.1",
            "assets": [
                {"name": "YTDownloaderGUI-Setup-1.2.1.exe",
                 "browser_download_url": "https://example.com/Setup-1.2.1.exe"},
            ],
        })
        with patch.object(updater.sys, "platform", "win32"):
            results = self._run(lambda *a, **k: resp, current_version="1.2.0")

        self.assertEqual(len(results["update_available"]), 1)
        version, url, name = results["update_available"][0]
        self.assertEqual(version, "v1.2.1")
        self.assertEqual(url, "https://example.com/Setup-1.2.1.exe")
        self.assertEqual(name, "YTDownloaderGUI-Setup-1.2.1.exe")


class UpdateDownloadWorkerTest(unittest.TestCase):
    def test_successful_download_writes_file_and_emits_finished_ok(self):
        chunks = [b"a" * 10, b"b" * 10, b""]
        resp = _fake_response(headers={"Content-Length": "20"})
        resp.read.side_effect = chunks

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "sub", "Setup.exe")
            worker = UpdateDownloadWorker("https://example.com/Setup.exe", dest)
            results = {"ok": [], "error": [], "progress": []}
            worker.finished_ok.connect(lambda p: results["ok"].append(p))
            worker.finished_error.connect(lambda m: results["error"].append(m))
            worker.progress.connect(lambda p: results["progress"].append(p))

            with patch("updater.urllib.request.urlopen", return_value=resp):
                worker.run()

            self.assertEqual(results["ok"], [dest])
            self.assertEqual(results["error"], [])
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"a" * 10 + b"b" * 10)
            self.assertEqual(results["progress"], [50.0, 100.0])

    def test_truncated_download_is_reported_as_error_and_cleans_up(self):
        resp = _fake_response(headers={"Content-Length": "20"})
        resp.read.side_effect = [b"a" * 10, b""]  # 宣言サイズ20に対し10バイトで打ち切り

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "Setup.exe")
            worker = UpdateDownloadWorker("https://example.com/Setup.exe", dest)
            results = {"ok": [], "error": []}
            worker.finished_ok.connect(lambda p: results["ok"].append(p))
            worker.finished_error.connect(lambda m: results["error"].append(m))

            with patch("updater.urllib.request.urlopen", return_value=resp):
                worker.run()

            self.assertEqual(results["ok"], [])
            self.assertEqual(len(results["error"]), 1)
            self.assertFalse(os.path.isfile(dest))

    def test_cancel_during_download_stops_and_cleans_up(self):
        resp = _fake_response(headers={"Content-Length": "20"})

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "Setup.exe")
            worker = UpdateDownloadWorker("https://example.com/Setup.exe", dest)

            def fake_read(size):
                worker.cancel()
                return b"a" * 10

            resp.read.side_effect = fake_read
            results = {"ok": [], "error": []}
            worker.finished_ok.connect(lambda p: results["ok"].append(p))
            worker.finished_error.connect(lambda m: results["error"].append(m))

            with patch("updater.urllib.request.urlopen", return_value=resp):
                worker.run()

            self.assertEqual(results["error"], ["キャンセルされました"])
            self.assertFalse(os.path.isfile(dest))

    def test_network_failure_is_reported_and_cleans_up_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "Setup.exe")
            # 途中まで書きかけの状態を模しておき、失敗時に削除されることを確認する
            with open(dest, "wb") as f:
                f.write(b"partial")

            worker = UpdateDownloadWorker("https://example.com/Setup.exe", dest)
            results = {"error": []}
            worker.finished_error.connect(lambda m: results["error"].append(m))

            with patch("updater.urllib.request.urlopen", side_effect=OSError("network down")):
                worker.run()

            self.assertEqual(len(results["error"]), 1)
            self.assertFalse(os.path.isfile(dest))


class ApplyDownloadedUpdateTest(unittest.TestCase):
    def test_windows_launches_detached_silent_installer(self):
        with patch.object(updater.sys, "platform", "win32"), \
             patch.object(updater.subprocess, "Popen") as popen_mock:
            apply_downloaded_update("C:/tmp/Setup.exe")

        args, kwargs = popen_mock.call_args
        self.assertEqual(args[0], ["C:/tmp/Setup.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        # アプリ終了後もインストーラーが生き残るよう、プロセスグループを切り離していること
        self.assertTrue(kwargs.get("close_fds"))

    def test_unsupported_platform_raises(self):
        with patch.object(updater.sys, "platform", "linux"):
            with self.assertRaises(RuntimeError):
                apply_downloaded_update("/tmp/whatever")

    def test_macos_bundle_path_is_derived_from_executable(self):
        fake_exe = "/Applications/YTDownloaderGUI.app/Contents/MacOS/YTDownloaderGUI"
        with patch.object(updater.sys, "platform", "darwin"), \
             patch.object(updater.sys, "executable", fake_exe), \
             patch.object(updater.os, "access", return_value=True), \
             patch.object(updater.subprocess, "Popen") as popen_mock, \
             patch("builtins.open", MagicMock()), \
             patch.object(updater.os, "makedirs"):
            apply_downloaded_update("/tmp/Update.dmg")

        args, kwargs = popen_mock.call_args
        command = args[0]
        self.assertEqual(command[0], "/bin/sh")
        self.assertIn("/tmp/Update.dmg", command)
        # os.path.dirname/abspathはこのテストを実行しているOSの区切り文字で正規化するため
        # (Windows上では"/Applications/..."が"C:\\Applications\\..."になる)、区切り文字に
        # 依存しないバンドル名の一致だけを確認する
        self.assertTrue(any(part.endswith("YTDownloaderGUI.app") for part in command))

    def test_macos_raises_when_not_running_from_a_bundle(self):
        with patch.object(updater.sys, "platform", "darwin"), \
             patch.object(updater.sys, "executable", "/usr/local/bin/python3"):
            with self.assertRaises(RuntimeError):
                apply_downloaded_update("/tmp/Update.dmg")

    def test_macos_raises_when_install_location_is_not_writable(self):
        fake_exe = "/Applications/YTDownloaderGUI.app/Contents/MacOS/YTDownloaderGUI"
        with patch.object(updater.sys, "platform", "darwin"), \
             patch.object(updater.sys, "executable", fake_exe), \
             patch.object(updater.os, "access", return_value=False):
            with self.assertRaises(RuntimeError):
                apply_downloaded_update("/tmp/Update.dmg")


if __name__ == "__main__":
    unittest.main()
