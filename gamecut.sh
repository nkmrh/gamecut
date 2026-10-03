#!/bin/bash
# gamecut の起動スクリプト（Mac / Linux）
# Python・仮想環境・ライブラリがなければ自動で用意してから gamecut を実行する。
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="$HOME/.gamecut"
VENV="$HOME_DIR/venv"
PY_VERSION="3.12.7"

say() { echo "[準備] $*"; }

ok_python() {
  [ -x "$1" ] || command -v "$1" >/dev/null 2>&1 || return 1
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
  for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
           "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3" \
           python3.13 python3.12 python3.11 python3.10 python3; do
    if ok_python "$p"; then command -v "$p" || echo "$p"; return 0; fi
  done
  return 1
}

install_python() {
  if command -v brew >/dev/null 2>&1; then
    say "Python を Homebrew で入れます"
    brew install python@3.12 && return 0
  fi
  if [ "$(uname)" = "Darwin" ]; then
    say "Python ${PY_VERSION} を python.org から入れます（Mac のパスワードを聞かれます）"
    pkg="$(mktemp -d)/python.pkg"
    curl -L --fail -o "$pkg" "https://www.python.org/ftp/python/${PY_VERSION}/python-${PY_VERSION}-macos11.pkg"
    sudo installer -pkg "$pkg" -target /
  else
    echo "Python 3.10 以上をインストールしてから、もう一度実行してください。"; exit 1
  fi
}

if [ ! -x "$VENV/bin/python" ]; then
  PY="$(find_python || true)"
  if [ -z "$PY" ]; then
    install_python
    PY="$(find_python)" || { echo "Python を見つけられませんでした。"; exit 1; }
  fi
  say "専用の環境を作成します（${VENV}）"
  mkdir -p "$HOME_DIR"
  "$PY" -m venv "$VENV"
fi

REQ="$DIR/requirements.txt"
HASH_FILE="$VENV/.requirements.sha"
NEW_HASH="$(shasum "$REQ" 2>/dev/null | cut -d' ' -f1 || cksum "$REQ" | cut -d' ' -f1)"
if [ ! -f "$HASH_FILE" ] || [ "$(cat "$HASH_FILE")" != "$NEW_HASH" ]; then
  say "必要なライブラリを入れます（初回は数分かかります）"
  "$VENV/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip
  "$VENV/bin/python" -m pip install --disable-pip-version-check -r "$REQ"
  echo "$NEW_HASH" > "$HASH_FILE"
fi

export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m gamecut "$@"
