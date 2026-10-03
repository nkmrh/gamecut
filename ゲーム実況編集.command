#!/bin/bash
# ダブルクリックで起動（Mac）
cd "$(dirname "$0")"
./gamecut.sh "$@"
echo
read -n 1 -s -r -p "何かキーを押すと閉じます"
