#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export MARKET_SYMBOL="${MARKET_SYMBOL:-TAO}"
export LIGHTER_MM_CONFIG="${LIGHTER_MM_CONFIG:-$PWD/configs/tao_live_mm.json}"
export LOG_DIR="${LOG_DIR:-$PWD/logs}"
exec "$PWD/venv/bin/python" -u market_maker_v2.py --symbol "$MARKET_SYMBOL" --live
