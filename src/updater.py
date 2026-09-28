"""起動時のアップデート確認・ダウンロード・適用(インストーラー実行/再起動)。

GitHub Releases (tsubamon55/youtube-downloader) の最新リリースをGitHub APIで問い合わせ、
同梱のVERSIONファイルより新しいtag_nameがあれば、OSに応じたアセット(Windows: Setup*.exe,
macOS: *.dmg)をダウンロードして適用する。リリースが1件も無い/該当アセットが無い場合は
「アップデート無し」と同じ扱いにし、ユーザーには何も表示しない(バックグラウンドの
自動確認でエラーを見せても対処法が無く、単に不安を与えるだけのため)。
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

from config import CONFIG
from paths import find_bundled_file, get_app_data_dir, get_log_file_path, log_debug

GITHUB_REPO = "tsubamon55/youtube-downloader"
GITHUB_API_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
VERSION_FILE_NAME = "VERSION"
_REQUEST_HEADERS = {"User-Agent": "YTDownloaderGUI"}


class UpdateCancelledError(Exception):
    """ダウンロード中にユーザーがキャンセルした場合に投げる"""


def get_current_version() -> str | None:
    """自分のバージョンが分からない場合はNoneを返す。仮の値(0.0.0等)で比較すると
    どのリリースも新しいと判定され、更新後も同じ状態のまま毎回更新を促し続けてしまうため、
    呼び出し側はNoneのときアップデート確認自体を行わないこと"""
    path = find_bundled_file(VERSION_FILE_NAME)
    if path is None:
        log_debug(f"get_current_version: {VERSION_FILE_NAME} が見つからないためアップデート確認を行いません")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            version = f.read().strip()
    except OSError as e:
        log_debug(f"get_current_version: {path} の読み込みに失敗しました ({e!r})")
        return None
    return version or None


def _parse_version(version: str) -> tuple[int, ...]:
    """"v1.2.10" のような表記の先頭の"v"等を落とし、数字部分だけをタプル化する。
    解析できない部分は0として扱う(既存のVERSIONファイルは"1.2.0"形式の単純な数字のみのため、
    ここでは厳密なsemver仕様への対応までは行わない)"""
    numbers = re.findall(r"\d+", version)
    return tuple(int(n) for n in numbers) if numbers else (0,)


def is_newer_version(remote: str, local: str) -> bool:
    remote_parts = _parse_version(remote)
    local_parts = _parse_version(local)
    length = max(len(remote_parts), len(local_parts))
    remote_parts += (0,) * (length - len(remote_parts))
    local_parts += (0,) * (length - len(local_parts))
    return remote_parts > local_parts


def _select_asset(assets: list[dict]) -> dict | None:
    """OSに応じたリリースアセットを選ぶ(Windows: Setup*.exe、macOS: *.dmg)。
    Windows用インストーラーが複数見つかった場合はファイル名に"setup"を含むものを優先する
    (installer.issのOutputBaseFilenameが"YTDownloaderGUI-Setup-<version>"のため)"""
    if sys.platform == "win32":
        candidates = [a for a in assets if a.get("name", "").lower().endswith(".exe")]
        candidates.sort(key=lambda a: "setup" not in a.get("name", "").lower())
        return candidates[0] if candidates else None
    if sys.platform == "darwin":
        candidates = [a for a in assets if a.get("name", "").lower().endswith(".dmg")]
        return candidates[0] if candidates else None
    return None


def download_dir() -> str:
    return os.path.join(get_app_data_dir(), "updates")


class UpdateCheckWorker(QThread):
    update_available = pyqtSignal(str, str, str)  # version, download_url, asset_name
    up_to_date = pyqtSignal()
    check_failed = pyqtSignal(str)

    def run(self) -> None:
        current_version = get_current_version()
        if current_version is None:
            self.up_to_date.emit()
            return

        try:
            request = urllib.request.Request(GITHUB_API_LATEST_RELEASE_URL, headers=_REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=CONFIG.update_check_timeout_seconds) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # リリースが1件も無いリポジトリでは/releases/latestが404になる。
                # 通常運用でも起こり得る状態であり「更新無し」と同じ扱いにする
                self.up_to_date.emit()
            else:
                self.check_failed.emit(str(e))
            return
        except Exception as e:
            self.check_failed.emit(str(e))
            return

        remote_version = str(data.get("tag_name") or "").strip()
        if not remote_version or not is_newer_version(remote_version, current_version):
            self.up_to_date.emit()
            return

        asset = _select_asset(data.get("assets") or [])
        if asset is None:
            log_debug(f"UpdateCheckWorker: 対応するアセットが見つかりません (assets={data.get('assets')!r})")
            self.up_to_date.emit()
            return

        self.update_available.emit(remote_version, asset.get("browser_download_url", ""), asset.get("name", ""))


class UpdateDownloadWorker(QThread):
    progress = pyqtSignal(float)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    # サムネイル等の小さい取得と異なり数十MB単位のインストーラーを読むため、チャンクは大きめにする
    _CHUNK_SIZE = 256 * 1024

    def __init__(self, url: str, dest_path: str):
        super().__init__()
        self.url = url
        self.dest_path = dest_path
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        try:
            request = urllib.request.Request(self.url, headers=_REQUEST_HEADERS)
            os.makedirs(os.path.dirname(self.dest_path), exist_ok=True)
            with urllib.request.urlopen(request, timeout=CONFIG.update_check_timeout_seconds) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with open(self.dest_path, "wb") as f:
                    while True:
                        if self._is_cancelled:
                            raise UpdateCancelledError()
                        chunk = resp.read(self._CHUNK_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self.progress.emit(downloaded / total * 100)
            # http.clientのread()は接続が途中で切れても例外を投げず空を返すことがあるため、
            # 受信量が宣言されたサイズに満たない場合は壊れたファイルとして扱う
            # (そのまま適用すると、途中までのインストーラーがサイレント実行されてしまう)
            if total and downloaded != total:
                raise OSError(f"ダウンロードが途中で切断されました ({downloaded}/{total} バイト)")
            self.finished_ok.emit(self.dest_path)
        except UpdateCancelledError:
            self._cleanup_partial_file()
            self.finished_error.emit("キャンセルされました")
        except Exception as e:
            self._cleanup_partial_file()
            self.finished_error.emit(str(e))

    def _cleanup_partial_file(self) -> None:
        if os.path.isfile(self.dest_path):
            try:
                os.remove(self.dest_path)
            except OSError as e:
                log_debug(f"UpdateDownloadWorker: 未完成ファイルの削除に失敗しました ({e!r})")


def _launch_windows_installer(installer_path: str) -> None:
    """ダウンロードしたSetupを非同期に起動する。DETACHED_PROCESS/CREATE_NEW_PROCESS_GROUPで
    このアプリのプロセスグループから切り離しておくことで、この直後にアプリが終了しても
    インストーラーの実行(サイレントインストール→installer.issの[Run]セクションに
    よる再起動)がそのまま継続する"""
    subprocess.Popen(
        [installer_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


# macOSで.appを差し替えるヘルパー。引数は「終了を待つPID」「dmgのパス」「差し替える.appのパス」。
# 旧プロセスの終了を待ってから差し替え・起動する(起動中に`open`すると同じbundle idの旧インスタンスが
# 前面に出るだけで新バージョンが起動しない)。dittoはバンドル内のシンボリックリンクやxattrを保ったまま
# コピーし(copytreeの既定ではリンクが実体化してバンドル構成と署名が壊れる)、一旦隣の作業用パスへ
# 展開してから入れ替えるため、途中で失敗しても元の.appは残る。
# quarantine属性を外すのは、ダウンロードしたdmg由来の属性が付いたままだとGatekeeperの警告で
# 再起動が止まるため(このアプリはアドホック署名のみで公証していない。README参照)
_MACOS_UPDATE_SCRIPT = """#!/bin/sh
PID="$1"
DMG="$2"
DEST="$3"
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done

MNT=$(mktemp -d /tmp/ytdlgui-update.XXXXXX) || exit 1
if ! hdiutil attach -nobrowse -noautoopen -mountpoint "$MNT" "$DMG"; then
    echo "updater: dmgのマウントに失敗しました"
    open "$DEST"
    exit 1
fi
APP=$(find "$MNT" -maxdepth 1 -name '*.app' | head -n 1)
STAGE="$DEST.updating"
rm -rf "$STAGE"
if [ -n "$APP" ] && ditto "$APP" "$STAGE"; then
    hdiutil detach "$MNT" >/dev/null
    xattr -dr com.apple.quarantine "$STAGE"
    rm -rf "$DEST.old"
    if mv "$DEST" "$DEST.old"; then
        if mv "$STAGE" "$DEST"; then
            rm -rf "$DEST.old"
        else
            echo "updater: 新しい.appの配置に失敗したため元に戻します"
            mv "$DEST.old" "$DEST"
        fi
    fi
else
    echo "updater: .appのコピーに失敗しました"
    hdiutil detach "$MNT" >/dev/null
fi
rm -rf "$STAGE"
open "$DEST"
"""


def _current_app_bundle_path() -> str:
    """実行中の.appバンドルのパス(sys.executableは <X>.app/Contents/MacOS/<実行ファイル>)。
    /Applications決め打ちにすると、~/Applications等から起動していた場合に別の場所へ
    2つ目のコピーを作るだけで、実際に使っている方が更新されない"""
    bundle = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(sys.executable))))
    if not bundle.endswith(".app"):
        raise RuntimeError(".appバンドルから起動していないため自動アップデートを適用できません")
    return bundle


def _launch_macos_updater(dmg_path: str) -> None:
    bundle = _current_app_bundle_path()
    # 差し替えは親フォルダ内でのリネームで行うため、書き込めない場所(管理者権限が必要な
    # /Applicationsを一般ユーザーで使っている等)ではアプリを終了させる前にここで止める
    if not os.access(os.path.dirname(bundle), os.W_OK):
        raise RuntimeError(f"{os.path.dirname(bundle)} に書き込む権限がありません")

    script_path = os.path.join(download_dir(), "apply_update.sh")
    os.makedirs(download_dir(), exist_ok=True)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(_MACOS_UPDATE_SCRIPT)

    log_path = get_log_file_path()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            ["/bin/sh", script_path, str(os.getpid()), dmg_path, bundle],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )


def apply_downloaded_update(local_path: str) -> None:
    """ダウンロード済みのインストーラー/dmgを適用するプロセスを起動する。
    どちらのOSでもこのアプリの終了後に差し替え・再起動が行われる前提のため、
    呼び出し元は実行中の処理を止めた上でアプリを終了させてから呼ぶこと"""
    if sys.platform == "win32":
        _launch_windows_installer(local_path)
    elif sys.platform == "darwin":
        _launch_macos_updater(local_path)
    else:
        raise RuntimeError("このOSでは自動アップデートに対応していません")
