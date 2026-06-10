#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:10}
export WINEPREFIX="$HOME/mt5bot/wineprefix"
export WINEDEBUG=-all

wine 'C:\Program Files\MetaTrader 5\terminal64.exe'
