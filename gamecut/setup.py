"""足りないもの（Python ライブラリ・ffmpeg）の確認と自動インストール。

このファイルは標準ライブラリだけで動くようにしてある（ライブラリが入る前に呼ばれるため）。
"""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HOME_DIR = Path.home() / ".gamecut"
BIN_DIR = HOME_DIR / "bin"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
EXE = ".exe" if IS_WIN else ""

# import 名 → pip のパッケージ名
PACKAGES = {
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "numpy": "numpy",
    "certifi": "certifi",
    "faster_whisper": "faster-whisper",
    "googleapiclient": "google-api-python-client",
    "google_auth_oauthlib": "google-auth-oauthlib",
}

AUTO_INSTALL = os.environ.get("GAMECUT_NO_AUTO_INSTALL") != "1"


def log(msg: str) -> None:
    print(f"[準備] {msg}", flush=True)


# ---------------------------------------------------------------- Python ライブラリ
def missing_packages() -> list[str]:
    return [pip for mod, pip in PACKAGES.items() if importlib.util.find_spec(mod) is None]


def ensure_python_packages() -> None:
    missing = missing_packages()
    if not missing:
        return
    if not AUTO_INSTALL:
        sys.exit(f"必要なライブラリがありません: {', '.join(missing)}\n"
                 f"  {sys.executable} -m pip install {' '.join(missing)}")
    log(f"足りないライブラリを入れます: {', '.join(missing)}")
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *missing]
    if sys.prefix == sys.base_prefix:  # 仮想環境の外ならユーザー領域に入れる
        cmd.insert(4, "--user")
    if subprocess.call(cmd) != 0:
        sys.exit("ライブラリのインストールに失敗しました。ネット接続を確認してもう一度実行してください。")
    importlib.invalidate_caches()
    import site
    if sys.prefix == sys.base_prefix:
        p = site.getusersitepackages()
        if p not in sys.path:
            sys.path.append(p)


# ---------------------------------------------------------------- ffmpeg
def _ffmpeg_works(path: str) -> bool:
    try:
        return subprocess.run([path, "-hide_banner", "-version"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _winget_paths() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    found = [local / "Microsoft" / "WinGet" / "Links"]
    pkgs = local / "Microsoft" / "WinGet" / "Packages"
    if pkgs.exists():
        found += [p.parent for p in pkgs.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe")]
    return found


def find_tool(name: str) -> str | None:
    """ffmpeg / ffprobe の場所を探す（専用フォルダ → PATH → よくある場所）"""
    candidates = [BIN_DIR / f"{name}{EXE}"]
    which = shutil.which(name)
    if which:
        candidates.append(Path(which))
    if IS_MAC:
        candidates += [Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name]
    if IS_WIN:
        candidates += [d / f"{name}{EXE}" for d in _winget_paths()]
    for c in candidates:
        if c.exists() and _ffmpeg_works(str(c)):
            return str(c)
    return None


def _download(url: str, dest: Path) -> None:
    log(f"ダウンロード中: {url}")
    try:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "gamecut"})
        with urllib.request.urlopen(req, context=ctx, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception:
        # 証明書の問題などで失敗したら OS 標準の curl を使う（Mac・Windows 10 以降に標準で入っている）
        if subprocess.call(["curl", "-L", "--fail", "-o", str(dest), url]) != 0:
            raise RuntimeError(f"ダウンロードに失敗しました: {url}")


def _extract(zip_path: Path, names: list[str]) -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            base = Path(info.filename).name
            if base in names:
                target = BIN_DIR / base
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                target.chmod(0o755)


def _download_ffmpeg() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if IS_WIN:
            z = tmp / "ffmpeg.zip"
            _download("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip", z)
            _extract(z, ["ffmpeg.exe", "ffprobe.exe"])
        elif IS_MAC:
            arch = "arm64" if platform.machine() == "arm64" else "amd64"
            for tool in ("ffmpeg", "ffprobe"):
                z = tmp / f"{tool}.zip"
                _download(f"https://ffmpeg.martin-riedl.de/redirect/latest/macos/{arch}/release/{tool}.zip", z)
                _extract(z, [tool])
                # ダウンロード品に付く制限を外す
                subprocess.call(["xattr", "-dr", "com.apple.quarantine", str(BIN_DIR / tool)],
                                stderr=subprocess.DEVNULL)
        else:
            raise RuntimeError("この OS では ffmpeg を自動で入れられません。手動でインストールしてください。")


def install_ffmpeg() -> None:
    log("ffmpeg が見つからないので自動で入れます（数分かかることがあります）")
    if IS_MAC and shutil.which("brew"):
        if subprocess.call(["brew", "install", "ffmpeg"]) == 0 and find_tool("ffmpeg") and find_tool("ffprobe"):
            return
    if IS_WIN and shutil.which("winget"):
        subprocess.call(["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--silent",
                         "--accept-package-agreements", "--accept-source-agreements"])
        if find_tool("ffmpeg") and find_tool("ffprobe"):
            return
    _download_ffmpeg()


def ensure_ffmpeg() -> tuple[str, str]:
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    if not AUTO_INSTALL:
        sys.exit("ffmpeg が見つかりません。インストールしてからもう一度実行してください。")
    install_ffmpeg()
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not (ffmpeg and ffprobe):
        sys.exit("ffmpeg を自動で入れられませんでした。README の手順で手動インストールしてください。")
    log(f"ffmpeg を使用: {ffmpeg}")
    return ffmpeg, ffprobe


def doctor(fix: bool = False) -> bool:
    ok = True
    print(f"Python      : {sys.version.split()[0]} ({sys.executable})")
    miss = missing_packages()
    print(f"ライブラリ  : {'OK' if not miss else '不足 → ' + ', '.join(miss)}")
    ok &= not miss
    ff, fp = find_tool("ffmpeg"), find_tool("ffprobe")
    print(f"ffmpeg      : {ff or '見つかりません'}")
    print(f"ffprobe     : {fp or '見つかりません'}")
    ok &= bool(ff and fp)
    bgm = HOME_DIR / "bgm" / "library.json"
    print(f"BGM         : {'ダウンロード済み' if bgm.exists() else '未ダウンロード（初回の編集時に自動で取得）'}")
    if fix and not ok:
        ensure_python_packages()
        ensure_ffmpeg()
        return doctor(fix=False)
    print("→ すべて OK" if ok else "→ 足りないものがあります（gamecut doctor --fix で自動インストール）")
    return ok
