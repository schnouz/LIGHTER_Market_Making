#!/usr/bin/env python3
"""Decision gate for MM tournament candidates.

This report is intentionally stricter than the raw tournament report.  It is
meant for fast operational decisions: what to ignore, what to keep watching,
and what can become a small canary after enough live-paper evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

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


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_hours(slot: dict) -> float:
    started = _parse_dt(slot.get("started_at"))
    updated = _parse_dt(slot.get("updated_at"))
    if started and updated:
        return max((updated - started).total_seconds() / 3600.0, 0.0)
    if updated:
        return max((datetime.now(timezone.utc) - updated).total_seconds() / 3600.0, 0.0)
    return 0.0


def _total_pnl(slot: dict) -> float:
    realized = _float(slot.get("realized_pnl"))
    portfolio = _float(slot.get("portfolio_value"))
    initial = _float(slot.get("initial_portfolio_value"))
    return realized + (portfolio - initial - realized)


def _adverse_5s(slot: dict) -> float:
    adverse = slot.get("adverse_markout_bps_avg", {}) or {}
    if isinstance(adverse, dict):
        return _float(adverse.get("5.0", adverse.get("5")))
    return _float(adverse)


def _pnl_bps_on_volume(pnl: float, volume: float) -> float:
    return (pnl / volume * 10_000.0) if volume > 0 else 0.0


def discover_run_roots(paths: list[str]) -> list[Path]:
    if paths:
        return [Path(p).resolve() for p in paths]
    root = PROJECT_ROOT / "logs" / "tournament"
    if not root.exists():
        raise SystemExit(f"No tournament root found: {root}")
    return sorted([p for p in root.iterdir() if p.is_dir()])


def compact_params(slot: dict) -> str:
    p = slot.get("params", {}) or {}
    bits = []
    for key, label in (
        ("spread_multiplier", "sm"),
        ("cj_min_half_spread_bps", "minh"),
        ("cj_max_half_spread_bps", "maxh"),
        ("capital_usage_percent", "cap"),
        ("num_levels", "lvl"),
    ):
        if p.get(key) is not None:
            bits.append(f"{label}={p[key]}")
    return " ".join(bits)


def collect_rows(run_roots: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for run_root in run_roots:
        if not run_root.exists():
            continue
        for symbol_dir in sorted(p for p in run_root.iterdir() if p.is_dir() and (p / "grid").exists()):
            grid_dir = symbol_dir / "grid"
            trade_counts = load_trade_counts(str(grid_dir))
            for slot in load_state_files(str(grid_dir)):
                param_key = slot.get("param_key", "")
                fills = _int(slot.get("fill_count"))
                volume = _float(slot.get("total_volume"))
                pnl = _total_pnl(slot)
                age = _age_hours(slot)
                initial = _float(slot.get("initial_portfolio_value"), 1000.0) or 1000.0
                spread = _float(slot.get("spread_capture_bps_avg"))
                adverse = _adverse_5s(slot)
                boundary = _float(slot.get("inventory_boundary_ratio"))
                inventory_max = _float(slot.get("inventory_max_usd_seen"))
                fit = slot.get("cj_kappa_fit_quality")
                fit_quality = _float(fit, -1.0) if fit is not None else -1.0
                pnl_day = pnl * 24.0 / age if age > 0 else 0.0
                roi_day_bps = pnl_day / initial * 10_000.0 if initial > 0 else 0.0
                pnl_vol_bps = _pnl_bps_on_volume(pnl, volume)
                base_score = quality_score({**slot, "total_pnl": pnl})
                confidence = min(1.0, fills / 20.0) * min(1.0, age / 24.0)
                decision_score = (
                    pnl_day
                    + 0.03 * pnl_vol_bps
                    + 0.02 * spread
                    + 0.02 * base_score
                    - 0.05 * max(0.0, adverse)
                    - 4.0 * max(0.0, boundary - 0.35)
                    + 0.25 * confidence
                )
                rows.append(
                    {
                        "run": run_root.name,
                        "symbol": symbol_dir.name,
                        "file": slot.get("file", ""),
                        "param_key": param_key,
                        "params": compact_params(slot),
                        "fills": fills,
                        "trade_count": trade_counts.get(param_key, 0),
                        "volume": volume,
                        "pnl": pnl,
                        "pnl_day": pnl_day,
                        "roi_day_bps": roi_day_bps,
                        "pnl_vol_bps": pnl_vol_bps,
                        "age_hours": age,
                        "spread_bps": spread,
                        "adverse_5s_bps": adverse,
                        "inventory_boundary_ratio": boundary,
                        "inventory_max_usd": inventory_max,
                        "fit_quality": fit_quality,
                        "quality_score": base_score,
                        "decision_score": decision_score,
                    }
                )
    return rows


def classify(row: dict, args: argparse.Namespace) -> tuple[str, str]:
    if row["age_hours"] < args.min_age_hours:
        return "wait", f"age<{args.min_age_hours:g}h"
    if row["fills"] < args.min_fills:
        return "wait", f"fills<{args.min_fills}"
    if row["pnl"] <= 0:
        return "reject", "negative pnl"
    if row["pnl_vol_bps"] <= args.min_pnl_bps_on_volume:
        return "reject", "weak pnl/volume"
    if row["adverse_5s_bps"] > args.max_adverse_bps:
        return "reject", "adverse markout"
    if row["inventory_boundary_ratio"] > args.max_boundary_ratio:
        return "reject", "inventory boundary"
    if row["age_hours"] >= args.canary_age_hours and row["fills"] >= args.canary_min_fills:
        return "canary_candidate", "24h+ gate passed"
    return "watch", "needs more time"


def best_by_symbol(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    selected = {}
    for row in rows:
        status, reason = classify(row, args)
        row = {**row, "status": status, "reason": reason}
        previous = selected.get(row["symbol"])
        if previous is None or row["decision_score"] > previous["decision_score"]:
            selected[row["symbol"]] = row
    return sorted(selected.values(), key=lambda r: r["decision_score"], reverse=True)


def print_rows(title: str, rows: list[dict], top: int) -> None:
    print(title)
    print(
        f"{'#':>2} {'STATUS':<16} {'SYM':<8} {'AGE':>5} {'FILLS':>6} "
        f"{'PNL':>9} {'PNL/D':>9} {'ROI/D':>7} {'VOLBPS':>7} "
        f"{'SPRD':>7} {'ADV5':>7} {'INV':>5} {'SCORE':>8} PARAMS"
    )
    print("-" * 140)
    for idx, row in enumerate(rows[:top], 1):
        print(
            f"{idx:>2} {row['status']:<16} {row['symbol']:<8} "
            f"{row['age_hours']:>4.1f}h {row['fills']:>6} "
            f"${row['pnl']:>8.3f} ${row['pnl_day']:>8.3f} "
            f"{row['roi_day_bps']:>6.1f} {row['pnl_vol_bps']:>7.2f} "
            f"{row['spread_bps']:>7.2f} {row['adverse_5s_bps']:>7.2f} "
            f"{row['inventory_boundary_ratio']:>5.2f} {row['decision_score']:>8.2f} "
            f"{row['params']} | {row['run']}"
        )
        print(f"   reason: {row['reason']} | file: {row['file']}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast decision gate for MM tournament candidates")
    parser.add_argument("run_roots", nargs="*", help="Tournament run roots. Defaults to all logs/tournament runs.")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--min-age-hours", type=float, default=6.0)
    parser.add_argument("--canary-age-hours", type=float, default=24.0)
    parser.add_argument("--min-fills", type=int, default=8)
    parser.add_argument("--canary-min-fills", type=int, default=15)
    parser.add_argument("--min-pnl-bps-on-volume", type=float, default=0.0)
    parser.add_argument("--max-adverse-bps", type=float, default=8.0)
    parser.add_argument("--max-boundary-ratio", type=float, default=0.80)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = collect_rows(discover_run_roots(args.run_roots))
    for row in rows:
        status, reason = classify(row, args)
        row["status"] = status
        row["reason"] = reason

    actionable = [r for r in rows if r["status"] in {"canary_candidate", "watch"}]
    canary = [r for r in rows if r["status"] == "canary_candidate"]
    watch = [r for r in rows if r["status"] == "watch"]
    rejects = [r for r in rows if r["status"] == "reject"]
    waits = [r for r in rows if r["status"] == "wait"]
    top_rows = sorted(actionable, key=lambda r: r["decision_score"], reverse=True)
    symbol_rows = best_by_symbol(rows, args)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gates": {
            "min_age_hours": args.min_age_hours,
            "canary_age_hours": args.canary_age_hours,
            "min_fills": args.min_fills,
            "canary_min_fills": args.canary_min_fills,
            "min_pnl_bps_on_volume": args.min_pnl_bps_on_volume,
            "max_adverse_bps": args.max_adverse_bps,
            "max_boundary_ratio": args.max_boundary_ratio,
        },
        "counts": {
            "rows": len(rows),
            "canary_candidate": len(canary),
            "watch": len(watch),
            "reject": len(rejects),
            "wait": len(waits),
        },
        "top": top_rows[: args.top],
        "best_by_symbol": symbol_rows[: args.top],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print("MM DECISION GATE")
    print(f"Generated: {payload['generated_at']}")
    print(
        "Gates: "
        f"watch >= {args.min_age_hours:g}h/{args.min_fills} fills, "
        f"canary >= {args.canary_age_hours:g}h/{args.canary_min_fills} fills, "
        f"pnl/vol > {args.min_pnl_bps_on_volume:g} bps, "
        f"adverse <= {args.max_adverse_bps:g} bps"
    )
    print(
        f"Counts: rows={len(rows)} canary={len(canary)} "
        f"watch={len(watch)} reject={len(rejects)} wait={len(waits)}"
    )
    print()
    print_rows(f"TOP ACTIONABLE ({len(top_rows)} rows)", top_rows, args.top)
    print_rows("BEST BY SYMBOL", symbol_rows, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
