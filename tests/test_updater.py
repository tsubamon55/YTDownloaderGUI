"""updater.py(起動時のアップデート確認・ダウンロード・適用)の単体テスト。

実際のGitHub API通信・ネットワークダウンロード・インストーラー実行は行わず、
urllib.request.urlopenやsubprocess.Popen等をモックして検証する。
QThreadはworkers.py側のテストと同じく、.start()ではなく.run()を直接呼んで
同期的に実行する(イベントループ無しでも直接接続でシグナルが即時に届くため)。
"""

import hashlib
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
    parse_sha256sums,
)

RELEASE_URL = "https://github.com/tsubamon55/YTDownloaderGUI/releases/download/v1.2.1/"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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

    def test_windows_ignores_exe_without_setup_in_name(self):
        """"setup"を含まないexeは/VERYSILENTで実行してよいインストーラーとは限らない"""
        assets = [{"name": "YTDownloaderGUI-portable.exe"}]
        with patch.object(updater.sys, "platform", "win32"):
            self.assertIsNone(_select_asset(assets))

    def test_macos_selects_dmg_for_running_architecture(self):
        assets = [
            {"name": "YTDownloaderGUI-Setup-1.2.1.exe"},
            {"name": "YTDownloaderGUI-1.2.1-arm64.dmg"},
            {"name": "YTDownloaderGUI-1.2.1-x86_64.dmg"},
        ]
        for machine, expected in (("arm64", "YTDownloaderGUI-1.2.1-arm64.dmg"),
                                  ("x86_64", "YTDownloaderGUI-1.2.1-x86_64.dmg")):
            with self.subTest(machine=machine), patch.object(updater.sys, "platform", "darwin"), \
                    patch.object(updater.platform, "machine", return_value=machine):
                self.assertEqual(_select_asset(assets)["name"], expected)

    def test_macos_untagged_dmg_is_only_for_apple_silicon(self):
        """アーキテクチャ表記の無いdmgはApple Silicon専用ビルド。Intel Macへ配ると起動できなくなる"""
        assets = [{"name": "YTDownloaderGUI-1.2.1.dmg"}]
        with patch.object(updater.sys, "platform", "darwin"):
            with patch.object(updater.platform, "machine", return_value="arm64"):
                self.assertEqual(_select_asset(assets)["name"], "YTDownloaderGUI-1.2.1.dmg")
            with patch.object(updater.platform, "machine", return_value="x86_64"):
                self.assertIsNone(_select_asset(assets))

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


class ParseSha256SumsTest(unittest.TestCase):
    def test_finds_hash_for_exact_name(self):
        text = (
            f"{'a' * 64}  YTDownloaderGUI-Setup-1.2.1.exe\n"
            f"{'B' * 64} *YTDownloaderGUI-1.2.1-arm64.dmg\n"
        )
        self.assertEqual(parse_sha256sums(text, "YTDownloaderGUI-Setup-1.2.1.exe"), "a" * 64)
        self.assertEqual(parse_sha256sums(text, "YTDownloaderGUI-1.2.1-arm64.dmg"), "b" * 64)
        self.assertIsNone(parse_sha256sums(text, "Setup-1.2.1.exe"))

    def test_ignores_malformed_lines(self):
        self.assertIsNone(parse_sha256sums("deadbeef  a.exe\n", "a.exe"))


class UpdateCheckWorkerTest(unittest.TestCase):
    def _run(self, urlopen_side_effect, current_version="1.2.0"):
        results = {"update_available": [], "up_to_date": [], "check_failed": []}
        worker = UpdateCheckWorker()
        worker.update_available.connect(lambda *a: results["update_available"].append(a))
        worker.up_to_date.connect(lambda: results["up_to_date"].append(True))
        worker.check_failed.connect(lambda msg: results["check_failed"].append(msg))

        with patch.object(updater, "get_current_version", return_value=current_version), \
             patch.object(updater, "remove_stale_update_files"), \
             patch("updater.urllib.request.urlopen", side_effect=urlopen_side_effect):
            worker.run()
        return results

    @staticmethod
    def _release(assets, sums_text=None):
        """GitHub APIの応答と、SHA256SUMSの中身を返すurlopenの代わり"""
        api = _fake_response(json_data={"tag_name": "v1.2.1", "assets": assets})
        sums = _fake_response(payload_bytes=(sums_text or "").encode())

        def urlopen(request, timeout=None):
            url = getattr(request, "full_url", request)
            return sums if url.endswith("SHA256SUMS") else api

        return urlopen

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

    SETUP = {"name": "YTDownloaderGUI-Setup-1.2.1.exe",
             "browser_download_url": RELEASE_URL + "YTDownloaderGUI-Setup-1.2.1.exe"}
    SUMS = {"name": "SHA256SUMS", "browser_download_url": RELEASE_URL + "SHA256SUMS"}

    def test_newer_version_with_matching_asset_emits_update_available(self):
        urlopen = self._release([self.SETUP, self.SUMS], f"{'c' * 64}  YTDownloaderGUI-Setup-1.2.1.exe\n")
        with patch.object(updater.sys, "platform", "win32"):
            results = self._run(urlopen, current_version="1.2.0")

        self.assertEqual(len(results["update_available"]), 1)
        version, url, name, sha256 = results["update_available"][0]
        self.assertEqual(version, "v1.2.1")
        self.assertEqual(url, self.SETUP["browser_download_url"])
        self.assertEqual(name, "YTDownloaderGUI-Setup-1.2.1.exe")
        self.assertEqual(sha256, "c" * 64)

    def test_release_without_checksum_is_not_offered(self):
        """照合できないファイルはサイレント実行しない"""
        for assets, sums in (([self.SETUP], None), ([self.SETUP, self.SUMS], f"{'c' * 64}  other.exe\n")):
            with self.subTest(assets=assets), patch.object(updater.sys, "platform", "win32"):
                results = self._run(self._release(assets, sums), current_version="1.2.0")
            self.assertEqual(results["update_available"], [])
            self.assertEqual(len(results["up_to_date"]), 1)

    def test_asset_outside_this_repository_is_ignored(self):
        for asset in (
            {"name": "YTDownloaderGUI-Setup-1.2.1.exe", "browser_download_url": "https://example.com/Setup.exe"},
            {"name": "../../YTDownloaderGUI-Setup-1.2.1.exe",
             "browser_download_url": RELEASE_URL + "YTDownloaderGUI-Setup-1.2.1.exe"},
        ):
            with self.subTest(asset=asset), patch.object(updater.sys, "platform", "win32"):
                results = self._run(self._release([asset, self.SUMS], ""), current_version="1.2.0")
            self.assertEqual(results["update_available"], [])


class UpdateDownloadWorkerTest(unittest.TestCase):
    CONTENT_SHA256 = sha256_of(b"a" * 10 + b"b" * 10)

    def test_successful_download_writes_file_and_emits_finished_ok(self):
        chunks = [b"a" * 10, b"b" * 10, b""]
        resp = _fake_response(headers={"Content-Length": "20"})
        resp.read.side_effect = chunks

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "sub", "Setup.exe")
            worker = UpdateDownloadWorker("https://example.com/Setup.exe", dest, self.CONTENT_SHA256)
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
            worker = UpdateDownloadWorker("https://example.com/Setup.exe", dest, self.CONTENT_SHA256)
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
            worker = UpdateDownloadWorker("https://example.com/Setup.exe", dest, self.CONTENT_SHA256)

            def fake_read(size):
                worker.cancel()
                return b"a" * 10

            resp.read.side_effect = fake_read
            results = {"ok": [], "error": [], "cancelled": []}
            worker.finished_ok.connect(lambda p: results["ok"].append(p))
            worker.finished_error.connect(lambda m: results["error"].append(m))
            worker.cancelled.connect(lambda: results["cancelled"].append(True))

            with patch("updater.urllib.request.urlopen", return_value=resp):
                worker.run()

            self.assertEqual(results["cancelled"], [True])
            self.assertEqual(results["error"], [])
            self.assertFalse(os.path.isfile(dest))

    def test_network_failure_is_reported_and_cleans_up_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "Setup.exe")
            # 途中まで書きかけの状態を模しておき、失敗時に削除されることを確認する
            with open(dest, "wb") as f:
                f.write(b"partial")

            worker = UpdateDownloadWorker("https://example.com/Setup.exe", dest, self.CONTENT_SHA256)
            results = {"error": []}
            worker.finished_error.connect(lambda m: results["error"].append(m))

            with patch("updater.urllib.request.urlopen", side_effect=OSError("network down")):
                worker.run()

            self.assertEqual(len(results["error"]), 1)
            self.assertFalse(os.path.isfile(dest))


class UpdateDownloadVerificationTest(unittest.TestCase):
    def _download(self, tmp, expected_sha256):
        resp = _fake_response(headers={"Content-Length": "4"})
        resp.read.side_effect = [b"data", b""]
        dest = os.path.join(tmp, "Setup.exe")
        worker = UpdateDownloadWorker("https://example.com/Setup.exe", dest, expected_sha256)
        results = {"ok": [], "error": []}
        worker.finished_ok.connect(results["ok"].append)
        worker.finished_error.connect(results["error"].append)
        with patch("updater.urllib.request.urlopen", return_value=resp):
            worker.run()
        return dest, results

    def test_hash_mismatch_is_rejected_and_nothing_is_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest, results = self._download(tmp, "0" * 64)
            self.assertEqual(results["ok"], [])
            self.assertEqual(len(results["error"]), 1)
            self.assertEqual(os.listdir(tmp), [])

    def test_matching_hash_is_moved_into_place_from_part_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest, results = self._download(tmp, sha256_of(b"data"))
            self.assertEqual(results["ok"], [dest])
            self.assertEqual(os.listdir(tmp), ["Setup.exe"])


class RemoveStaleUpdateFilesTest(unittest.TestCase):
    def test_removes_previous_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("YTDownloaderGUI-Setup-1.0.0.exe", "apply_update.sh"):
                open(os.path.join(tmp, name), "w").close()
            with patch.object(updater, "download_dir", return_value=tmp):
                updater.remove_stale_update_files()
            self.assertEqual(os.listdir(tmp), [])

    def test_missing_folder_is_fine(self):
        with patch.object(updater, "download_dir", return_value=os.path.join(tempfile.gettempdir(), "no-such-dir-x")):
            updater.remove_stale_update_files()


class ApplyVerificationTest(unittest.TestCase):
    def test_tampered_file_is_not_launched(self):
        """ダウンロード後に差し替えられたファイルは、適用直前の照合で弾く"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Setup.exe")
            with open(path, "wb") as f:
                f.write(b"tampered")
            with patch.object(updater.sys, "platform", "win32"), \
                 patch.object(updater.subprocess, "Popen") as popen_mock, \
                 self.assertRaises(RuntimeError):
                apply_downloaded_update(path, sha256_of(b"original"))
            popen_mock.assert_not_called()


class ApplyDownloadedUpdateTest(unittest.TestCase):
    def setUp(self):
        # ここでは起動方法だけを確かめるため、ハッシュ値の照合(ApplyVerificationTest)は省く
        verify_patch = patch.object(updater, "verify_sha256")
        verify_patch.start()
        self.addCleanup(verify_patch.stop)

    def test_windows_launches_detached_silent_installer(self):
        # DETACHED_PROCESS等はWindows版のsubprocessにしか無いため、他OSで実行する場合(CIのmacOS等)は仮の値を置く
        with patch.object(updater.sys, "platform", "win32"), \
             patch.object(updater.subprocess, "DETACHED_PROCESS", 0x8, create=True), \
             patch.object(updater.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True), \
             patch.object(updater.subprocess, "Popen") as popen_mock:
            apply_downloaded_update("C:/tmp/Setup.exe", "0" * 64)

        args, kwargs = popen_mock.call_args
        self.assertEqual(args[0], ["C:/tmp/Setup.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        # アプリ終了後もインストーラーが生き残るよう、プロセスグループを切り離していること
        self.assertTrue(kwargs.get("close_fds"))

    def test_unsupported_platform_raises(self):
        with patch.object(updater.sys, "platform", "linux"):
            with self.assertRaises(RuntimeError):
                apply_downloaded_update("/tmp/whatever", "0" * 64)

    def test_macos_bundle_path_is_derived_from_executable(self):
        fake_exe = "/Applications/YTDownloaderGUI.app/Contents/MacOS/YTDownloaderGUI"
        with patch.object(updater.sys, "platform", "darwin"), \
             patch.object(updater.sys, "executable", fake_exe), \
             patch.object(updater.os, "access", return_value=True), \
             patch.object(updater.subprocess, "Popen") as popen_mock, \
             patch("builtins.open", MagicMock()), \
             patch.object(updater.os, "makedirs"):
            apply_downloaded_update("/tmp/Update.dmg", "0" * 64)

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
                apply_downloaded_update("/tmp/Update.dmg", "0" * 64)

    def test_macos_raises_when_install_location_is_not_writable(self):
        fake_exe = "/Applications/YTDownloaderGUI.app/Contents/MacOS/YTDownloaderGUI"
        with patch.object(updater.sys, "platform", "darwin"), \
             patch.object(updater.sys, "executable", fake_exe), \
             patch.object(updater.os, "access", return_value=False):
            with self.assertRaises(RuntimeError):
                apply_downloaded_update("/tmp/Update.dmg", "0" * 64)


if __name__ == "__main__":
    unittest.main()
