#!/usr/bin/env bash
set -euo pipefail

ROOT="${LIGHTER_MM_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export MARKET_SYMBOL="${MARKET_SYMBOL:-HYPE}"
export LIGHTER_MM_CONFIG="${LIGHTER_MM_CONFIG:-$ROOT/configs/hype_live_mm.json}"
export CHALLENGER_LOG_ROOT="${CHALLENGER_LOG_ROOT:-$ROOT/logs/challengers_hype}"
exec "$ROOT/scripts/run_mm_spread_challenger.sh" "$@"
