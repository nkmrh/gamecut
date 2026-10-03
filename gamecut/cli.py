"""コマンドの入口"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import setup

HELP = """\
ゲーム実況動画を自動で編集して YouTube にアップロードします。

使い方:
  gamecut                     メニューを表示（デスクトップの「ゲーム実況素材」フォルダを使用）
  gamecut run [フォルダ]       未作成の素材を編集してアップロード
  gamecut run [フォルダ] --no-upload   編集だけ
  gamecut upload [フォルダ]    アップロード待ち・失敗した動画をアップロード
  gamecut status [フォルダ]    状態を一覧表示
  gamecut auth [フォルダ]      YouTube にログイン
  gamecut init [フォルダ]      設定ファイルを作成
  gamecut doctor [--fix]      必要なものが揃っているか確認（--fix で自動インストール）
"""


def default_folder() -> Path:
    home = Path.home()
    desktops = [home / "Desktop"]
    if os.name == "nt":
        desktops = [home / "OneDrive" / "Desktop", home / "OneDrive" / "デスクトップ", home / "Desktop"]
    desktop = next((d for d in desktops if d.exists()), home)
    return desktop / "ゲーム実況素材"


def init_folder(folder: Path, quiet: bool = False) -> None:
    from .config import CONFIG_NAME, CONFIG_TEMPLATE, SIDECAR_EXAMPLE
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "bgm").mkdir(exist_ok=True)
    cfg = folder / CONFIG_NAME
    if not cfg.exists():
        cfg.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        (folder / "動画ごとの設定の例.yaml.txt").write_text(SIDECAR_EXAMPLE, encoding="utf-8")
        if not quiet:
            print(f"設定ファイルを作成しました: {cfg}")
    elif not quiet:
        print(f"設定ファイルはすでにあります: {cfg}")


def open_folder(folder: Path) -> None:
    if os.name == "nt":
        os.startfile(folder)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.call(["open", str(folder)])
    else:
        subprocess.call(["xdg-open", str(folder)])


def _cmd(name: str, folder: Path, args) -> int:
    from . import pipeline, youtube
    from .config import load_config, resolve

    if name == "init":
        init_folder(folder)
        return 0
    if not folder.exists():
        print(f"フォルダが見つかりません: {folder}")
        return 1
    cfg = load_config(folder, Path(args.config) if getattr(args, "config", None) else None)
    if name == "run":
        return 1 if pipeline.run_all(folder, cfg, upload=not args.no_upload, shorts=not args.no_shorts,
                                     only=args.only) else 0
    if name == "upload":
        return 1 if pipeline.upload_all(folder, cfg) else 0
    if name == "status":
        pipeline.status(folder, cfg)
        return 0
    if name == "auth":
        y = cfg["youtube"]
        try:
            svc = youtube.get_service(resolve(cfg, y["client_secrets"]), resolve(cfg, y["token"]))
            ch = svc.channels().list(part="snippet", mine=True).execute().get("items", [])
            print(f"ログインしました: {ch[0]['snippet']['title'] if ch else '(チャンネル名を取得できません)'}")
            return 0
        except youtube.AuthError as e:
            print(e)
            return 1
    raise ValueError(name)


def menu(folder: Path) -> None:
    init_folder(folder, quiet=True)
    items = {
        "1": ("編集してアップロード", "run", {}),
        "2": ("編集だけ（アップロードしない）", "run", {"no_upload": True}),
        "3": ("状態を見る", "status", {}),
        "4": ("YouTube にログイン", "auth", {}),
        "5": ("アップロード待ちを再アップロード", "upload", {}),
    }
    while True:
        print("\n========== ゲーム実況 自動編集 ==========")
        print(f" 素材フォルダ: {folder}")
        for k, (label, _, _) in items.items():
            print(f"  {k}. {label}")
        print("  6. 素材フォルダを開く")
        print("  7. 素材フォルダを変更する（フォルダをここにドラッグして Enter）")
        print("  0. 終了")
        try:
            choice = input("番号を入力してください > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "0":
            return
        if choice == "6":
            open_folder(folder)
            continue
        if choice == "7":
            p = input("フォルダ > ").strip().strip("'\"").replace("\\ ", " ")
            if p and Path(p).expanduser().is_dir():
                folder = Path(p).expanduser().resolve()
                init_folder(folder, quiet=True)
            else:
                print("フォルダが見つかりません。")
            continue
        if choice not in items:
            continue
        _, name, extra = items[choice]
        ns = argparse.Namespace(no_upload=extra.get("no_upload", False), no_shorts=False, only=None, config=None)
        try:
            _cmd(name, folder, ns)
        except KeyboardInterrupt:
            print("\n中断しました。")
        except Exception as e:
            print(f"エラー: {e}")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--no-auto-install" in argv:
        argv.remove("--no-auto-install")
        os.environ["GAMECUT_NO_AUTO_INSTALL"] = "1"
        setup.AUTO_INSTALL = False
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows のコンソールでも日本語を出す
    except Exception:
        pass

    p = argparse.ArgumentParser(prog="gamecut", description=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    for name in ("run", "upload", "status", "auth", "init", "menu"):
        sp = sub.add_parser(name)
        sp.add_argument("folder", nargs="?", default=None)
        sp.add_argument("--config", default=None, help="設定ファイルの場所")
        if name == "run":
            sp.add_argument("--no-upload", action="store_true", help="アップロードしない")
            sp.add_argument("--no-shorts", action="store_true", help="ショートを作らない")
            sp.add_argument("--only", default=None, help="この名前の素材だけ処理する")
    dp = sub.add_parser("doctor")
    dp.add_argument("--fix", action="store_true")

    # フォルダだけ渡された（アイコンへのドラッグ＆ドロップ）ときはメニューを開く
    if len(argv) == 1 and Path(argv[0]).is_dir():
        argv = ["menu", argv[0]]
    args = p.parse_args(argv)

    if args.cmd == "doctor":
        sys.exit(0 if setup.doctor(fix=args.fix) else 1)
    setup.ensure_python_packages()
    if args.cmd in (None, "menu"):
        folder = Path(args.folder).expanduser().resolve() if getattr(args, "folder", None) else default_folder()
        menu(folder)
        return
    folder = Path(args.folder).expanduser().resolve() if args.folder else Path.cwd()
    sys.exit(_cmd(args.cmd, folder, args))
