#!/usr/bin/env python3
"""Build observational shadow/challenger reports from live MM logs."""

from __future__ import annotations

import argparse
import json
import os

from execution_quality import build_shadow_report, write_shadow_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize live execution quality and shadow guard profiles.")
    parser.add_argument("--symbol", default=os.getenv("MARKET_SYMBOL", "BTC"), help="Market symbol, e.g. BTC")
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"), help="Directory containing trade/markout logs")
    parser.add_argument("--output-dir", default=None, help="Output directory for the JSON report")
    parser.add_argument("--horizon", type=float, default=30.0, help="Preferred markout horizon in seconds")
    parser.add_argument("--limit", type=int, default=None, help="Optional max rows to read from each CSV")
    parser.add_argument("--stdout", action="store_true", help="Print full report JSON instead of a short summary")
    args = parser.parse_args()

    report = build_shadow_report(
        log_dir=args.log_dir,
        symbol=args.symbol,
        preferred_horizon_sec=args.horizon,
        limit=args.limit,
    )
    out_path = write_shadow_report(
        log_dir=args.log_dir,
        symbol=args.symbol,
        output_dir=args.output_dir,
        preferred_horizon_sec=args.horizon,
        limit=args.limit,
    )
    if args.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        print(f"Shadow report written: {out_path}")
        print(f"Symbol: {report['symbol']} | winner: {report['winner']}")
        print(
            "Fills: {count} | pnl ${pnl:.4f} | volume ${volume:.2f} | toxicity {tox:.3f}".format(
                count=summary["trades"]["count"],
                pnl=summary["trades"]["realized_pnl_usd"],
                volume=summary["trades"]["volume_usd"],
                tox=summary["toxicity"]["score"],
            )
        )
        for profile in report["profiles"]:
            print(f"- {profile['name']}: score={profile['score']:.4f} ({profile['mode']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
