#!/usr/bin/env python3
"""Summarize BTC market-making dry-run challengers."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean


PROFILES = (
    "baseline_current",
    "more_conservative",
    "profit_tighter",
    "trend_defensive",
    "ultra_selective",
    "fast_reprice_guarded",
    "one_level_defensive",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _read_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def summarize_profile(root: Path, profile: str) -> dict:
    log_dir = root / profile
    state = _read_state(log_dir / "dry_run_state.json")
    trades = _read_trades(log_dir / "trades_BTC.csv")

    notionals = [_float(row.get("notional_usd")) for row in trades]
    spread_captures = [_float(row.get("spread_capture_bps")) for row in trades if row.get("spread_capture_bps")]
    inventory = [_float(row.get("inventory_after_usd")) for row in trades if row.get("inventory_after_usd")]
    realized = [_float(row.get("realized_pnl_cumulative")) for row in trades if row.get("realized_pnl_cumulative")]

    fill_count = int(_float(state.get("fill_count"), len(trades)))
    volume_usd = _float(state.get("total_volume"), sum(notionals))

    return {
        "profile": profile,
        "status": "has_state" if state else "starting",
        "fills": fill_count,
        "volume_usd": round(volume_usd, 4),
        "realized_pnl_usd": round(_float(state.get("realized_pnl"), realized[-1] if realized else 0.0), 6),
        "portfolio_value": round(_float(state.get("portfolio_value")), 6),
        "position": round(_float(state.get("position")), 8),
        "inventory_max_usd_seen": round(_float(state.get("inventory_max_usd_seen"), max(inventory) if inventory else 0.0), 4),
        "inventory_boundary_ratio": round(_float(state.get("inventory_boundary_ratio")), 6),
        "spread_capture_bps_avg": round(
            _float(state.get("spread_capture_bps_avg"), mean(spread_captures) if spread_captures else 0.0),
            6,
        ),
        "markout_bps_avg": state.get("markout_bps_avg", {}),
        "adverse_markout_bps_avg": state.get("adverse_markout_bps_avg", {}),
        "updated_at": state.get("updated_at"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize BTC MM dry-run challengers")
    parser.add_argument(
        "--root",
        default=str(PROJECT_ROOT / "logs" / "challengers"),
        help="Challenger log root",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of table")
    args = parser.parse_args()

    root = Path(args.root)
    rows = [summarize_profile(root, profile) for profile in PROFILES]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0

    headers = [
        "profile",
        "status",
        "fills",
        "realized_pnl_usd",
        "volume_usd",
        "position",
        "inventory_max_usd_seen",
        "spread_capture_bps_avg",
        "updated_at",
    ]
    widths = {header: len(header) for header in headers}
    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len(str(row.get(header, ""))))
    print("  ".join(header.ljust(widths[header]) for header in headers))
    print("  ".join("-" * widths[header] for header in headers))
    for row in rows:
        print("  ".join(str(row.get(header, "")).ljust(widths[header]) for header in headers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
