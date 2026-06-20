#!/usr/bin/env bash
set -euo pipefail

ROOT="${LIGHTER_MM_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export MARKET_SYMBOL="${MARKET_SYMBOL:-LIT}"
export LIGHTER_MM_CONFIG="${LIGHTER_MM_CONFIG:-$ROOT/configs/lit_live_mm.json}"
export CHALLENGER_LOG_ROOT="${CHALLENGER_LOG_ROOT:-$ROOT/logs/challengers_lit}"
exec "$ROOT/scripts/run_mm_spread_challenger.sh" "$@"
