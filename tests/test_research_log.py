import gzip
import json
import os
import time
from datetime import datetime, timezone

from research_log import ResearchJsonlLogger, ResearchLogConfig


def test_research_jsonl_logger_appends_and_writes_summary(tmp_path):
    logger = ResearchJsonlLogger(
        ResearchLogConfig(enabled=True, root_dir=str(tmp_path), cleanup_interval_seconds=9999),
        "BTC",
    )

    path = logger.append("quotes", "quote_snapshot", {"mid": 100.0, "bad": float("nan")})
    summary_path = logger.write_summary({"profiles": [{"name": "baseline", "score": 1.0}]})

    assert path is not None
    row = json.loads(path.read_text().splitlines()[0])
    assert row["symbol"] == "BTC"
    assert row["stream"] == "quotes"
    assert row["type"] == "quote_snapshot"
    assert row["bad"] is None

    assert summary_path is not None
    summary = json.loads(summary_path.read_text())
    assert summary["symbol"] == "BTC"
    assert summary["profiles"][0]["name"] == "baseline"


def test_research_jsonl_logger_compresses_previous_days_and_enforces_fifo(tmp_path):
    logger = ResearchJsonlLogger(
        ResearchLogConfig(
            enabled=True,
            root_dir=str(tmp_path),
            raw_retention_days=3,
            compressed_retention_days=30,
            max_total_bytes=80,
            cleanup_interval_seconds=9999,
        ),
        "BTC",
    )
    root = tmp_path / "BTC"
    root.mkdir(parents=True, exist_ok=True)
    old_raw = root / "quotes_2020-01-01.jsonl"
    old_raw.write_text('{"old":true}\n')

    result = logger.cleanup(now=time.time())

    gz_path = root / "quotes_2020-01-01.jsonl.gz"
    assert result["compressed"] == 1
    assert not old_raw.exists()
    assert gz_path.exists()
    with gzip.open(gz_path, "rt") as f:
        assert json.loads(f.readline())["old"] is True

    oldest = root / "oldest.jsonl.gz"
    newer = root / "newer.jsonl.gz"
    oldest.write_bytes(b"x" * 100)
    newer.write_bytes(b"y" * 100)
    old_time = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(oldest, (old_time, old_time))

    logger.cleanup(now=time.time())

    assert not oldest.exists()
