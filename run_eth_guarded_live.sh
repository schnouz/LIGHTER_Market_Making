#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export MARKET_SYMBOL="${MARKET_SYMBOL:-ETH}"
export LIGHTER_MM_CONFIG="${LIGHTER_MM_CONFIG:-$PWD/configs/eth_guarded_live_mm.json}"
export LOG_DIR="${LOG_DIR:-$PWD/logs/eth_guarded}"
exec "$PWD/venv/bin/python" -u market_maker_v2.py --symbol "$MARKET_SYMBOL" --live
