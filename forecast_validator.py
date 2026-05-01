"""
forecast_validator.py — Out-of-sample validation: compare backtest forecasts
to real (paper or live) performance once the forecast horizon elapses.

Why this exists
---------------
Backtests are easy to fool — sizing bugs, look-ahead bias, structural mismatches
between simulated and real fills. A backtest that says "+25% / year" can still
deliver -5% in production. The only way to surface that gap is to capture the
forecast at a fixed point in time, wait for the horizon to elapse, then compare
to what actually happened during that window.

Workflow
--------
1. `python forecast_validator.py --create-snapshot --horizon-days 30`
   - Runs the backtester with the *current* config to produce a horizon
     forecast (expected return, drawdown, win rate, trade count) plus 5/95
     confidence intervals from bootstrap-resampling the historical trades.
   - Persists everything to data/forecasts/<snapshot_id>.json with a
     `validate_after` timestamp.

2. `python forecast_validator.py --validate`
   - Scans data/forecasts/ for snapshots whose `validate_after` has elapsed
     and don't yet have a `validation` block.
   - For each: reads trades.csv (and tick_log.csv if present) for the
     [created_at, validate_after] window, computes actual metrics, runs the
     comparison, writes the validation block back into the snapshot file.
   - Flags severity per metric: pass / warning / fail.

3. `python forecast_validator.py --list`
   - Tabulates all snapshots: id, status (pending / due / validated),
     headline divergences for the validated ones.

The CLI is designed to run on a daily cron — every day, validate any
snapshots that came due, and once a week create a fresh snapshot.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger


# ── File paths ─────────────────────────────────────────────────────────────────


def _data_dir() -> Path:
    """Resolve the bot's data directory (respecting WHEEL_BOT_DATA_DIR for farm bots)."""
    return Path(os.environ.get(
        "WHEEL_BOT_DATA_DIR",
        str(Path(__file__).parent / "data"),
    ))


def _forecasts_dir() -> Path:
    p = _data_dir() / "forecasts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _trades_csv_path() -> Path:
    return _data_dir() / "trades.csv"


# ── Multi-bot helpers (for the farm iteration loop) ───────────────────────────


def _slugify(name: str) -> str:
    """
    Mirror bot_farm.py's slugifier so we resolve to the same data dirs.
    `Safest V1` → `safest-v1`, `capital_roi_20260501_1813` → unchanged.
    """
    import re
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "config"


def list_paper_bot_data_dirs(repo_root: Path | None = None) -> list[tuple[str, Path]]:
    """
    Return [(bot_name, bot_data_dir), ...] for every config with status='paper'.

    bot_data_dir is the farm/<slug>/data/ path that bot_farm.py uses for
    each bot's isolated state. Even if the farm isn't running, this is
    where forecast snapshots SHOULD live so each bot is validated against
    its own trades.csv.

    Skips invalid YAML and configs without _meta.status. Always safe to
    call — never raises.
    """
    root = repo_root or Path(__file__).parent
    configs_dir = root / "configs"
    out: list[tuple[str, Path]] = []
    if not configs_dir.exists():
        return out
    try:
        import yaml
    except ImportError:
        return out
    for yaml_path in sorted(configs_dir.glob("*.yaml")):
        try:
            cfg = yaml.safe_load(yaml_path.read_text()) or {}
        except Exception:
            continue
        meta = cfg.get("_meta") or {}
        if meta.get("status") != "paper":
            continue
        name = meta.get("name") or yaml_path.stem
        slug = _slugify(name)
        bot_data_dir = root / "farm" / slug / "data"
        out.append((name, bot_data_dir))
    return out


# ── Severity levels ────────────────────────────────────────────────────────────


SEVERITY_PASS = "pass"
SEVERITY_WARNING = "warning"
SEVERITY_FAIL = "fail"


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class ForecastMetrics:
    """The headline numbers we expect to see over the forecast horizon."""

    expected_total_return_pct: float = 0.0
    expected_max_drawdown_pct: float = 0.0
    expected_win_rate_pct: float = 0.0
    expected_trades_count: float = 0.0
    expected_avg_premium_yield_pct: float = 0.0
    expected_avg_pnl_per_trade_usd: float = 0.0
    expected_sharpe_ratio: float = 0.0

    # 5/95 percentile bands from bootstrap resampling.
    return_pct_ci: tuple[float, float] = (0.0, 0.0)
    drawdown_pct_ci: tuple[float, float] = (0.0, 0.0)
    trades_count_ci: tuple[float, float] = (0.0, 0.0)


@dataclass
class ActualMetrics:
    """What actually happened in the window."""

    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    trades_count: int = 0
    avg_premium_yield_pct: float = 0.0
    avg_pnl_per_trade_usd: float = 0.0
    starting_equity: float = 0.0
    ending_equity: float = 0.0
    # Number of paper-mode trades vs live/testnet trades — useful for triage.
    paper_trades: int = 0
    real_trades: int = 0


@dataclass
class Finding:
    """One divergence between forecast and actual."""

    metric: str
    severity: str   # pass | warning | fail
    expected: Any
    actual: Any
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


# ── Forecast creation ──────────────────────────────────────────────────────────


def _bootstrap_pnl(trades: list, n_per_sample: int, n_samples: int = 2000) -> list[float]:
    """
    Bootstrap-sample total P&L over `n_per_sample` trades from `trades`.

    Returns the list of total P&L per resample. Use np.percentile(...) to get
    confidence intervals.

    Falls back gracefully when trades is empty or n_per_sample <= 0.
    """
    if not trades or n_per_sample <= 0:
        return []
    pnls = [t.pnl_usd for t in trades]
    out: list[float] = []
    for _ in range(n_samples):
        total = sum(random.choices(pnls, k=n_per_sample))
        out.append(total)
    return out


def _percentile(xs: list[float], p: float) -> float:
    """Simple percentile (linear interpolation). p in [0, 100]."""
    if not xs:
        return 0.0
    xs_sorted = sorted(xs)
    if len(xs_sorted) == 1:
        return xs_sorted[0]
    rank = (p / 100.0) * (len(xs_sorted) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    frac = rank - lo
    return xs_sorted[lo] + (xs_sorted[hi] - xs_sorted[lo]) * frac


def _historical_drawdown_distribution(
    starting_equity: float,
    trades: list,
    n_per_sample: int,
    n_samples: int = 2000,
) -> list[float]:
    """
    Approximate drawdown distribution by replaying random sequences of
    trades and computing the worst peak-to-trough drawdown.

    Returns the list of drawdown percentages (negative numbers). Use
    np.percentile with p=5 to get a "bad case" forecast.
    """
    if not trades or n_per_sample <= 0 or starting_equity <= 0:
        return []
    pnls = [t.pnl_usd for t in trades]
    out: list[float] = []
    for _ in range(n_samples):
        equity = starting_equity
        peak = equity
        worst_dd = 0.0
        for _ in range(n_per_sample):
            equity += random.choice(pnls)
            peak = max(peak, equity)
            dd = (equity - peak) / peak * 100.0 if peak > 0 else 0.0
            if dd < worst_dd:
                worst_dd = dd
        out.append(worst_dd)
    return out


def create_snapshot(
    horizon_days: int,
    btc_price_now: float = 0.0,
    iv_rank_now: float = 0.0,
    note: str = "",
    starting_equity_override: float | None = None,
) -> Path:
    """
    Run the backtester with the current config, scale its metrics to the
    forecast horizon, and persist the snapshot to data/forecasts/.

    `starting_equity_override` lets you create a forecast for an equity level
    different from the one in config.yaml — useful when the configured
    starting_equity is too small to trigger any backtest trades (e.g. $1k
    when strikes are $70k+).

    Returns the path to the snapshot file.
    """
    # Imported lazily so the CLI starts even if matplotlib isn't installed.
    import copy
    from backtester import Backtester
    from config import cfg

    bt_cfg = copy.deepcopy(cfg) if starting_equity_override is not None else cfg
    if starting_equity_override is not None:
        bt_cfg.backtest.starting_equity = float(starting_equity_override)

    logger.info(
        f"Creating forecast snapshot — horizon = {horizon_days} days, "
        f"equity = ${bt_cfg.backtest.starting_equity:,.0f}"
    )
    bt = Backtester(config=bt_cfg)
    results = bt.run()

    if results.num_cycles == 0:
        raise RuntimeError(
            f"Backtest produced zero trades at ${bt_cfg.backtest.starting_equity:,.0f} "
            f"starting equity — cannot build a forecast. Likely causes: equity is "
            f"below the minimum lot collateral (1 BTC × strike ≈ $70k+) or the "
            f"IV-rank threshold ({cfg.strategy.iv_rank_threshold:.0%}) is too high "
            f"for the lookback window. Try --starting-equity 100000 or lower the "
            f"IV threshold in config.yaml."
        )

    starting_equity = results.starting_equity

    # Days covered by the historical backtest
    if results.dates and len(results.dates) >= 2:
        backtest_days = max(
            (results.dates[-1] - results.dates[0]).days, 1
        )
    else:
        backtest_days = max(horizon_days, 1)

    # Expected number of trades in the forecast horizon, scaled from history
    trades_per_day = results.num_cycles / backtest_days
    expected_trades = trades_per_day * horizon_days

    # Bootstrap P&L over `expected_trades` trades — gives us a return CI
    n_per_sample = max(1, int(round(expected_trades)))
    pnl_samples = _bootstrap_pnl(results.trades, n_per_sample, n_samples=2000)
    return_pct_samples = [p / starting_equity * 100.0 for p in pnl_samples] if pnl_samples else [0.0]

    # Drawdown bootstrap
    dd_samples = _historical_drawdown_distribution(
        starting_equity=starting_equity,
        trades=results.trades,
        n_per_sample=n_per_sample,
    )

    # Trades count CI: Poisson approximation around lambda = expected_trades
    # Use bootstrap of binomial counts as a robust approximation
    trades_count_samples: list[float] = []
    if backtest_days > 0:
        for _ in range(2000):
            # Each historical day either had a trade open or didn't
            # Approximate: draw `horizon_days` Bernoulli trials with p=trades_per_day
            p = min(1.0, trades_per_day)
            count = sum(1 for _ in range(horizon_days) if random.random() < p)
            trades_count_samples.append(count)

    forecast = ForecastMetrics(
        expected_total_return_pct=round(
            sum(return_pct_samples) / len(return_pct_samples) if return_pct_samples else 0.0, 3
        ),
        expected_max_drawdown_pct=round(
            sum(dd_samples) / len(dd_samples) if dd_samples else 0.0, 3
        ),
        expected_win_rate_pct=round(results.win_rate_pct, 2),
        expected_trades_count=round(expected_trades, 2),
        expected_avg_premium_yield_pct=round(results.avg_premium_yield_pct, 4),
        expected_avg_pnl_per_trade_usd=round(results.avg_pnl_per_trade_usd, 2),
        expected_sharpe_ratio=round(results.sharpe_ratio, 3),
        return_pct_ci=(
            round(_percentile(return_pct_samples, 5), 3),
            round(_percentile(return_pct_samples, 95), 3),
        ),
        drawdown_pct_ci=(
            round(_percentile(dd_samples, 5), 3) if dd_samples else 0.0,
            round(_percentile(dd_samples, 95), 3) if dd_samples else 0.0,
        ),
        trades_count_ci=(
            round(_percentile(trades_count_samples, 5), 1) if trades_count_samples else 0.0,
            round(_percentile(trades_count_samples, 95), 1) if trades_count_samples else 0.0,
        ),
    )

    now = datetime.now(timezone.utc)
    validate_after = now + timedelta(days=horizon_days)
    snapshot_id = now.strftime("%Y%m%d_%H%M%S")

    # Capture the config snapshot (for drift detection if config changes mid-horizon)
    config_snapshot = {
        "iv_rank_threshold": cfg.strategy.iv_rank_threshold,
        "target_delta_min": cfg.strategy.target_delta_min,
        "target_delta_max": cfg.strategy.target_delta_max,
        "min_dte": cfg.strategy.min_dte,
        "max_dte": cfg.strategy.max_dte,
        "max_equity_per_leg": cfg.sizing.max_equity_per_leg,
        "max_open_legs": cfg.sizing.max_open_legs,
        "starting_equity": cfg.backtest.starting_equity,
        "lookback_months": cfg.backtest.lookback_months,
        "max_adverse_delta": cfg.risk.max_adverse_delta,
        "max_loss_per_leg": cfg.risk.max_loss_per_leg,
        "max_daily_drawdown": cfg.risk.max_daily_drawdown,
    }

    snapshot = {
        "snapshot_id": snapshot_id,
        "created_at": now.isoformat(),
        "validate_after": validate_after.isoformat(),
        "horizon_days": horizon_days,
        "note": note,
        "config": config_snapshot,
        "market_at_snapshot": {
            "btc_price": float(btc_price_now),
            "iv_rank": float(iv_rank_now),
        },
        "backtest_summary": {
            "num_cycles": results.num_cycles,
            "backtest_days": backtest_days,
            "starting_equity": starting_equity,
            "ending_equity": results.ending_equity,
            "total_return_pct": results.total_return_pct,
            "annualized_return_pct": results.annualized_return_pct,
            "sharpe_ratio": results.sharpe_ratio,
            "max_drawdown_pct": results.max_drawdown_pct,
            "win_rate_pct": results.win_rate_pct,
            # Capital-efficiency metrics — recorded so the Forecasts tab can
            # show what equity floor / margin ROI / yield-on-margin the
            # backtest predicts. Not yet compared to actual (would require
            # extending compute_actual_metrics to track margin usage from
            # trades.csv); for now this is informational context.
            "annualised_margin_roi": getattr(results, "annualised_margin_roi", 0.0),
            "premium_on_margin": getattr(results, "premium_on_margin", 0.0),
            "min_viable_capital": getattr(results, "min_viable_capital", 0.0),
            "avg_margin_utilization": getattr(results, "avg_margin_utilization", 0.0),
            "total_margin_deployed": getattr(results, "total_margin_deployed", 0.0),
            "trades_per_year": getattr(results, "trades_per_year", 0.0),
            "avg_pnl_per_trade_usd": getattr(results, "avg_pnl_per_trade_usd", 0.0),
        },
        "forecast": _dataclass_to_jsonable(forecast),
        "validation": None,
    }

    out_path = _forecasts_dir() / f"forecast_{snapshot_id}.json"
    out_path.write_text(json.dumps(snapshot, indent=2))
    logger.info(f"Forecast snapshot written: {out_path}")
    logger.info(
        f"  Expected return: {forecast.expected_total_return_pct:.2f}% "
        f"(CI {forecast.return_pct_ci[0]:.2f}% to {forecast.return_pct_ci[1]:.2f}%)"
    )
    logger.info(
        f"  Expected drawdown: {forecast.expected_max_drawdown_pct:.2f}% "
        f"(CI {forecast.drawdown_pct_ci[0]:.2f}% to {forecast.drawdown_pct_ci[1]:.2f}%)"
    )
    logger.info(
        f"  Expected trades: {forecast.expected_trades_count:.1f} "
        f"(CI {forecast.trades_count_ci[0]:.0f} to {forecast.trades_count_ci[1]:.0f})"
    )
    logger.info(f"  Validate after: {validate_after.isoformat()}")
    return out_path


def _dataclass_to_jsonable(obj) -> dict:
    """Convert dataclass to a JSON-serialisable dict (tuples → lists)."""
    d = asdict(obj)
    for k, v in list(d.items()):
        if isinstance(v, tuple):
            d[k] = list(v)
    return d


# ── Actual performance (read from trades.csv) ──────────────────────────────────


def compute_actual_metrics(
    trades_csv_path: Path,
    window_start: datetime,
    window_end: datetime,
    starting_equity: float,
) -> ActualMetrics:
    """
    Read trades.csv, filter to trades closed within [window_start, window_end],
    and compute the same headline metrics that the forecast predicted.

    Trades are matched by `timestamp` field (ISO format with timezone).
    """
    if not trades_csv_path.exists():
        return ActualMetrics(starting_equity=starting_equity, ending_equity=starting_equity)

    in_window = []
    with open(trades_csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.fromisoformat(row["timestamp"])
            except (KeyError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if window_start <= ts <= window_end:
                in_window.append(row)

    if not in_window:
        return ActualMetrics(starting_equity=starting_equity, ending_equity=starting_equity)

    pnls_usd = []
    yields = []
    paper = 0
    real = 0
    for row in in_window:
        try:
            pnl_usd = float(row.get("pnl_usd", 0.0))
        except (TypeError, ValueError):
            pnl_usd = 0.0
        pnls_usd.append(pnl_usd)
        try:
            entry_price = float(row.get("entry_price", 0.0))
            strike = float(row.get("strike", 0.0))
            contracts = float(row.get("contracts", 0.0))
            if strike > 0 and contracts > 0:
                # premium / collateral (yield per trade as %)
                yields.append((entry_price * contracts) / (strike * contracts) * 100.0)
        except (TypeError, ValueError):
            pass
        mode = (row.get("mode") or "").lower()
        if mode == "paper":
            paper += 1
        else:
            real += 1

    # Drawdown — replay the trades and track peak/trough
    equity = starting_equity
    peak = equity
    worst_dd = 0.0
    for p in pnls_usd:
        equity += p
        peak = max(peak, equity)
        dd_pct = (equity - peak) / peak * 100.0 if peak > 0 else 0.0
        if dd_pct < worst_dd:
            worst_dd = dd_pct

    total_pnl = sum(pnls_usd)
    ending_equity = starting_equity + total_pnl
    wins = sum(1 for p in pnls_usd if p >= 0)

    return ActualMetrics(
        total_return_pct=round(total_pnl / starting_equity * 100.0, 3) if starting_equity > 0 else 0.0,
        max_drawdown_pct=round(worst_dd, 3),
        win_rate_pct=round(wins / len(pnls_usd) * 100.0, 1) if pnls_usd else 0.0,
        trades_count=len(pnls_usd),
        avg_premium_yield_pct=round(sum(yields) / len(yields), 3) if yields else 0.0,
        avg_pnl_per_trade_usd=round(total_pnl / len(pnls_usd), 2) if pnls_usd else 0.0,
        starting_equity=starting_equity,
        ending_equity=round(ending_equity, 2),
        paper_trades=paper,
        real_trades=real,
    )


# ── Comparison ─────────────────────────────────────────────────────────────────


def compare(forecast: ForecastMetrics, actual: ActualMetrics) -> list[Finding]:
    """
    Compare forecast vs actual and produce one Finding per metric.

    Severity rules:
      - PASS: actual is inside the forecast 5/95 CI (or within tolerance for
        metrics without a CI).
      - WARNING: actual is outside the CI but the gap is small (one CI width
        or less), OR a positive surprise (better than upper CI).
      - FAIL: actual is materially worse than the lower CI (> one CI width).
    """
    findings: list[Finding] = []

    # ── Total return ──────────────────────────────────────────────────────────
    lo, hi = forecast.return_pct_ci
    actual_ret = actual.total_return_pct
    ci_width = max(hi - lo, 1e-9)
    if lo <= actual_ret <= hi:
        findings.append(Finding(
            metric="total_return_pct",
            severity=SEVERITY_PASS,
            expected=[lo, hi],
            actual=actual_ret,
            message=f"Return {actual_ret:+.2f}% inside 5/95 CI [{lo:+.2f}%, {hi:+.2f}%]",
        ))
    elif actual_ret > hi:
        findings.append(Finding(
            metric="total_return_pct",
            severity=SEVERITY_WARNING,
            expected=[lo, hi],
            actual=actual_ret,
            message=f"Return {actual_ret:+.2f}% ABOVE upper CI {hi:+.2f}% — surprise gain (verify methodology)",
        ))
    else:
        gap_widths = (lo - actual_ret) / ci_width
        sev = SEVERITY_FAIL if gap_widths > 1.0 else SEVERITY_WARNING
        findings.append(Finding(
            metric="total_return_pct",
            severity=sev,
            expected=[lo, hi],
            actual=actual_ret,
            message=(
                f"Return {actual_ret:+.2f}% BELOW lower CI {lo:+.2f}% "
                f"by {gap_widths:.1f}× CI width — strategy underperforming forecast"
            ),
        ))

    # ── Max drawdown ──────────────────────────────────────────────────────────
    dd_lo, dd_hi = forecast.drawdown_pct_ci  # both negative; lo is worse
    actual_dd = actual.max_drawdown_pct
    dd_width = max(abs(dd_lo - dd_hi), 1e-9)
    if actual_dd >= dd_lo:   # less drawdown than worst forecast = OK
        findings.append(Finding(
            metric="max_drawdown_pct",
            severity=SEVERITY_PASS,
            expected=[dd_lo, dd_hi],
            actual=actual_dd,
            message=f"Drawdown {actual_dd:.2f}% within forecast envelope [{dd_lo:.2f}%, {dd_hi:.2f}%]",
        ))
    else:
        gap_widths = (dd_lo - actual_dd) / dd_width
        sev = SEVERITY_FAIL if gap_widths > 1.0 else SEVERITY_WARNING
        findings.append(Finding(
            metric="max_drawdown_pct",
            severity=sev,
            expected=[dd_lo, dd_hi],
            actual=actual_dd,
            message=(
                f"Drawdown {actual_dd:.2f}% WORSE than 5%-CI worst {dd_lo:.2f}% "
                f"by {gap_widths:.1f}× CI width — risk model underestimates downside"
            ),
        ))

    # ── Trade count ────────────────────────────────────────────────────────────
    tc_lo, tc_hi = forecast.trades_count_ci
    actual_tc = actual.trades_count
    if tc_lo <= actual_tc <= tc_hi:
        findings.append(Finding(
            metric="trades_count",
            severity=SEVERITY_PASS,
            expected=[tc_lo, tc_hi],
            actual=actual_tc,
            message=f"Trade count {actual_tc} inside CI [{tc_lo:.0f}, {tc_hi:.0f}]",
        ))
    elif actual_tc < tc_lo:
        findings.append(Finding(
            metric="trades_count",
            severity=SEVERITY_WARNING,
            expected=[tc_lo, tc_hi],
            actual=actual_tc,
            message=(
                f"Only {actual_tc} trades vs CI {tc_lo:.0f}–{tc_hi:.0f}: "
                f"strategy not finding qualifying setups (IV threshold too high? "
                f"market regime change?)"
            ),
        ))
    else:
        findings.append(Finding(
            metric="trades_count",
            severity=SEVERITY_WARNING,
            expected=[tc_lo, tc_hi],
            actual=actual_tc,
            message=f"{actual_tc} trades exceeds CI upper {tc_hi:.0f} — over-trading vs forecast",
        ))

    # ── Zero-trades short-circuit ─────────────────────────────────────────────
    # When no trades closed in the window, win_rate and avg_yield are undefined.
    # Firing a FAIL on those metrics would be misleading — the trade-count
    # finding above already captures the "no trades" signal correctly.
    if actual.trades_count == 0:
        findings.append(Finding(
            metric="win_rate_pct",
            severity=SEVERITY_PASS,
            expected=forecast.expected_win_rate_pct,
            actual=None,
            message="Skipped: zero trades in window — win rate is undefined",
        ))
        findings.append(Finding(
            metric="avg_premium_yield_pct",
            severity=SEVERITY_PASS,
            expected=forecast.expected_avg_premium_yield_pct,
            actual=None,
            message="Skipped: zero trades in window — yield is undefined",
        ))
        findings.append(Finding(
            metric="sample_size",
            severity=SEVERITY_WARNING,
            expected=">=5 trades",
            actual=0,
            message=(
                "Zero trades in window — either the bot didn't run, the IV-rank "
                "threshold filtered everything out, or the strategy is dormant. "
                "Increase horizon or check bot uptime."
            ),
        ))
        return findings

    # ── Win rate ──────────────────────────────────────────────────────────────
    expected_wr = forecast.expected_win_rate_pct
    actual_wr = actual.win_rate_pct
    wr_gap = abs(expected_wr - actual_wr)
    if wr_gap <= 15.0:
        findings.append(Finding(
            metric="win_rate_pct",
            severity=SEVERITY_PASS,
            expected=expected_wr,
            actual=actual_wr,
            message=f"Win rate {actual_wr:.1f}% near forecast {expected_wr:.1f}% (gap {wr_gap:.1f}pp)",
        ))
    elif wr_gap <= 25.0:
        findings.append(Finding(
            metric="win_rate_pct",
            severity=SEVERITY_WARNING,
            expected=expected_wr,
            actual=actual_wr,
            message=f"Win rate {actual_wr:.1f}% diverges from forecast {expected_wr:.1f}% by {wr_gap:.1f}pp",
        ))
    else:
        findings.append(Finding(
            metric="win_rate_pct",
            severity=SEVERITY_FAIL,
            expected=expected_wr,
            actual=actual_wr,
            message=(
                f"Win rate {actual_wr:.1f}% vs forecast {expected_wr:.1f}% — "
                f"{wr_gap:.1f}pp gap suggests structural drift "
                f"(e.g. backtest pricing diverging from real fills)"
            ),
        ))

    # ── Premium yield ─────────────────────────────────────────────────────────
    expected_yield = forecast.expected_avg_premium_yield_pct
    actual_yield = actual.avg_premium_yield_pct
    if expected_yield > 0:
        yield_gap_pct = abs(expected_yield - actual_yield) / expected_yield * 100.0
        if yield_gap_pct <= 30.0:
            findings.append(Finding(
                metric="avg_premium_yield_pct",
                severity=SEVERITY_PASS,
                expected=expected_yield,
                actual=actual_yield,
                message=f"Yield {actual_yield:.3f}% near forecast {expected_yield:.3f}%",
            ))
        else:
            sev = SEVERITY_FAIL if yield_gap_pct > 60.0 else SEVERITY_WARNING
            findings.append(Finding(
                metric="avg_premium_yield_pct",
                severity=sev,
                expected=expected_yield,
                actual=actual_yield,
                message=(
                    f"Yield {actual_yield:.3f}% vs forecast {expected_yield:.3f}% "
                    f"({yield_gap_pct:.0f}% gap) — Black-Scholes backtest may be "
                    f"mispricing premiums vs real Deribit chain"
                ),
            ))

    # ── Sample size warning ───────────────────────────────────────────────────
    if actual.trades_count < 5:
        findings.append(Finding(
            metric="sample_size",
            severity=SEVERITY_WARNING,
            expected=">=5 trades",
            actual=actual.trades_count,
            message=(
                f"Only {actual.trades_count} closed trades in window — "
                f"comparison is statistically weak. Increase horizon or wait."
            ),
        ))

    return findings


def overall_severity(findings: list[Finding]) -> str:
    """Aggregate the worst severity across findings."""
    if any(f.severity == SEVERITY_FAIL for f in findings):
        return SEVERITY_FAIL
    if any(f.severity == SEVERITY_WARNING for f in findings):
        return SEVERITY_WARNING
    return SEVERITY_PASS


# ── Validation runner ──────────────────────────────────────────────────────────


def validate_snapshot(snapshot_path: Path, force: bool = False) -> dict:
    """
    Validate a single forecast snapshot if its horizon has elapsed.

    Returns the snapshot dict with the `validation` block populated.
    Writes back to disk in-place.

    `force` runs the comparison even before validate_after (useful for tests).
    """
    snap = json.loads(snapshot_path.read_text())
    if snap.get("validation") and not force:
        return snap   # already validated

    validate_after = datetime.fromisoformat(snap["validate_after"])
    if validate_after.tzinfo is None:
        validate_after = validate_after.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if now < validate_after and not force:
        return snap   # not yet due

    created_at = datetime.fromisoformat(snap["created_at"])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    starting_equity = float(snap["backtest_summary"].get("starting_equity", 0.0))
    actual = compute_actual_metrics(
        trades_csv_path=_trades_csv_path(),
        window_start=created_at,
        window_end=validate_after,
        starting_equity=starting_equity,
    )

    forecast_dict = snap["forecast"]
    forecast = ForecastMetrics(
        expected_total_return_pct=forecast_dict.get("expected_total_return_pct", 0.0),
        expected_max_drawdown_pct=forecast_dict.get("expected_max_drawdown_pct", 0.0),
        expected_win_rate_pct=forecast_dict.get("expected_win_rate_pct", 0.0),
        expected_trades_count=forecast_dict.get("expected_trades_count", 0.0),
        expected_avg_premium_yield_pct=forecast_dict.get("expected_avg_premium_yield_pct", 0.0),
        expected_avg_pnl_per_trade_usd=forecast_dict.get("expected_avg_pnl_per_trade_usd", 0.0),
        expected_sharpe_ratio=forecast_dict.get("expected_sharpe_ratio", 0.0),
        return_pct_ci=tuple(forecast_dict.get("return_pct_ci", [0.0, 0.0])),
        drawdown_pct_ci=tuple(forecast_dict.get("drawdown_pct_ci", [0.0, 0.0])),
        trades_count_ci=tuple(forecast_dict.get("trades_count_ci", [0.0, 0.0])),
    )

    findings = compare(forecast, actual)
    overall = overall_severity(findings)

    snap["validation"] = {
        "validated_at": now.isoformat(),
        "actual": asdict(actual),
        "findings": [f.to_dict() for f in findings],
        "overall_status": overall,
    }
    snapshot_path.write_text(json.dumps(snap, indent=2))
    return snap


def validate_all_due(force: bool = False) -> list[dict]:
    """Validate every snapshot whose horizon has elapsed and isn't yet validated."""
    out = []
    for path in sorted(_forecasts_dir().glob("forecast_*.json")):
        try:
            result = validate_snapshot(path, force=force)
            if result.get("validation"):
                out.append({
                    "path": str(path),
                    "snapshot_id": result["snapshot_id"],
                    "overall_status": result["validation"]["overall_status"],
                    "findings_count": len(result["validation"]["findings"]),
                })
        except Exception as exc:
            logger.error(f"Failed to validate {path}: {exc}")
    return out


def list_snapshots() -> list[dict]:
    """Return a summary list of all snapshots — id, status, key metrics."""
    out = []
    now = datetime.now(timezone.utc)
    for path in sorted(_forecasts_dir().glob("forecast_*.json")):
        try:
            snap = json.loads(path.read_text())
        except Exception:
            continue
        validate_after = datetime.fromisoformat(snap["validate_after"])
        if validate_after.tzinfo is None:
            validate_after = validate_after.replace(tzinfo=timezone.utc)
        if snap.get("validation"):
            status = snap["validation"]["overall_status"]
        elif now >= validate_after:
            status = "due"
        else:
            status = "pending"
        out.append({
            "path": str(path),
            "snapshot_id": snap["snapshot_id"],
            "created_at": snap["created_at"],
            "validate_after": snap["validate_after"],
            "horizon_days": snap["horizon_days"],
            "status": status,
        })
    return out


# ── CLI ────────────────────────────────────────────────────────────────────────


def _with_bot_data_dir(bot_data_dir: Path):
    """
    Context manager: temporarily set WHEEL_BOT_DATA_DIR so the module-level
    `_data_dir()` and friends resolve to the farm bot's isolated data path.
    Restores the original env on exit.
    """
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        prev = os.environ.get("WHEEL_BOT_DATA_DIR")
        os.environ["WHEEL_BOT_DATA_DIR"] = str(bot_data_dir)
        try:
            yield
        finally:
            if prev is None:
                del os.environ["WHEEL_BOT_DATA_DIR"]
            else:
                os.environ["WHEEL_BOT_DATA_DIR"] = prev
    return _ctx()


def _cli_create(args: argparse.Namespace) -> int:
    if getattr(args, "all_paper_bots", False):
        bots = list_paper_bot_data_dirs()
        if not bots:
            print("\n  No paper-status configs found in configs/.\n")
            return 1
        print(f"\nCreating snapshots for {len(bots)} paper bots…\n")
        ok = 0
        failed: list[tuple[str, str]] = []
        for name, bot_data_dir in bots:
            try:
                with _with_bot_data_dir(bot_data_dir):
                    out = create_snapshot(
                        horizon_days=args.horizon_days,
                        btc_price_now=args.btc_price or 0.0,
                        iv_rank_now=args.iv_rank or 0.0,
                        note=(args.note or "") + f" [bot={name}]",
                        starting_equity_override=args.starting_equity,
                    )
                print(f"  ✓ {name}: {out.name}")
                ok += 1
            except Exception as exc:
                print(f"  ✗ {name}: {exc}")
                failed.append((name, str(exc)))
        print(f"\n{ok}/{len(bots)} created. {len(failed)} failed.\n")
        return 0 if not failed else 1

    out = create_snapshot(
        horizon_days=args.horizon_days,
        btc_price_now=args.btc_price or 0.0,
        iv_rank_now=args.iv_rank or 0.0,
        note=args.note or "",
        starting_equity_override=args.starting_equity,
    )
    print(f"\n  Snapshot saved: {out}\n")
    return 0


def _cli_validate(args: argparse.Namespace) -> int:
    if getattr(args, "all_paper_bots", False):
        bots = list_paper_bot_data_dirs()
        if not bots:
            print("\n  No paper-status configs found in configs/.\n")
            return 1
        print(f"\nValidating snapshots across {len(bots)} paper bots…\n")
        any_fail = False
        for name, bot_data_dir in bots:
            try:
                with _with_bot_data_dir(bot_data_dir):
                    results = validate_all_due(force=args.force)
            except Exception as exc:
                print(f"  ✗ {name}: validate errored: {exc}")
                continue
            if not results:
                continue   # silent on no-due — keeps the report tight
            for r in results:
                print(
                    f"  {name:<32}  {r['snapshot_id']}  →  "
                    f"{r['overall_status'].upper():<8}  "
                    f"({r['findings_count']} findings)"
                )
                if r["overall_status"] == SEVERITY_FAIL:
                    any_fail = True
        print()
        return 2 if any_fail else 0

    results = validate_all_due(force=args.force)
    if not results:
        print("\n  No snapshots to validate (none due, or all already validated).\n")
        return 0
    for r in results:
        print(
            f"  {r['snapshot_id']}  →  {r['overall_status'].upper():<8}  "
            f"({r['findings_count']} findings)"
        )
    print()
    # Exit non-zero if any FAIL, so cron can detect issues
    if any(r["overall_status"] == SEVERITY_FAIL for r in results):
        return 2
    return 0


def _cli_list(args: argparse.Namespace) -> int:
    if getattr(args, "all_paper_bots", False):
        bots = list_paper_bot_data_dirs()
        if not bots:
            print("\n  No paper-status configs found in configs/.\n")
            return 0
        print()
        print(f"  {'BOT':<32}  {'ID':<18}  {'CREATED':<19}  {'HORIZON':>8}  {'STATUS':<10}")
        print(f"  {'-'*32}  {'-'*18}  {'-'*19}  {'-'*8}  {'-'*10}")
        total = 0
        for name, bot_data_dir in bots:
            with _with_bot_data_dir(bot_data_dir):
                snaps = list_snapshots()
            for s in snaps:
                print(
                    f"  {name:<32}  {s['snapshot_id']:<18}  "
                    f"{s['created_at'][:19]:<19}  {s['horizon_days']:>5}d   "
                    f"{s['status']:<10}"
                )
                total += 1
        if total == 0:
            print(f"\n  No snapshots in any paper bot's data/forecasts/ yet.")
        print()
        return 0

    snaps = list_snapshots()
    if not snaps:
        print("\n  No snapshots found in data/forecasts/\n")
        return 0
    print()
    print(f"  {'ID':<18}  {'CREATED':<25}  {'HORIZON':>8}  {'STATUS':<10}")
    print(f"  {'-'*18}  {'-'*25}  {'-'*8}  {'-'*10}")
    for s in snaps:
        print(
            f"  {s['snapshot_id']:<18}  {s['created_at'][:19]:<25}  "
            f"{s['horizon_days']:>5}d   {s['status']:<10}"
        )
    print()
    return 0


def _cli_show(args: argparse.Namespace) -> int:
    target = _forecasts_dir() / f"forecast_{args.snapshot_id}.json"
    if not target.exists():
        print(f"  Snapshot not found: {target}")
        return 1
    snap = json.loads(target.read_text())
    print(json.dumps(snap, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BTC Wheel Bot — forecast vs actual validation harness",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Create a new forecast snapshot from current config")
    p_create.add_argument("--horizon-days", type=int, default=30,
                          help="How many days the forecast covers (default: 30)")
    p_create.add_argument("--btc-price", type=float, default=0.0,
                          help="Optional: BTC spot at snapshot time (recorded for context)")
    p_create.add_argument("--iv-rank", type=float, default=0.0,
                          help="Optional: IV rank at snapshot time (recorded for context)")
    p_create.add_argument("--note", type=str, default="",
                          help="Optional: free-text note (e.g. 'after fixing collateral bug')")
    p_create.add_argument("--starting-equity", type=float, default=None,
                          help="Override config.yaml starting_equity for the backtest "
                               "(useful when configured equity is below the minimum "
                               "lot collateral). Recommended: match your live equity.")
    p_create.add_argument("--all-paper-bots", action="store_true", default=False,
                          dest="all_paper_bots",
                          help="Create one snapshot per paper-status config in configs/, "
                               "writing each into farm/<slug>/data/forecasts/. The farm "
                               "iteration loop uses this — every test bot gets its own "
                               "30-day forecast for cross-bot comparison.")
    p_create.set_defaults(func=_cli_create)

    p_validate = sub.add_parser("validate", help="Validate snapshots whose horizon has elapsed")
    p_validate.add_argument("--force", action="store_true",
                            help="Validate even if validate_after hasn't elapsed (testing only)")
    p_validate.add_argument("--all-paper-bots", action="store_true", default=False,
                            dest="all_paper_bots",
                            help="Validate due snapshots across every paper-status bot "
                                 "(walks farm/<slug>/data/forecasts/ for each).")
    p_validate.set_defaults(func=_cli_validate)

    p_list = sub.add_parser("list", help="List all snapshots and their status")
    p_list.add_argument("--all-paper-bots", action="store_true", default=False,
                        dest="all_paper_bots",
                        help="List snapshots from every paper-status bot's data dir.")
    p_list.set_defaults(func=_cli_list)

    p_show = sub.add_parser("show", help="Print a snapshot file as JSON")
    p_show.add_argument("snapshot_id", help="e.g. 20260501_120000")
    p_show.set_defaults(func=_cli_show)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
