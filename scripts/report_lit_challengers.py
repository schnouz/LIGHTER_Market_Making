#!/usr/bin/env python3
"""Summarize LIT market-making dry-run challengers."""

from __future__ import annotations

from report_mm_challengers import main as generic_main


if __name__ == "__main__":
    import sys

    if "--symbol" not in sys.argv:
        sys.argv.extend(["--symbol", "LIT"])
    raise SystemExit(generic_main())
