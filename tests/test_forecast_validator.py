"""
tests/test_forecast_validator.py — out-of-sample validation harness.

Verifies the forecast/actual comparison logic without hitting Deribit.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from forecast_validator import (
    ActualMetrics,
    Finding,
    ForecastMetrics,
    SEVERITY_FAIL,
    SEVERITY_PASS,
    SEVERITY_WARNING,
    _bootstrap_pnl,
    _percentile,
    compare,
    compute_actual_metrics,
    overall_severity,
    validate_snapshot,
)


# ── Bootstrap / percentile primitives ──────────────────────────────────────────


def test_percentile_empty_returns_zero():
    assert _percentile([], 50.0) == 0.0


def test_percentile_single_value_returns_value():
    assert _percentile([42.0], 5.0) == 42.0
    assert _percentile([42.0], 95.0) == 42.0


def test_percentile_endpoints():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(xs, 0.0) == 1.0
    assert _percentile(xs, 100.0) == 5.0
    assert _percentile(xs, 50.0) == 3.0


class _FakeTrade:
    def __init__(self, pnl_usd: float):
        self.pnl_usd = pnl_usd


def test_bootstrap_pnl_returns_n_samples():
    trades = [_FakeTrade(100), _FakeTrade(-50), _FakeTrade(20)]
    out = _bootstrap_pnl(trades, n_per_sample=5, n_samples=200)
    assert len(out) == 200


def test_bootstrap_pnl_handles_empty_trades():
    assert _bootstrap_pnl([], n_per_sample=5) == []


# ── compute_actual_metrics ─────────────────────────────────────────────────────


def _write_synthetic_trades_csv(path: Path, trades: list[dict]) -> None:
    fieldnames = [
        "timestamp", "instrument", "option_type", "strike",
        "entry_price", "exit_price", "contracts",
        "pnl_btc", "pnl_usd", "equity_before", "equity_after",
        "btc_price", "iv_rank_at_entry", "dte_at_entry", "dte_at_close",
        "slippage_btc", "fill_time_sec", "reason", "mode",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in trades:
            full = {k: row.get(k, "") for k in fieldnames}
            w.writerow(full)


def test_compute_actual_metrics_no_trades_returns_zero(tmp_path):
    """No trades → zero return, zero drawdown, no PnL."""
    csv_path = tmp_path / "trades.csv"
    _write_synthetic_trades_csv(csv_path, [])
    metrics = compute_actual_metrics(
        csv_path,
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        starting_equity=10_000.0,
    )
    assert metrics.trades_count == 0
    assert metrics.total_return_pct == 0.0
    assert metrics.starting_equity == 10_000.0
    assert metrics.ending_equity == 10_000.0


def test_compute_actual_metrics_filters_by_date_window(tmp_path):
    """Trades outside the window must be ignored."""
    csv_path = tmp_path / "trades.csv"
    _write_synthetic_trades_csv(csv_path, [
        {"timestamp": "2026-01-15T08:00:00+00:00", "pnl_usd": "100.0",
         "entry_price": "0.02", "strike": "70000", "contracts": "0.1", "mode": "paper"},
        {"timestamp": "2026-02-15T08:00:00+00:00", "pnl_usd": "-50.0",   # outside window
         "entry_price": "0.02", "strike": "70000", "contracts": "0.1", "mode": "paper"},
        {"timestamp": "2026-01-20T08:00:00+00:00", "pnl_usd": "200.0",
         "entry_price": "0.02", "strike": "70000", "contracts": "0.1", "mode": "paper"},
    ])
    metrics = compute_actual_metrics(
        csv_path,
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        starting_equity=10_000.0,
    )
    assert metrics.trades_count == 2
    assert metrics.total_return_pct == pytest.approx((100.0 + 200.0) / 10_000.0 * 100.0)


def test_compute_actual_metrics_drawdown_replay(tmp_path):
    """
    Drawdown is path-dependent. With sequence [+200, -500, +100], peak=+200,
    trough=-300 → drawdown ≈ -4.95% on $10k.
    """
    csv_path = tmp_path / "trades.csv"
    _write_synthetic_trades_csv(csv_path, [
        {"timestamp": "2026-01-05T08:00:00+00:00", "pnl_usd": "200.0", "mode": "paper"},
        {"timestamp": "2026-01-10T08:00:00+00:00", "pnl_usd": "-500.0", "mode": "paper"},
        {"timestamp": "2026-01-15T08:00:00+00:00", "pnl_usd": "100.0", "mode": "paper"},
    ])
    metrics = compute_actual_metrics(
        csv_path,
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        starting_equity=10_000.0,
    )
    # Equity: 10000 → 10200 (peak) → 9700 (trough) → 9800
    # Drawdown from peak = (9700 - 10200) / 10200 = -4.90%
    assert metrics.max_drawdown_pct == pytest.approx(-4.902, abs=0.01)
    assert metrics.win_rate_pct == pytest.approx(2 / 3 * 100, abs=0.1)


def test_compute_actual_metrics_separates_paper_vs_real(tmp_path):
    csv_path = tmp_path / "trades.csv"
    _write_synthetic_trades_csv(csv_path, [
        {"timestamp": "2026-01-05T08:00:00+00:00", "pnl_usd": "10", "mode": "paper"},
        {"timestamp": "2026-01-06T08:00:00+00:00", "pnl_usd": "20", "mode": "testnet"},
        {"timestamp": "2026-01-07T08:00:00+00:00", "pnl_usd": "30", "mode": "live"},
    ])
    metrics = compute_actual_metrics(
        csv_path,
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        starting_equity=10_000.0,
    )
    assert metrics.paper_trades == 1
    assert metrics.real_trades == 2


# ── compare() — severity rules ─────────────────────────────────────────────────


def _forecast(
    return_ci=(0.0, 5.0),
    drawdown_ci=(-10.0, -1.0),
    trades_ci=(2.0, 8.0),
    win_rate=70.0,
    yield_pct=1.5,
) -> ForecastMetrics:
    return ForecastMetrics(
        expected_total_return_pct=2.5,
        expected_max_drawdown_pct=-5.0,
        expected_win_rate_pct=win_rate,
        expected_trades_count=5.0,
        expected_avg_premium_yield_pct=yield_pct,
        expected_avg_pnl_per_trade_usd=20.0,
        expected_sharpe_ratio=1.0,
        return_pct_ci=return_ci,
        drawdown_pct_ci=drawdown_ci,
        trades_count_ci=trades_ci,
    )


def test_compare_pass_when_actual_inside_all_cis():
    """Actual matches forecast → all PASS."""
    fc = _forecast()
    actual = ActualMetrics(
        total_return_pct=2.5, max_drawdown_pct=-3.0, win_rate_pct=72.0,
        trades_count=5, avg_premium_yield_pct=1.5,
    )
    findings = compare(fc, actual)
    assert overall_severity(findings) == SEVERITY_PASS


def test_compare_fail_when_return_far_below_lower_ci():
    """Return way below 5% CI → FAIL."""
    fc = _forecast(return_ci=(0.0, 5.0))
    actual = ActualMetrics(
        total_return_pct=-20.0,           # 20pp below lower CI; gap >> CI width
        max_drawdown_pct=-3.0, win_rate_pct=70.0, trades_count=5,
        avg_premium_yield_pct=1.5,
    )
    findings = compare(fc, actual)
    return_finding = next(f for f in findings if f.metric == "total_return_pct")
    assert return_finding.severity == SEVERITY_FAIL


def test_compare_warning_when_return_just_below_lower_ci():
    """Return below CI but within one CI width → WARNING."""
    fc = _forecast(return_ci=(0.0, 5.0))
    actual = ActualMetrics(
        total_return_pct=-2.0,            # 2pp below lower CI; gap < CI width (5pp)
        max_drawdown_pct=-3.0, win_rate_pct=70.0, trades_count=5,
        avg_premium_yield_pct=1.5,
    )
    findings = compare(fc, actual)
    return_finding = next(f for f in findings if f.metric == "total_return_pct")
    assert return_finding.severity == SEVERITY_WARNING


def test_compare_fail_when_drawdown_worse_than_ci():
    """Drawdown -25% when CI worst was -10% → FAIL."""
    fc = _forecast(drawdown_ci=(-10.0, -1.0))
    actual = ActualMetrics(
        total_return_pct=2.0, max_drawdown_pct=-25.0, win_rate_pct=70.0,
        trades_count=5, avg_premium_yield_pct=1.5,
    )
    findings = compare(fc, actual)
    dd_finding = next(f for f in findings if f.metric == "max_drawdown_pct")
    assert dd_finding.severity == SEVERITY_FAIL


def test_compare_warning_on_low_trade_count():
    """Trades 0 vs CI [2, 8] → WARNING (strategy not finding setups)."""
    fc = _forecast(trades_ci=(2.0, 8.0))
    actual = ActualMetrics(
        total_return_pct=0.0, max_drawdown_pct=0.0, win_rate_pct=0.0,
        trades_count=0, avg_premium_yield_pct=0.0,
    )
    findings = compare(fc, actual)
    tc_finding = next(f for f in findings if f.metric == "trades_count")
    assert tc_finding.severity == SEVERITY_WARNING


def test_compare_fail_on_large_win_rate_gap():
    """Win rate 70 → 30 actual: 40pp gap → FAIL (structural drift)."""
    fc = _forecast(win_rate=70.0)
    actual = ActualMetrics(
        total_return_pct=0.0, max_drawdown_pct=-1.0, win_rate_pct=30.0,
        trades_count=10, avg_premium_yield_pct=1.5,
    )
    findings = compare(fc, actual)
    wr_finding = next(f for f in findings if f.metric == "win_rate_pct")
    assert wr_finding.severity == SEVERITY_FAIL


def test_compare_warning_on_premium_yield_drift():
    """Yield 1.5% forecast vs 0.6% actual = 60% gap → WARNING."""
    fc = _forecast(yield_pct=1.5)
    actual = ActualMetrics(
        total_return_pct=0.0, max_drawdown_pct=-1.0, win_rate_pct=70.0,
        trades_count=10, avg_premium_yield_pct=0.6,
    )
    findings = compare(fc, actual)
    y_finding = next(f for f in findings if f.metric == "avg_premium_yield_pct")
    assert y_finding.severity == SEVERITY_WARNING


def test_compare_low_sample_size_warns():
    """Even when everything passes, < 5 trades adds a WARNING."""
    fc = _forecast()
    actual = ActualMetrics(
        total_return_pct=2.5, max_drawdown_pct=-3.0, win_rate_pct=70.0,
        trades_count=2, avg_premium_yield_pct=1.5,
    )
    findings = compare(fc, actual)
    sample_findings = [f for f in findings if f.metric == "sample_size"]
    assert len(sample_findings) == 1
    assert sample_findings[0].severity == SEVERITY_WARNING


def test_compare_zero_trades_does_not_fail_win_rate_or_yield():
    """
    With zero trades, win_rate and yield are undefined. Don't fire FAIL on
    them — the trade-count finding already captures the signal. Otherwise a
    completely-idle bot looks "FAILing" when really it just didn't trade.
    """
    fc = _forecast()
    actual = ActualMetrics(
        total_return_pct=0.0, max_drawdown_pct=0.0, win_rate_pct=0.0,
        trades_count=0, avg_premium_yield_pct=0.0,
    )
    findings = compare(fc, actual)
    wr_finding = next(f for f in findings if f.metric == "win_rate_pct")
    yield_finding = next(f for f in findings if f.metric == "avg_premium_yield_pct")
    assert wr_finding.severity == SEVERITY_PASS
    assert yield_finding.severity == SEVERITY_PASS
    # No FAIL anywhere; sample_size warning still fires.
    assert overall_severity(findings) != SEVERITY_FAIL
    assert any(f.metric == "sample_size" for f in findings)


# ── End-to-end: validate_snapshot persists results ─────────────────────────────


def test_validate_snapshot_writes_validation_block(tmp_path, monkeypatch):
    """
    Build a synthetic snapshot file + trades.csv, run validate_snapshot with
    --force, and verify the validation block is written back to disk with
    the right structure.
    """
    # Set up an isolated data dir
    monkeypatch.setenv("WHEEL_BOT_DATA_DIR", str(tmp_path))
    forecasts_dir = tmp_path / "forecasts"
    forecasts_dir.mkdir()

    # Synthetic trades.csv inside the date window
    trades_csv = tmp_path / "trades.csv"
    _write_synthetic_trades_csv(trades_csv, [
        {"timestamp": "2026-04-15T08:00:00+00:00", "pnl_usd": "150.0",
         "entry_price": "0.02", "strike": "70000", "contracts": "0.1",
         "mode": "paper"},
        {"timestamp": "2026-04-20T08:00:00+00:00", "pnl_usd": "100.0",
         "entry_price": "0.02", "strike": "70000", "contracts": "0.1",
         "mode": "paper"},
        {"timestamp": "2026-04-25T08:00:00+00:00", "pnl_usd": "75.0",
         "entry_price": "0.02", "strike": "70000", "contracts": "0.1",
         "mode": "paper"},
    ])

    snapshot_path = forecasts_dir / "forecast_20260401_120000.json"
    snapshot = {
        "snapshot_id": "20260401_120000",
        "created_at": "2026-04-01T12:00:00+00:00",
        "validate_after": "2026-05-01T12:00:00+00:00",
        "horizon_days": 30,
        "config": {},
        "market_at_snapshot": {"btc_price": 70000, "iv_rank": 0.5},
        "backtest_summary": {"starting_equity": 10_000.0},
        "forecast": {
            "expected_total_return_pct": 3.0,
            "expected_max_drawdown_pct": -3.0,
            "expected_win_rate_pct": 75.0,
            "expected_trades_count": 4.0,
            "expected_avg_premium_yield_pct": 1.5,
            "expected_avg_pnl_per_trade_usd": 75.0,
            "expected_sharpe_ratio": 1.5,
            "return_pct_ci": [0.0, 6.0],
            "drawdown_pct_ci": [-8.0, -1.0],
            "trades_count_ci": [2.0, 7.0],
        },
        "validation": None,
    }
    snapshot_path.write_text(json.dumps(snapshot))

    result = validate_snapshot(snapshot_path, force=True)
    assert result["validation"] is not None
    val = result["validation"]
    assert "validated_at" in val
    assert val["actual"]["trades_count"] == 3
    assert val["actual"]["total_return_pct"] == pytest.approx(3.25)   # 325/10000
    assert "findings" in val
    assert val["overall_status"] in (SEVERITY_PASS, SEVERITY_WARNING, SEVERITY_FAIL)

    # Persisted to disk
    reread = json.loads(snapshot_path.read_text())
    assert reread["validation"] is not None
    assert reread["validation"]["actual"]["trades_count"] == 3


def test_validate_snapshot_skips_if_already_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("WHEEL_BOT_DATA_DIR", str(tmp_path))
    (tmp_path / "forecasts").mkdir()
    snapshot_path = tmp_path / "forecasts" / "forecast_X.json"
    sentinel = "PRE_EXISTING_VALIDATION"
    snapshot = {
        "snapshot_id": "X",
        "created_at": "2026-01-01T00:00:00+00:00",
        "validate_after": "2026-01-02T00:00:00+00:00",
        "horizon_days": 1,
        "config": {},
        "market_at_snapshot": {},
        "backtest_summary": {"starting_equity": 1000.0},
        "forecast": {},
        "validation": {"sentinel": sentinel},
    }
    snapshot_path.write_text(json.dumps(snapshot))
    result = validate_snapshot(snapshot_path)
    assert result["validation"]["sentinel"] == sentinel  # unchanged
