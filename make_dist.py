"""配布用 zip（dist/gamecut.zip）を作る。

鍵ファイル・ログイン情報・出力・キャッシュは入れない。実行権限は zip 内に保持する。
"""
from __future__ import annotations

import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOP = "ゲーム実況編集ツール"
FILES = [
    "ゲーム実況編集.command", "ゲーム実況編集.bat", "gamecut.sh", "gamecut.bat", "gamecut_setup.ps1",
    "requirements.txt", "pyproject.toml", "README.md", "はじめにお読みください.txt",
]
EXECUTABLE = {"ゲーム実況編集.command", "gamecut.sh"}
FORBIDDEN = ("client_secret", "token.json", ".venv", "__pycache__", "出力", "作成済み")


def main() -> None:
    files = [ROOT / f for f in FILES] + sorted((ROOT / "gamecut").glob("*.py"))
    for f in files:
        if not f.exists():
            raise SystemExit(f"見つかりません: {f}")
        if any(x in str(f.relative_to(ROOT)) for x in FORBIDDEN):
            raise SystemExit(f"配布してはいけないファイルです: {f}")
    out = ROOT / "dist" / "gamecut.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            rel = f.relative_to(ROOT)
            info = zipfile.ZipInfo(f"{TOP}/{rel.as_posix()}")
            info.flag_bits |= 0x800  # ファイル名を UTF-8 として記録（日本語名の文字化け防止）
            mode = 0o755 if rel.name in EXECUTABLE else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, f.read_bytes())
    print(f"作成しました: {out}（{out.stat().st_size // 1024} KB, {len(files)} ファイル）")


if __name__ == "__main__":
    main()
