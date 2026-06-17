"""Local JSONL research logger with rotation and FIFO retention.

This is intentionally local-first: live/research processes can append compact
events at high reliability without pushing every tick to Supabase.  Old raw
files are compressed, then the oldest compressed files are deleted when age or
size limits are reached.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(v) for v in value]
    return value


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(_safe_json_value(payload), f, separators=(",", ":"), sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


@dataclass(frozen=True, slots=True)
class ResearchLogConfig:
    enabled: bool = False
    root_dir: str = "logs/research"
    raw_retention_days: int = 3
    compressed_retention_days: int = 30
    max_total_bytes: int = 5 * 1024 * 1024 * 1024
    cleanup_interval_seconds: float = 3600.0
    compress_previous_days: bool = True


class ResearchJsonlLogger:
    """Append compact JSONL events to per-day stream files."""

    def __init__(self, config: ResearchLogConfig, symbol: str):
        self.config = config
        self.symbol = symbol
        self.root = Path(config.root_dir) / symbol
        self._lock = threading.Lock()
        self._last_cleanup = 0.0
        if self.config.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def append(self, stream: str, event_type: str, payload: dict[str, Any]) -> Optional[Path]:
        if not self.enabled:
            return None
        stream = self._sanitize_stream(stream)
        now = _utc_now()
        path = self.root / f"{stream}_{now.strftime('%Y-%m-%d')}.jsonl"
        event = {
            "ts": _iso_now(),
            "symbol": self.symbol,
            "stream": stream,
            "type": event_type,
            **payload,
        }
        line = json.dumps(_safe_json_value(event), separators=(",", ":"), sort_keys=True)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as f:
                f.write(line)
                f.write("\n")
            self.maybe_cleanup_locked()
        return path

    def write_summary(self, payload: dict[str, Any], name: str = "summary") -> Optional[Path]:
        if not self.enabled:
            return None
        path = self.root / f"{self._sanitize_stream(name)}.json"
        with self._lock:
            _atomic_json_write(
                path,
                {
                    "updated_at": _iso_now(),
                    "symbol": self.symbol,
                    **payload,
                },
            )
            self.maybe_cleanup_locked()
        return path

    def maybe_cleanup(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.maybe_cleanup_locked()

    def maybe_cleanup_locked(self) -> None:
        now = time.time()
        if now - self._last_cleanup < max(self.config.cleanup_interval_seconds, 1.0):
            return
        self._last_cleanup = now
        self.cleanup(now=now)

    def cleanup(self, *, now: Optional[float] = None) -> dict[str, int]:
        if not self.enabled:
            return {"compressed": 0, "deleted": 0}
        now = time.time() if now is None else now
        self.root.mkdir(parents=True, exist_ok=True)
        compressed = 0
        deleted = 0
        today = _utc_now().strftime("%Y-%m-%d")

        if self.config.compress_previous_days:
            for path in self.root.glob("*.jsonl"):
                if path.name.endswith(f"_{today}.jsonl"):
                    continue
                age_days = (now - path.stat().st_mtime) / 86400.0
                if age_days >= 1.0 or not path.name.endswith(f"_{today}.jsonl"):
                    gz_path = path.with_suffix(path.suffix + ".gz")
                    if not gz_path.exists():
                        with path.open("rb") as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
                            while True:
                                chunk = src.read(1024 * 1024)
                                if not chunk:
                                    break
                                dst.write(chunk)
                    path.unlink(missing_ok=True)
                    compressed += 1

        raw_cutoff = now - max(self.config.raw_retention_days, 0) * 86400.0
        gz_cutoff = now - max(self.config.compressed_retention_days, 0) * 86400.0
        for path in self.root.glob("*.jsonl"):
            if path.stat().st_mtime < raw_cutoff:
                path.unlink(missing_ok=True)
                deleted += 1
        for path in self.root.glob("*.jsonl.gz"):
            if path.stat().st_mtime < gz_cutoff:
                path.unlink(missing_ok=True)
                deleted += 1

        deleted += self._enforce_size_limit()
        return {"compressed": compressed, "deleted": deleted}

    def _enforce_size_limit(self) -> int:
        max_bytes = int(self.config.max_total_bytes)
        if max_bytes <= 0:
            return 0
        files = [path for path in self.root.glob("*") if path.is_file()]
        total = sum(path.stat().st_size for path in files)
        if total <= max_bytes:
            return 0
        deletable = sorted(
            (path for path in files if path.suffix == ".gz" or path.name.endswith(".jsonl.gz")),
            key=lambda path: path.stat().st_mtime,
        )
        deleted = 0
        for path in deletable:
            if total <= max_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size
            deleted += 1
        return deleted

    @staticmethod
    def _sanitize_stream(stream: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in stream.strip())
        return cleaned or "events"


def config_from_mapping(mapping: dict[str, Any], *, default_enabled: bool = False) -> ResearchLogConfig:
    return ResearchLogConfig(
        enabled=bool(mapping.get("enabled", default_enabled)),
        root_dir=str(mapping.get("root_dir", "logs/research")),
        raw_retention_days=int(mapping.get("raw_retention_days", 3)),
        compressed_retention_days=int(mapping.get("compressed_retention_days", 30)),
        max_total_bytes=int(mapping.get("max_total_mb", 5120)) * 1024 * 1024,
        cleanup_interval_seconds=float(mapping.get("cleanup_interval_seconds", 3600.0)),
        compress_previous_days=bool(mapping.get("compress_previous_days", True)),
    )
