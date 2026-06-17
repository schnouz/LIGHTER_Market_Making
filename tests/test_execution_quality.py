import csv
import json

from execution_quality import (
    ToxicFlowGuardConfig,
    build_shadow_report,
    evaluate_toxic_flow_guard,
    write_shadow_report,
)


def test_toxic_flow_guard_suppresses_risk_increasing_side_for_long_inventory():
    decision = evaluate_toxic_flow_guard(
        config=ToxicFlowGuardConfig(
            min_samples=4,
            adverse_threshold_bps=2.0,
            severe_adverse_bps=6.0,
            inventory_ratio_trigger=0.2,
        ),
        adverse_bps=7.0,
        sample_count=8,
        spread_capture_bps=0.4,
        inventory_ratio=0.5,
        position_size=0.01,
    )

    assert decision.active
    assert decision.suppress_side == "buy"
    assert decision.spread_multiplier > 1.0
    assert decision.reason == "toxic_flow_suppress_buy"


def test_toxic_flow_guard_observe_only_keeps_action_metadata():
    decision = evaluate_toxic_flow_guard(
        config=ToxicFlowGuardConfig(observe_only=True, min_samples=1, severe_adverse_bps=5.0),
        adverse_bps=8.0,
        sample_count=2,
        spread_capture_bps=0.2,
        inventory_ratio=0.8,
        position_size=-0.01,
    )

    assert decision.active
    assert decision.observe_only
    assert decision.suppress_side == "sell"


def test_toxic_flow_guard_suppresses_on_weak_spread_plus_adverse_markout():
    decision = evaluate_toxic_flow_guard(
        config=ToxicFlowGuardConfig(
            min_samples=4,
            adverse_threshold_bps=2.0,
            severe_adverse_bps=7.0,
            spread_capture_floor_bps=0.5,
            inventory_ratio_trigger=0.25,
            suppress_on_weak_spread_adverse=True,
        ),
        adverse_bps=2.4,
        sample_count=16,
        spread_capture_bps=0.35,
        inventory_ratio=0.47,
        position_size=-0.001,
    )

    assert decision.active
    assert decision.suppress_side == "sell"
    assert decision.reason == "weak_spread_capture_suppress_sell"


def test_toxic_flow_guard_does_not_suppress_weak_spread_when_flat():
    decision = evaluate_toxic_flow_guard(
        config=ToxicFlowGuardConfig(
            min_samples=4,
            adverse_threshold_bps=2.0,
            severe_adverse_bps=7.0,
            spread_capture_floor_bps=0.5,
            inventory_ratio_trigger=0.25,
            suppress_on_weak_spread_adverse=True,
        ),
        adverse_bps=2.4,
        sample_count=16,
        spread_capture_bps=0.35,
        inventory_ratio=0.0,
        position_size=0.0,
    )

    assert decision.active
    assert decision.suppress_side is None
    assert decision.reason == "toxic_flow"


def test_shadow_report_summarizes_trade_and_markout_logs(tmp_path):
    trade_path = tmp_path / "trades_BTC.csv"
    markout_path = tmp_path / "markouts_BTC.csv"
    with trade_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp",
            "symbol",
            "side",
            "price",
            "size",
            "level",
            "position_after",
            "realized_pnl",
            "available_capital",
            "portfolio_value",
            "simulated",
            "notional_usd",
            "fee_usd",
            "entry_vwap_after",
            "realized_pnl_cumulative",
            "mid_at_fill",
            "spread_capture_bps",
            "inventory_after_usd",
            "client_order_index",
            "exchange_order_index",
            "fill_source",
        ])
        writer.writerow(["t", "BTC", "buy", "100", "1", 0, "1", "0.02", "100", "100", "false", "100", "0", "", "0.02", "100", "1.2", "100", "1", "10", "test"])
        writer.writerow(["t", "BTC", "sell", "101", "1", 0, "0", "0.03", "100", "100", "false", "101", "0", "", "0.05", "101", "1.0", "0", "2", "11", "test"])

    with markout_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp",
            "symbol",
            "fill_id",
            "horizon_sec",
            "side",
            "fill_price",
            "size",
            "notional_usd",
            "mid_at_fill",
            "mid_at_markout",
            "markout_bps",
            "adverse_bps",
            "spread_capture_bps",
            "position_after",
            "realized_delta_usd",
            "realized_pnl_cumulative",
            "fill_source",
            "client_order_index",
            "exchange_order_index",
        ])
        writer.writerow(["t", "BTC", "f1", "30", "buy", "100", "1", "100", "100", "99.9", "-10", "10", "1.2", "1", "0", "0", "test", "1", "10"])
        writer.writerow(["t", "BTC", "f2", "30", "sell", "101", "1", "101", "101", "100.8", "20", "0", "1.0", "0", "0", "0", "test", "2", "11"])

    report = build_shadow_report(log_dir=str(tmp_path), symbol="BTC")
    out = write_shadow_report(log_dir=str(tmp_path), symbol="BTC")

    assert report["summary"]["trades"]["count"] == 2
    assert report["summary"]["trades"]["realized_pnl_usd"] == 0.05
    assert report["summary"]["markouts"]["30"]["count"] == 2
    assert report["profiles"][0]["name"] in {"baseline", "toxic_guard_light", "toxic_guard_inventory"}
    with open(out) as f:
        payload = json.load(f)
    assert payload["symbol"] == "BTC"
