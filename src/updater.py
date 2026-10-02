"""起動時のアップデート確認・ダウンロード・適用(インストーラー実行/再起動)。

GitHub Releases (tsubamon55/YTDownloaderGUI) の最新リリースをGitHub APIで問い合わせ、
同梱のVERSIONファイルより新しいtag_nameがあれば、OSに応じたアセット(Windows: *Setup*.exe,
macOS: 実行中のCPUに合う*.dmg)をダウンロードして適用する。リリースが1件も無い/該当アセットが
無い場合は「アップデート無し」と同じ扱いにし、ユーザーには何も表示しない(バックグラウンドの
自動確認でエラーを見せても対処法が無く、単に不安を与えるだけのため)。

ダウンロードしたファイルは、同じリリースに添付されたSHA256SUMS(release.ymlが生成)の値と
照合してから適用する。SHA256SUMSが無い・記載が無いリリースは適用しない(アップデートは
確認なしでサイレント実行されるため、破損・差し替えられたファイルを実行しないことを優先する)。
"""

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal

from config import CONFIG
from paths import find_bundled_file, get_app_data_dir, get_log_file_path, log_debug, remove_file_quietly

GITHUB_REPO = "tsubamon55/YTDownloaderGUI"
GITHUB_API_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
VERSION_FILE_NAME = "VERSION"
_REQUEST_HEADERS = {"User-Agent": "YTDownloaderGUI"}
# アセットのダウンロードURLは、このリポジトリのリリース配下以外を受け付けない
# (APIの応答が想定外の内容でも、無関係な場所からファイルを取得して実行しないため)
_ASSET_URL_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
# アセット名はダウンロード先のファイル名にそのまま使うため、フォルダ区切り等を含むものは拒否する
_ASSET_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
CHECKSUMS_ASSET_NAME = "SHA256SUMS"
_MAX_CHECKSUMS_BYTES = 64 * 1024
_SHA256SUMS_LINE = re.compile(r"([0-9a-fA-F]{64}) [ *](.+)")


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
        with open(path, encoding="utf-8") as f:
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


def _macos_arch() -> str:
    """実行中のプロセスのCPUアーキテクチャ("arm64"または"x86_64")"""
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def _dmg_arch(name: str) -> str | None:
    """dmgのファイル名に書かれた対応アーキテクチャ(書かれていなければNone)"""
    lowered = name.lower()
    if "universal" in lowered:
        return "universal"
    if "arm64" in lowered:
        return "arm64"
    if "x86_64" in lowered or "intel" in lowered:
        return "x86_64"
    return None


def _select_asset(assets: list[dict]) -> dict | None:
    """OSに応じたリリースアセットを選ぶ。

    Windowsはファイル名に"setup"を含むexeだけを選ぶ(installer.issのOutputBaseFilenameが
    "YTDownloaderGUI-Setup-<version>"のため)。それ以外のexeは/VERYSILENTを付けて実行して
    よいインストーラーとは限らないため選ばない。
    macOSは実行中のCPUに合うdmgを選ぶ。アーキテクチャの書かれていないdmgは、
    名前に付けるようにする前のApple Silicon専用ビルドなので、Apple Siliconでのみ選ぶ
    (Intel Macへ配ると、更新後に起動できなくなる)"""
    if sys.platform == "win32":
        candidates = [
            a for a in assets
            if a.get("name", "").lower().endswith(".exe") and "setup" in a.get("name", "").lower()
        ]
        return candidates[0] if candidates else None
    if sys.platform == "darwin":
        arch = _macos_arch()
        dmgs = [a for a in assets if a.get("name", "").lower().endswith(".dmg")]
        preferences: list[str | None] = [arch, "universal"]
        if arch == "arm64":
            preferences.append(None)
        for wanted in preferences:
            for asset in dmgs:
                if _dmg_arch(asset.get("name", "")) == wanted:
                    return asset
        return None
    return None


def _is_trusted_asset(name: str, url: str) -> bool:
    """アセット名がファイル名として安全で、URLがこのリポジトリのリリース配下か"""
    return bool(_ASSET_NAME_PATTERN.fullmatch(name)) and ".." not in name and url.startswith(_ASSET_URL_PREFIX)


def parse_sha256sums(text: str, name: str) -> str | None:
    """sha256sum形式("<64桁の16進数>  <ファイル名>")の一覧から、nameのハッシュ値(小文字)を返す"""
    for line in text.splitlines():
        match = _SHA256SUMS_LINE.fullmatch(line.strip())
        if match and match.group(2) == name:
            return match.group(1).lower()
    return None


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: str, expected_sha256: str) -> None:
    """pathの内容がexpected_sha256と一致しなければRuntimeErrorを送出する"""
    actual = file_sha256(path)
    if actual != expected_sha256.lower():
        raise RuntimeError(
            "ダウンロードしたファイルが破損しているか、配布元のものと異なるため適用できません"
            f"(SHA-256 期待値: {expected_sha256}, 実際: {actual})"
        )


def download_dir() -> str:
    return os.path.join(get_app_data_dir(), "updates")


def remove_stale_update_files() -> None:
    """以前のアップデートでダウンロードしたインストーラー・dmg・適用用スクリプトを削除する。
    適用後も残しておく理由は無く、放っておくと版を重ねるごとに数十MBずつ溜まっていくため。
    (実行中で削除できないものは次回に回す)"""
    folder = download_dir()
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return
    except OSError as e:
        log_debug(f"remove_stale_update_files: {folder} の一覧取得に失敗 ({e!r})")
        return
    for name in names:
        remove_file_quietly(os.path.join(folder, name), "remove_stale_update_files")


class UpdateCheckWorker(QThread):
    update_available = pyqtSignal(str, str, str, str)  # version, download_url, asset_name, sha256
    up_to_date = pyqtSignal()
    check_failed = pyqtSignal(str)

    def run(self) -> None:
        current_version = get_current_version()
        if current_version is None:
            self.up_to_date.emit()
            return
        remove_stale_update_files()

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

        assets = data.get("assets") or []
        asset = _select_asset(assets)
        if asset is None:
            log_debug(f"UpdateCheckWorker: 対応するアセットが見つかりません (assets={assets!r})")
            self.up_to_date.emit()
            return
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not _is_trusted_asset(name, url):
            log_debug(f"UpdateCheckWorker: アセットの名前・URLが想定外のため無視します ({name!r}, {url!r})")
            self.up_to_date.emit()
            return

        try:
            expected_sha256 = self._fetch_expected_sha256(assets, name)
        except Exception as e:
            self.check_failed.emit(f"{CHECKSUMS_ASSET_NAME}の取得に失敗しました ({e})")
            return
        if expected_sha256 is None:
            log_debug(f"UpdateCheckWorker: {remote_version} の{CHECKSUMS_ASSET_NAME}に {name} の記載が無いため適用しません")
            self.up_to_date.emit()
            return

        self.update_available.emit(remote_version, url, name, expected_sha256)

    @staticmethod
    def _fetch_expected_sha256(assets: list[dict], name: str) -> str | None:
        """リリースに添付されたSHA256SUMSから、nameの期待ハッシュ値を求める(無ければNone)"""
        checksums = next((a for a in assets if a.get("name") == CHECKSUMS_ASSET_NAME), None)
        if checksums is None:
            return None
        url = str(checksums.get("browser_download_url") or "")
        if not _is_trusted_asset(CHECKSUMS_ASSET_NAME, url):
            log_debug(f"UpdateCheckWorker: {CHECKSUMS_ASSET_NAME}のURLが想定外のため無視します ({url!r})")
            return None
        request = urllib.request.Request(url, headers=_REQUEST_HEADERS)
        with urllib.request.urlopen(request, timeout=CONFIG.update_check_timeout_seconds) as resp:
            data = resp.read(_MAX_CHECKSUMS_BYTES + 1)
        if len(data) > _MAX_CHECKSUMS_BYTES:
            raise ValueError(f"{CHECKSUMS_ASSET_NAME}が大きすぎます")
        return parse_sha256sums(data.decode("utf-8", errors="replace"), name)


class UpdateDownloadWorker(QThread):
    progress = pyqtSignal(float)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)
    # ユーザーによるキャンセル。エラーとは区別し、呼び出し側がエラー表示を出さずに済むようにする
    cancelled = pyqtSignal()

    # サムネイル等の小さい取得と異なり数十MB単位のインストーラーを読むため、チャンクは大きめにする
    _CHUNK_SIZE = 256 * 1024

    def __init__(self, url: str, dest_path: str, expected_sha256: str):
        super().__init__()
        self.url = url
        self.dest_path = dest_path
        self.expected_sha256 = expected_sha256
        # 完成前のファイルは別名で書き、検証が済んでから本来の名前にする。途中で落ちても
        # 本来の名前の(実行され得る)ファイルが中途半端な内容で残らないようにするため
        self._part_path = f"{dest_path}.part"
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
                with open(self._part_path, "wb") as f:
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
            verify_sha256(self._part_path, self.expected_sha256)
            os.replace(self._part_path, self.dest_path)
            self.finished_ok.emit(self.dest_path)
        except UpdateCancelledError:
            self._cleanup_partial_file()
            self.cancelled.emit()
        except Exception as e:
            self._cleanup_partial_file()
            self.finished_error.emit(str(e))

    def _cleanup_partial_file(self) -> None:
        remove_file_quietly(self._part_path, "UpdateDownloadWorker")
        remove_file_quietly(self.dest_path, "UpdateDownloadWorker")


def _launch_windows_installer(installer_path: str) -> None:
    """ダウンロードしたSetupを非同期に起動する。DETACHED_PROCESS/CREATE_NEW_PROCESS_GROUPで
    このアプリのプロセスグループから切り離しておくことで、この直後にアプリが終了しても
    インストーラーの実行(サイレントインストール→installer.issの[Run]セクションに
    よる再起動)がそのまま継続する"""
    # 下の定数はWindows版のsubprocessにしか無いため、mypyに他OSでの型チェックを省かせる
    if sys.platform != "win32":
        raise RuntimeError("Windows以外ではインストーラーを起動できません")
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
            if ! mv "$DEST.old" "$DEST"; then
                echo "updater: 元の.appを戻せませんでした。$DEST.old を $DEST へ手動で戻してください"
            fi
        fi
    else
        echo "updater: 実行中だった.appを退避できなかったため、更新を中止しました"
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


def apply_downloaded_update(local_path: str, expected_sha256: str) -> None:
    """ダウンロード済みのインストーラー/dmgを適用するプロセスを起動する。
    どちらのOSでもこのアプリの終了後に差し替え・再起動が行われる前提のため、
    呼び出し元は実行中の処理を止めた上でアプリを終了させてから呼ぶこと。

    ダウンロード完了から適用(アプリの終了時)までの間にファイルが差し替えられていないよう、
    起動の直前にもう一度ハッシュ値を確かめる"""
    verify_sha256(local_path, expected_sha256)
    if sys.platform == "win32":
        _launch_windows_installer(local_path)
    elif sys.platform == "darwin":
        _launch_macos_updater(local_path)
    else:
        raise RuntimeError("このOSでは自動アップデートに対応していません")
