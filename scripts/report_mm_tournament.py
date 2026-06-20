#!/usr/bin/env python3
"""Aggregate multi-symbol grid tournament results."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from check_grid_results import load_state_files, load_trade_counts, quality_score  # noqa: E402


def _float(value, default=0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _slot_total(slot: dict) -> float:
    realized = _float(slot.get("realized_pnl"))
    portfolio = _float(slot.get("portfolio_value"))
    initial = _float(slot.get("initial_portfolio_value"))
    return realized + (portfolio - initial - realized)


def enrich_slots(slots: list[dict], trades: dict[str, int]) -> list[dict]:
    rows = []
    for slot in slots:
        row = dict(slot)
        row["total_pnl"] = _slot_total(row)
        row["trade_count"] = trades.get(row.get("param_key", ""), 0)
        row["quality_score"] = quality_score(row)
        rows.append(row)
    return rows


def discover_run_root(path: Path | None) -> Path:
    if path is not None:
        return path
    root = PROJECT_ROOT / "logs" / "tournament"
    runs = [p for p in root.iterdir() if p.is_dir()] if root.exists() else []
    if not runs:
        raise SystemExit(f"No tournament runs found under {root}")
    return max(runs, key=lambda p: p.stat().st_mtime)


def summarize_symbol(symbol_dir: Path) -> dict:
    grid_dir = symbol_dir / "grid"
    slots = enrich_slots(load_state_files(str(grid_dir)), load_trade_counts(str(grid_dir)))
    if not slots:
        return {
            "symbol": symbol_dir.name,
            "slots": 0,
            "fills": 0,
            "volume": 0.0,
            "best_score": None,
            "best_pnl": None,
        }
    filled = [s for s in slots if int(s.get("fill_count", 0) or 0) > 0]
    best_score = max(slots, key=lambda s: s.get("quality_score", -999.0))
    best_pnl = max(slots, key=lambda s: s.get("total_pnl", -10**9))
    return {
        "symbol": symbol_dir.name,
        "slots": len(slots),
        "filled_slots": len(filled),
        "fills": sum(int(s.get("fill_count", 0) or 0) for s in slots),
        "volume": sum(_float(s.get("total_volume")) for s in slots),
        "profitable_slots": sum(1 for s in slots if _float(s.get("total_pnl")) > 0),
        "best_score": compact_slot(best_score),
        "best_pnl": compact_slot(best_pnl),
    }


def compact_slot(slot: dict) -> dict:
    params = slot.get("params", {}) or {}
    adverse = slot.get("adverse_markout_bps_avg", {}) or {}
    return {
        "file": slot.get("file"),
        "score": round(_float(slot.get("quality_score")), 4),
        "total_pnl": round(_float(slot.get("total_pnl")), 6),
        "fills": int(slot.get("fill_count", 0) or 0),
        "volume": round(_float(slot.get("total_volume")), 4),
        "spread_capture_bps": round(_float(slot.get("spread_capture_bps_avg")), 4),
        "adverse_5s_bps": round(_float(adverse.get("5.0", adverse.get("5"))), 4),
        "spread_multiplier": params.get("spread_multiplier"),
        "max_half_spread_bps": params.get("cj_max_half_spread_bps"),
        "capital_usage_percent": params.get("capital_usage_percent"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report MM tournament results")
    parser.add_argument("run_root", nargs="?", help="Tournament run root. Defaults to latest logs/tournament run.")
    parser.add_argument("--json", action="store_true", help="Print JSON")
    parser.add_argument("--top", type=int, default=20, help="Number of global rows to print")
    args = parser.parse_args()

    run_root = discover_run_root(Path(args.run_root).resolve() if args.run_root else None)
    if not run_root.exists():
        raise SystemExit(f"Tournament run root not found: {run_root}")
    symbols = [p for p in sorted(run_root.iterdir()) if p.is_dir() and (p / "grid").exists()]
    summaries = [summarize_symbol(p) for p in symbols]

    all_slots: list[tuple[str, dict]] = []
    for p in symbols:
        grid_dir = p / "grid"
        for slot in enrich_slots(load_state_files(str(grid_dir)), load_trade_counts(str(grid_dir))):
            all_slots.append((p.name, slot))
    all_slots.sort(key=lambda item: item[1].get("quality_score", -999.0), reverse=True)

    payload = {
        "run_root": str(run_root),
        "symbols": summaries,
        "top_global": [
            {"symbol": symbol, **compact_slot(slot)}
            for symbol, slot in all_slots[: args.top]
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"MM TOURNAMENT REPORT: {run_root}")
    print()
    print(f"{'SYMBOL':<8} {'SLOTS':>5} {'FILLED':>6} {'FILLS':>7} {'VOL':>11} {'PROF':>5} {'BEST_SCORE':>10} {'BEST_PNL':>10} {'BEST FILE'}")
    print("-" * 120)
    for row in summaries:
        best_score = row.get("best_score") or {}
        best_pnl = row.get("best_pnl") or {}
        print(
            f"{row['symbol']:<8} {row.get('slots', 0):>5} {row.get('filled_slots', 0):>6} "
            f"{row.get('fills', 0):>7} ${row.get('volume', 0.0):>10.2f} "
            f"{row.get('profitable_slots', 0):>5} {best_score.get('score', 0):>10.2f} "
            f"${best_pnl.get('total_pnl', 0):>9.4f} {best_score.get('file', '')}"
        )

    print()
    print(f"TOP {args.top} GLOBAL BY QUALITY SCORE")
    print(f"{'#':>3} {'SYM':<8} {'SCORE':>9} {'PNL':>10} {'FILLS':>7} {'VOL':>11} {'SPRD':>7} {'ADV5':>7} {'sm':>5} {'max':>5} {'cap%':>6} FILE")
    print("-" * 132)
    for idx, (symbol, slot) in enumerate(all_slots[: args.top], 1):
        compact = compact_slot(slot)
        print(
            f"{idx:>3} {symbol:<8} {compact['score']:>9.2f} ${compact['total_pnl']:>9.4f} "
            f"{compact['fills']:>7} ${compact['volume']:>10.2f} "
            f"{compact['spread_capture_bps']:>7.2f} {compact['adverse_5s_bps']:>7.2f} "
            f"{str(compact['spread_multiplier']):>5} {str(compact['max_half_spread_bps']):>5} "
            f"{str(compact['capital_usage_percent']):>6} {compact['file']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
