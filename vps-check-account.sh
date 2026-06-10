#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:10}
export WINEPREFIX="$HOME/mt5bot/wineprefix"
export WINEDEBUG=-all

cd "$HOME/mt5bot/app"
wine "$HOME/mt5bot/wineprefix/drive_c/users/teguh/AppData/Local/Programs/Python/Python311/python.exe" account_check.py
