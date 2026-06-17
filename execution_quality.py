"""Execution-quality analytics and lightweight toxic-flow controls.

The live runner already records fills and post-fill markouts.  This module
turns those artifacts into two things:

- an offline shadow report for comparing candidate guard profiles;
- a small, deterministic toxic-flow decision used by the live quote loop.

It intentionally has no exchange dependency.
"""

from __future__ import annotations

import csv
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _finite(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _atomic_json_write(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


@dataclass(frozen=True, slots=True)
class ToxicFlowGuardConfig:
    enabled: bool = True
    observe_only: bool = False
    min_samples: int = 12
    adverse_threshold_bps: float = 3.0
    severe_adverse_bps: float = 8.0
    spread_capture_floor_bps: float = 0.5
    inventory_ratio_trigger: float = 0.25
    spread_widen_per_adverse_bps: float = 0.08
    max_spread_multiplier: float = 1.8
    suppress_risk_side: bool = True
    suppress_on_weak_spread_adverse: bool = False


@dataclass(frozen=True, slots=True)
class ToxicFlowDecision:
    active: bool = False
    observe_only: bool = False
    reason: str = "disabled"
    toxicity_score: float = 0.0
    adverse_bps: float = 0.0
    spread_capture_bps: float = 0.0
    sample_count: int = 0
    inventory_ratio: float = 0.0
    spread_multiplier: float = 1.0
    suppress_side: Optional[str] = None


_DEFAULT_TOXIC_FLOW_GUARD = ToxicFlowGuardConfig()


def evaluate_toxic_flow_guard(
    *,
    config: ToxicFlowGuardConfig,
    adverse_bps: float,
    sample_count: int,
    spread_capture_bps: float = 0.0,
    inventory_ratio: float = 0.0,
    position_size: float = 0.0,
) -> ToxicFlowDecision:
    """Return a conservative quote action from recent markout quality.

    The guard is deliberately monotonic and transparent: bad markouts widen
    quotes; bad markouts plus meaningful inventory can disable the side that
    would increase exposure.
    """
    if not config.enabled:
        return ToxicFlowDecision(reason="disabled")
    if sample_count < max(config.min_samples, 1):
        return ToxicFlowDecision(
            reason="insufficient_samples",
            sample_count=max(sample_count, 0),
            adverse_bps=max(adverse_bps, 0.0),
            spread_capture_bps=spread_capture_bps,
            inventory_ratio=max(inventory_ratio, 0.0),
            observe_only=config.observe_only,
        )

    adverse = max(float(adverse_bps or 0.0), 0.0)
    spread_capture = max(float(spread_capture_bps or 0.0), 0.0)
    inv_ratio = max(float(inventory_ratio or 0.0), 0.0)
    spread_deficit = max(0.0, config.spread_capture_floor_bps - spread_capture)
    excess = max(0.0, adverse - config.adverse_threshold_bps)
    if excess <= 0 and spread_deficit <= 0:
        return ToxicFlowDecision(
            reason="healthy",
            adverse_bps=adverse,
            spread_capture_bps=spread_capture,
            sample_count=sample_count,
            inventory_ratio=inv_ratio,
            observe_only=config.observe_only,
        )

    severe_den = max(config.severe_adverse_bps - config.adverse_threshold_bps, 1e-9)
    adverse_component = min(1.0, excess / severe_den)
    spread_component = min(1.0, spread_deficit / max(config.spread_capture_floor_bps, 1e-9))
    inventory_component = min(1.0, inv_ratio / max(config.inventory_ratio_trigger, 1e-9))
    toxicity_score = max(adverse_component, 0.5 * spread_component) * (0.6 + 0.4 * inventory_component)
    spread_multiplier = min(
        max(config.max_spread_multiplier, 1.0),
        1.0 + excess * max(config.spread_widen_per_adverse_bps, 0.0) + 0.15 * spread_component,
    )

    weak_spread_adverse = (
        config.suppress_on_weak_spread_adverse
        and spread_deficit > 0
        and adverse >= config.adverse_threshold_bps
        and inv_ratio >= config.inventory_ratio_trigger
    )

    suppress_side: Optional[str] = None
    if config.suppress_risk_side and inv_ratio >= config.inventory_ratio_trigger and (
        adverse >= config.severe_adverse_bps or weak_spread_adverse
    ):
        if position_size > 0:
            suppress_side = "buy"
        elif position_size < 0:
            suppress_side = "sell"

    reason = "toxic_flow"
    if suppress_side is not None:
        reason = (
            f"weak_spread_capture_suppress_{suppress_side}"
            if weak_spread_adverse and adverse < config.severe_adverse_bps
            else f"toxic_flow_suppress_{suppress_side}"
        )
    elif spread_deficit > 0 and excess <= 0:
        reason = "weak_spread_capture"

    return ToxicFlowDecision(
        active=True,
        observe_only=config.observe_only,
        reason=reason,
        toxicity_score=max(0.0, min(1.0, toxicity_score)),
        adverse_bps=adverse,
        spread_capture_bps=spread_capture,
        sample_count=sample_count,
        inventory_ratio=inv_ratio,
        spread_multiplier=spread_multiplier,
        suppress_side=suppress_side,
    )


def _read_csv(path: str, *, limit: Optional[int] = None) -> list[dict[str, str]]:
    try:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return []
    if limit is not None and limit > 0:
        return rows[-limit:]
    return rows


def _avg(values: Iterable[Optional[float]]) -> float:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return mean(clean) if clean else 0.0


def _sum(values: Iterable[Optional[float]]) -> float:
    return sum(v for v in values if v is not None and math.isfinite(v))


def summarize_execution_quality(
    *,
    trades_path: str,
    markouts_path: str,
    preferred_horizon_sec: float = 30.0,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Summarize live execution logs into a compact research payload."""
    trades = _read_csv(trades_path, limit=limit)
    markouts = _read_csv(markouts_path, limit=limit)

    notionals = [_finite(row.get("notional_usd")) for row in trades]
    deltas = [_finite(row.get("realized_pnl")) for row in trades]
    cumulatives = [_finite(row.get("realized_pnl_cumulative")) for row in trades]
    spread_captures = [_finite(row.get("spread_capture_bps")) for row in trades]
    inventory = [abs(_finite(row.get("inventory_after_usd"), 0.0) or 0.0) for row in trades]

    realized = next((v for v in reversed(cumulatives) if v is not None), None)
    if realized is None:
        realized = _sum(deltas)
    volume = _sum(notionals)
    pnl_bps = realized / volume * 10_000.0 if volume > 0 else 0.0
    wins = sum(1 for value in deltas if value is not None and value > 0)
    losses = sum(1 for value in deltas if value is not None and value < 0)
    flats = sum(1 for value in deltas if value is not None and abs(value) <= 1e-12)

    markouts_by_horizon: dict[str, dict[str, Any]] = {}
    horizon_keys: set[float] = set()
    for row in markouts:
        horizon = _finite(row.get("horizon_sec"))
        if horizon is not None:
            horizon_keys.add(horizon)

    for horizon in sorted(horizon_keys):
        rows = [row for row in markouts if _finite(row.get("horizon_sec")) == horizon]
        by_side: dict[str, dict[str, float | int]] = {}
        for side in ("buy", "sell"):
            side_rows = [row for row in rows if row.get("side") == side]
            by_side[side] = {
                "count": len(side_rows),
                "avg_markout_bps": _avg(_finite(r.get("markout_bps")) for r in side_rows),
                "avg_adverse_bps": _avg(_finite(r.get("adverse_bps")) for r in side_rows),
            }
        key = str(int(horizon) if horizon.is_integer() else horizon)
        markouts_by_horizon[key] = {
            "count": len(rows),
            "avg_markout_bps": _avg(_finite(row.get("markout_bps")) for row in rows),
            "avg_adverse_bps": _avg(_finite(row.get("adverse_bps")) for row in rows),
            "by_side": by_side,
        }

    target_horizon = None
    if horizon_keys:
        target_horizon = min(horizon_keys, key=lambda h: abs(h - preferred_horizon_sec))
    target_key = None
    target_markouts = {}
    if target_horizon is not None:
        target_key = str(int(target_horizon) if target_horizon.is_integer() else target_horizon)
        target_markouts = markouts_by_horizon.get(target_key, {})

    adverse = float(target_markouts.get("avg_adverse_bps", 0.0) or 0.0)
    spread_capture = _avg(spread_captures)
    toxicity_score = min(
        1.0,
        max(0.0, adverse / 10.0) + max(0.0, 0.5 - spread_capture) / 10.0,
    )

    return {
        "generated_at": _utc_now(),
        "files": {
            "trades": trades_path,
            "markouts": markouts_path,
        },
        "trades": {
            "count": len(trades),
            "wins": wins,
            "losses": losses,
            "flats": flats,
            "volume_usd": volume,
            "realized_pnl_usd": realized,
            "pnl_bps_on_volume": pnl_bps,
            "avg_notional_usd": _avg(notionals),
            "avg_spread_capture_bps": spread_capture,
        },
        "inventory": {
            "avg_abs_inventory_usd": _avg(inventory),
            "max_abs_inventory_usd": max(inventory) if inventory else 0.0,
        },
        "markouts": markouts_by_horizon,
        "target_horizon_sec": target_horizon,
        "target_markout": target_markouts,
        "toxicity": {
            "score": toxicity_score,
            "avg_adverse_bps": adverse,
            "avg_spread_capture_bps": spread_capture,
        },
    }


def build_shadow_report(
    *,
    log_dir: str,
    symbol: str,
    preferred_horizon_sec: float = 30.0,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Build an observational shadow report from recorded fills/markouts."""
    trades_path = os.path.join(log_dir, f"trades_{symbol}.csv")
    markouts_path = os.path.join(log_dir, f"markouts_{symbol}.csv")
    summary = summarize_execution_quality(
        trades_path=trades_path,
        markouts_path=markouts_path,
        preferred_horizon_sec=preferred_horizon_sec,
        limit=limit,
    )
    base_score = (
        summary["trades"]["pnl_bps_on_volume"]
        + 0.30 * summary["trades"]["avg_spread_capture_bps"]
        - 0.90 * summary["toxicity"]["avg_adverse_bps"]
        - 0.02 * summary["inventory"]["avg_abs_inventory_usd"]
    )
    adverse = summary["toxicity"]["avg_adverse_bps"]
    spread = summary["trades"]["avg_spread_capture_bps"]
    volume = summary["trades"]["volume_usd"]

    profiles = [
        {
            "name": "baseline",
            "mode": "observed",
            "score": base_score,
            "estimated_avoided_toxic_volume_usd": 0.0,
            "notes": "Observed live behavior; no hypothetical filtering.",
        },
        {
            "name": "toxic_guard_light",
            "mode": "shadow",
            "score": base_score + max(0.0, adverse - 3.0) * 0.35 - max(0.0, 0.5 - spread) * 0.25,
            "estimated_avoided_toxic_volume_usd": volume * min(0.20, max(0.0, adverse - 3.0) / 60.0),
            "notes": "Would widen when adverse markout rises; does not disable quoting while flat.",
        },
        {
            "name": "toxic_guard_inventory",
            "mode": "shadow",
            "score": base_score + max(0.0, adverse - 6.0) * 0.55,
            "estimated_avoided_toxic_volume_usd": volume * min(0.35, max(0.0, adverse - 6.0) / 50.0),
            "notes": "Would suppress only the risk-increasing side when inventory is already meaningful.",
        },
    ]
    profiles.sort(key=lambda row: row["score"], reverse=True)
    return {
        "generated_at": _utc_now(),
        "symbol": symbol,
        "summary": summary,
        "profiles": profiles,
        "winner": profiles[0]["name"] if profiles else None,
    }


def write_shadow_report(
    *,
    log_dir: str,
    symbol: str,
    output_dir: Optional[str] = None,
    preferred_horizon_sec: float = 30.0,
    limit: Optional[int] = None,
) -> str:
    output_dir = output_dir or os.path.join(log_dir, "shadow")
    os.makedirs(output_dir, exist_ok=True)
    report = build_shadow_report(
        log_dir=log_dir,
        symbol=symbol,
        preferred_horizon_sec=preferred_horizon_sec,
        limit=limit,
    )
    out_path = os.path.join(output_dir, f"shadow_{symbol}_summary.json")
    _atomic_json_write(out_path, report)
    return out_path


def config_from_mapping(mapping: dict[str, Any]) -> ToxicFlowGuardConfig:
    """Build a guard config from JSON/env-like values."""
    return ToxicFlowGuardConfig(
        enabled=bool(mapping.get("enabled", _DEFAULT_TOXIC_FLOW_GUARD.enabled)),
        observe_only=bool(mapping.get("observe_only", _DEFAULT_TOXIC_FLOW_GUARD.observe_only)),
        min_samples=int(mapping.get("min_samples", _DEFAULT_TOXIC_FLOW_GUARD.min_samples)),
        adverse_threshold_bps=float(mapping.get("adverse_threshold_bps", _DEFAULT_TOXIC_FLOW_GUARD.adverse_threshold_bps)),
        severe_adverse_bps=float(mapping.get("severe_adverse_bps", _DEFAULT_TOXIC_FLOW_GUARD.severe_adverse_bps)),
        spread_capture_floor_bps=float(mapping.get("spread_capture_floor_bps", _DEFAULT_TOXIC_FLOW_GUARD.spread_capture_floor_bps)),
        inventory_ratio_trigger=float(mapping.get("inventory_ratio_trigger", _DEFAULT_TOXIC_FLOW_GUARD.inventory_ratio_trigger)),
        spread_widen_per_adverse_bps=float(
            mapping.get("spread_widen_per_adverse_bps", _DEFAULT_TOXIC_FLOW_GUARD.spread_widen_per_adverse_bps)
        ),
        max_spread_multiplier=float(mapping.get("max_spread_multiplier", _DEFAULT_TOXIC_FLOW_GUARD.max_spread_multiplier)),
        suppress_risk_side=bool(mapping.get("suppress_risk_side", _DEFAULT_TOXIC_FLOW_GUARD.suppress_risk_side)),
        suppress_on_weak_spread_adverse=bool(
            mapping.get(
                "suppress_on_weak_spread_adverse",
                _DEFAULT_TOXIC_FLOW_GUARD.suppress_on_weak_spread_adverse,
            )
        ),
    )


def decision_to_dict(decision: ToxicFlowDecision) -> dict[str, Any]:
    return asdict(decision)
