"""
black_swan.py — Stress-test a named config against 6 extreme market scenarios.

Scenarios
---------
  Historical (sliced from real Deribit OHLCV + IV):
    1. Black Thursday      2020-03-01 → 2020-05-31   BTC -59% in 48h
    2. 2022 Bear Market    2021-11-01 → 2022-11-15   BTC -77% over 12 months
    3. FTX Collapse        2022-10-15 → 2022-12-31   -25% in 5 days
    4. 2021 Bull Run       2021-01-01 → 2021-05-15   +120% euphoric rip

  Synthetic (generated price + IV DataFrames):
    5. Flatline            90 days ±3%,  IV pinned at 28%
    6. Flash Crash + V     -40% in 3 days (IV 200%), then 60-day recovery

Usage (CLI):
    python black_swan.py my_config_name

Usage (API):
    POST /black_swan/run   { "config_name": "max_yield_v1" }
    GET  /black_swan/results/{config_name}
    GET  /black_swan/status/{job_id}
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# ── Scenario definitions ───────────────────────────────────────────────────────

@dataclass
class ScenarioSpec:
    id: str
    name: str
    description: str
    scenario_type: str          # "historical" | "synthetic"
    # Historical only
    date_from: str = ""
    date_to: str = ""
    # Pass thresholds
    max_drawdown_pass: float = 50.0   # must stay BELOW this %
    min_return_pass: float | None = None  # if set, total return must exceed this %
    # Severity weight for overall verdict (1 = advisory, 5 = critical gate)
    severity_weight: int = 3


SCENARIOS: list[ScenarioSpec] = [
    ScenarioSpec(
        id="black_thursday",
        name="Black Thursday",
        description=(
            "BTC crashed -59% in 48 hours on March 12, 2020 — the most violent "
            "single-day collapse in BTC history. Tests survival under catastrophic "
            "gap-down conditions with extreme IV."
        ),
        scenario_type="historical",
        date_from="2020-02-01",
        date_to="2020-05-31",
        max_drawdown_pass=35.0,
        severity_weight=5,
    ),
    ScenarioSpec(
        id="bear_market_2022",
        name="2022 Bear Market",
        description=(
            "BTC ground from $69K to $16K across 12 months — relentless selling, "
            "no meaningful recovery, multiple capitulation events. Tests whether "
            "the strategy can survive a prolonged structural bear market."
        ),
        scenario_type="historical",
        date_from="2021-11-01",
        date_to="2022-11-15",
        max_drawdown_pass=55.0,
        severity_weight=4,
    ),
    ScenarioSpec(
        id="ftx_collapse",
        name="FTX Collapse",
        description=(
            "FTX imploded in November 2022, sending BTC -25% in 5 days on top of "
            "an already distressed market. Tests response to sudden contagion shock "
            "when the market is already at lows."
        ),
        scenario_type="historical",
        date_from="2022-10-01",
        date_to="2022-12-31",
        max_drawdown_pass=25.0,
        severity_weight=4,
    ),
    ScenarioSpec(
        id="bull_run_2021",
        name="2021 Bull Run",
        description=(
            "BTC ripped from $29K to $64K in 4 months. If the strategy holds "
            "covered calls, extreme upside blows through strikes. Tests call-leg "
            "behaviour during euphoric vertical price action."
        ),
        scenario_type="historical",
        date_from="2021-01-01",
        date_to="2021-05-15",
        max_drawdown_pass=25.0,
        severity_weight=3,
    ),
    ScenarioSpec(
        id="flatline",
        name="Flatline",
        description=(
            "90 days of dead sideways action — ±3% daily noise, IV crushed to 28%. "
            "Premium is thin. Tests whether theta income still exceeds costs and "
            "whether the bot avoids churning in a low-vol environment."
        ),
        scenario_type="synthetic",
        max_drawdown_pass=8.0,
        min_return_pass=0.0,   # must not lose money when market does nothing
        severity_weight=2,
    ),
    ScenarioSpec(
        id="flash_crash",
        name="Flash Crash + V Recovery",
        description=(
            "BTC drops -40% in 3 days (IV spikes to 200%), then fully recovers "
            "over 60 days in a clean V shape. Tests whether the bot survives the "
            "crash, doesn't panic-close at the bottom, and captures recovery premium."
        ),
        scenario_type="synthetic",
        max_drawdown_pass=40.0,
        severity_weight=5,
    ),
]

SCENARIO_MAP = {s.id: s for s in SCENARIOS}


# ── Result data classes ────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_name: str
    scenario_type: str
    description: str
    severity_weight: int
    # Simulated metrics
    max_drawdown_pct: float
    total_return_pct: float
    num_trades: int
    win_rate_pct: float
    sharpe_ratio: float
    # Pass/fail
    drawdown_pass: bool
    return_pass: bool
    passed: bool
    # Thresholds used
    max_drawdown_threshold: float
    min_return_threshold: float | None
    # Extra context
    error: str = ""
    sim_days: int = 0


@dataclass
class BlackSwanReport:
    config_name: str
    run_at: str
    # Per-scenario results
    scenarios: list[ScenarioResult] = field(default_factory=list)
    # Overall verdict
    verdict: str = "UNKNOWN"          # PASS | PARTIAL | FAIL
    passed_count: int = 0
    failed_count: int = 0
    critical_failures: list[str] = field(default_factory=list)
    # Prerequisite check
    prereqs_met: bool = True
    prereqs_missing: list[str] = field(default_factory=list)


# ── Data helpers ───────────────────────────────────────────────────────────────

def _fetch_historical_ohlcv(
    date_from: str,
    date_to: str,
) -> pd.DataFrame:
    """
    Fetch BTC-PERPETUAL daily OHLCV from Deribit for the given date range.
    Fetches an extra 60 days before date_from so the IV synthesizer has
    enough history to compute a meaningful rolling realised-vol baseline.
    """
    from deribit_client import DeribitPublicREST
    rest = DeribitPublicREST(timeout=30)

    # Extra pre-period for IV rank warm-up
    t0 = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = int((t0 - timedelta(days=90)).timestamp())
    end_ts   = int(t1.timestamp())

    logger.info(f"  Fetching historical OHLCV: {date_from} → {date_to}")
    raw = rest._get("get_tradingview_chart_data", {
        "instrument_name":  "BTC-PERPETUAL",
        "start_timestamp":  start_ts * 1_000,
        "end_timestamp":    end_ts   * 1_000,
        "resolution":       "1D",
    })
    if not raw or raw.get("status") == "no_data":
        raise RuntimeError(f"Deribit returned no OHLCV for {date_from}→{date_to}")

    df = pd.DataFrame({
        "date":   pd.to_datetime(raw["ticks"], unit="ms", utc=True).normalize(),
        "open":   pd.array(raw["open"],   dtype=float),
        "high":   pd.array(raw["high"],   dtype=float),
        "low":    pd.array(raw["low"],    dtype=float),
        "close":  pd.array(raw["close"],  dtype=float),
        "volume": pd.array(raw["volume"], dtype=float),
    })
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    logger.info(f"  -> {len(df)} bars fetched")
    return df


def _fetch_historical_iv(date_from: str, date_to: str) -> list:
    """
    Fetch Deribit historical IV for the given date range.
    Returns list of [ts_ms, iv] pairs (same format as Deribit's endpoint).
    Returns [] when Deribit has no coverage for the period (pre-2021 is sparse).
    """
    from deribit_client import DeribitPublicREST
    rest = DeribitPublicREST(timeout=30)
    try:
        raw = rest._get("get_historical_volatility", {"currency": "BTC"})
        if not raw:
            return []
        # Filter to the requested range (+/- 90 day buffer)
        t0_ms = (datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                 - timedelta(days=90)).timestamp() * 1000
        t1_ms = datetime.strptime(date_to, "%Y-%m-%d").replace(
            tzinfo=timezone.utc).timestamp() * 1000
        filtered = [[ts, iv] for ts, iv in raw if t0_ms <= ts <= t1_ms]
        logger.info(f"  -> {len(filtered)} IV data points for {date_from}→{date_to}")
        return filtered
    except Exception as exc:
        logger.warning(f"  IV fetch failed: {exc} — will synthesise from OHLCV")
        return []


def _make_synthetic_flatline(
    days: int = 90,
    start_price: float = 50_000.0,
    daily_noise_pct: float = 3.0,
    fixed_iv: float = 28.0,
    seed: int = 42,
) -> tuple[pd.DataFrame, list]:
    """
    Generate a synthetic flatline scenario:
      - Price wanders ±daily_noise_pct% per day but mean-reverts strongly.
      - IV is constant at fixed_iv%.

    Returns (ohlcv_df, iv_history_list).
    """
    rng = np.random.default_rng(seed)
    price = start_price
    rows = []
    base_date = datetime(2023, 1, 1, tzinfo=timezone.utc)

    for i in range(days):
        # Mean-reverting noise: pulls back toward start_price
        reversion = (start_price - price) / start_price * 0.1
        daily_ret = rng.normal(reversion, daily_noise_pct / 100.0)
        daily_ret = float(np.clip(daily_ret, -0.08, 0.08))

        open_  = price
        close_ = price * (1 + daily_ret)
        intra  = abs(daily_ret) + rng.uniform(0.005, 0.015)
        high_  = max(open_, close_) * (1 + intra * 0.4)
        low_   = min(open_, close_) * (1 - intra * 0.4)
        vol    = rng.uniform(15_000, 25_000)

        rows.append({
            "date":   base_date + timedelta(days=i),
            "open":   open_,
            "high":   high_,
            "low":    low_,
            "close":  close_,
            "volume": vol,
        })
        price = close_

    ohlcv = pd.DataFrame(rows)
    ohlcv["date"] = pd.to_datetime(ohlcv["date"], utc=True).dt.normalize()

    # Build iv_history: constant fixed_iv for all days + 90-day pre-period
    # (we need ≥60 rows to trigger the iv_history path in run_with_data)
    iv_history = []
    for i in range(days + 90):
        ts_ms = int((base_date + timedelta(days=i - 90)).timestamp() * 1000)
        # Add ±2% daily jitter to keep it realistic
        jitter = rng.uniform(-2.0, 2.0)
        iv_history.append([ts_ms, fixed_iv + jitter])

    return ohlcv, iv_history


def _make_synthetic_flash_crash(
    crash_pct: float = 40.0,
    crash_days: int = 3,
    recovery_days: int = 60,
    start_price: float = 50_000.0,
    crash_peak_iv: float = 200.0,
    recovery_iv: float = 60.0,
    seed: int = 99,
) -> tuple[pd.DataFrame, list]:
    """
    Generate a synthetic flash-crash + V-recovery scenario:
      - Pre-crash: 10 calm days to prime the IV window.
      - Crash: price drops crash_pct% over crash_days.
      - Recovery: price V-recovers to start_price over recovery_days.
      - IV spikes to crash_peak_iv on crash day 1, decays exponentially
        back to recovery_iv over the recovery period.

    Returns (ohlcv_df, iv_history_list).
    """
    rng = np.random.default_rng(seed)
    base_date = datetime(2023, 6, 1, tzinfo=timezone.utc)

    rows = []
    iv_row = []
    price = start_price
    pre_days = 30

    # Pre-crash: calm market, moderate IV ~60%
    for i in range(pre_days):
        daily_ret = rng.normal(0.001, 0.015)
        open_  = price
        close_ = price * (1 + daily_ret)
        high_  = max(open_, close_) * (1 + abs(daily_ret) * 0.4 + 0.005)
        low_   = min(open_, close_) * (1 - abs(daily_ret) * 0.4 - 0.005)
        vol    = rng.uniform(20_000, 30_000)
        rows.append({"date": base_date + timedelta(days=i),
                     "open": open_, "high": high_, "low": low_,
                     "close": close_, "volume": vol})
        ts_ms = int((base_date + timedelta(days=i)).timestamp() * 1000)
        iv_row.append([ts_ms, recovery_iv + rng.uniform(-5, 5)])
        price = close_

    crash_start = price
    crash_bottom = crash_start * (1 - crash_pct / 100.0)
    total_days = pre_days + crash_days + recovery_days

    # Crash phase: distribute the drop across crash_days
    for j in range(crash_days):
        # Exponential curve: steepest on day 1
        frac = (j + 1) / crash_days
        target_price = crash_start * (1 - (crash_pct / 100.0) * frac)
        open_  = price
        close_ = target_price
        high_  = open_ * 1.005
        low_   = close_ * 0.985
        vol    = rng.uniform(60_000, 120_000)
        day_idx = pre_days + j
        rows.append({"date": base_date + timedelta(days=day_idx),
                     "open": open_, "high": high_, "low": low_,
                     "close": close_, "volume": vol})
        # IV spikes on crash day 1, tapers slightly through crash
        iv_spike = crash_peak_iv * (1.0 - j * 0.05)
        ts_ms = int((base_date + timedelta(days=day_idx)).timestamp() * 1000)
        iv_row.append([ts_ms, iv_spike])
        price = close_

    # Recovery phase: V-shape back to start_price
    for k in range(recovery_days):
        frac = (k + 1) / recovery_days
        target_price = crash_bottom + (crash_start - crash_bottom) * frac
        daily_noise = rng.normal(0.0, 0.015)
        open_  = price
        close_ = target_price * (1 + daily_noise)
        high_  = max(open_, close_) * (1 + abs(daily_noise) * 0.3 + 0.005)
        low_   = min(open_, close_) * (1 - abs(daily_noise) * 0.3 - 0.005)
        vol    = rng.uniform(25_000, 45_000)
        day_idx = pre_days + crash_days + k
        rows.append({"date": base_date + timedelta(days=day_idx),
                     "open": open_, "high": high_, "low": low_,
                     "close": close_, "volume": vol})
        # IV decays exponentially from crash_peak_iv to recovery_iv
        iv_val = recovery_iv + (crash_peak_iv - recovery_iv) * np.exp(-k * 0.07)
        ts_ms = int((base_date + timedelta(days=day_idx)).timestamp() * 1000)
        iv_row.append([ts_ms, float(iv_val) + rng.uniform(-3, 3)])
        price = close_

    ohlcv = pd.DataFrame(rows)
    ohlcv["date"] = pd.to_datetime(ohlcv["date"], utc=True).dt.normalize()

    return ohlcv, iv_row


# ── Prerequisites check ────────────────────────────────────────────────────────

def check_prerequisites(config_name: str, bot_id: str | None = None) -> tuple[bool, list[str]]:
    """
    Return (all_met, missing_list).

    Required gates before the Black Swan test is meaningful:
      1. Backtest has been run (backtest_results.json exists for this bot, or
         config has total_return_pct set)
      2. Walk-forward optimisation has been run (walk_forward_results.json exists)
    """
    missing = []

    # Gate 1: backtest results
    bt_found = False
    if bot_id:
        bt_path = BASE_DIR / "data" / "backtest_results.json"
        if bt_path.exists():
            bt_found = True
    # Also accept optimizer results as proof of backtest
    wf_path = BASE_DIR / "data" / "optimizer" / "walk_forward_results.json"
    if not bt_found:
        # Check config summary for total_return_pct
        try:
            import config_store as _cs
            summary = _cs.load_config_by_name(config_name)
            if summary.get("total_return_pct") is not None:
                bt_found = True
        except Exception:
            pass
    if not bt_found:
        missing.append("Backtest not yet completed — run the Backtest step first")

    # Gate 2: walk-forward
    if not wf_path.exists():
        missing.append("Walk-forward optimisation not yet completed — run the Optimiser step first")

    return (len(missing) == 0, missing)


# ── Core runner ────────────────────────────────────────────────────────────────

def _run_scenario(
    spec: ScenarioSpec,
    backtester,        # Backtester instance (with config pre-loaded)
    config_lookback_months: int,
) -> ScenarioResult:
    """
    Run a single scenario and return its result.
    """
    import copy as _copy
    from config import BacktestConfig

    logger.info(f"  Running scenario: {spec.name} [{spec.scenario_type}]")

    try:
        if spec.scenario_type == "historical":
            ohlcv = _fetch_historical_ohlcv(spec.date_from, spec.date_to)
            iv_history = _fetch_historical_iv(spec.date_from, spec.date_to)
        elif spec.id == "flatline":
            ohlcv, iv_history = _make_synthetic_flatline()
        elif spec.id == "flash_crash":
            ohlcv, iv_history = _make_synthetic_flash_crash()
        else:
            raise ValueError(f"Unknown synthetic scenario id: {spec.id}")

        # Temporarily override lookback_months to cover the full scenario window
        # so _simulate() doesn't clip to a shorter period than we intend.
        if spec.scenario_type == "historical":
            t0 = datetime.strptime(spec.date_from, "%Y-%m-%d")
            t1 = datetime.strptime(spec.date_to, "%Y-%m-%d")
            scenario_months = max(1, int((t1 - t0).days / 30) + 1)
        else:
            # Synthetic scenarios are ≤ ~4 months; use full extent
            scenario_months = max(1, int(len(ohlcv) / 30) + 1)

        original_lookback = backtester._cfg.backtest.lookback_months
        backtester._cfg.backtest.lookback_months = scenario_months

        try:
            bt_result = backtester.run_with_data(
                ohlcv_df=ohlcv,
                iv_history=iv_history,
                iv_window=min(365, len(ohlcv)),
            )
        finally:
            backtester._cfg.backtest.lookback_months = original_lookback

        drawdown_pass = bt_result.max_drawdown_pct <= spec.max_drawdown_pass
        return_pass = True
        if spec.min_return_pass is not None:
            return_pass = bt_result.total_return_pct >= spec.min_return_pass

        passed = drawdown_pass and return_pass

        return ScenarioResult(
            scenario_id=spec.id,
            scenario_name=spec.name,
            scenario_type=spec.scenario_type,
            description=spec.description,
            severity_weight=spec.severity_weight,
            max_drawdown_pct=round(bt_result.max_drawdown_pct, 2),
            total_return_pct=round(bt_result.total_return_pct, 2),
            num_trades=bt_result.num_cycles,
            win_rate_pct=round(bt_result.win_rate_pct, 1),
            sharpe_ratio=round(bt_result.sharpe_ratio, 2),
            drawdown_pass=drawdown_pass,
            return_pass=return_pass,
            passed=passed,
            max_drawdown_threshold=spec.max_drawdown_pass,
            min_return_threshold=spec.min_return_pass,
            sim_days=len(ohlcv),
        )

    except Exception as exc:
        logger.error(f"  Scenario {spec.id} failed with error: {exc}")
        return ScenarioResult(
            scenario_id=spec.id,
            scenario_name=spec.name,
            scenario_type=spec.scenario_type,
            description=spec.description,
            severity_weight=spec.severity_weight,
            max_drawdown_pct=0.0,
            total_return_pct=0.0,
            num_trades=0,
            win_rate_pct=0.0,
            sharpe_ratio=0.0,
            drawdown_pass=False,
            return_pass=False,
            passed=False,
            max_drawdown_threshold=spec.max_drawdown_pass,
            min_return_threshold=spec.min_return_pass,
            error=str(exc)[:300],
        )


def _compute_verdict(
    results: list[ScenarioResult],
) -> tuple[str, int, int, list[str]]:
    """
    Compute overall PASS / PARTIAL / FAIL verdict.

    Logic:
      - Any scenario with severity_weight == 5 that fails → FAIL
      - ≥ 2 scenarios failing → PARTIAL (if no critical failures)
      - All pass → PASS
    """
    passed = [r for r in results if r.passed and not r.error]
    failed = [r for r in results if not r.passed or r.error]
    critical_failures = [r.scenario_name for r in failed if r.severity_weight == 5]

    if critical_failures:
        verdict = "FAIL"
    elif len(failed) >= 2:
        verdict = "PARTIAL"
    elif len(failed) == 1:
        verdict = "PARTIAL"
    else:
        verdict = "PASS"

    return verdict, len(passed), len(failed), critical_failures


def run_black_swan(
    config_name: str,
    bot_id: str | None = None,
    skip_prereq_check: bool = False,
) -> BlackSwanReport:
    """
    Run all 6 black-swan scenarios for the given config.
    Returns a BlackSwanReport with per-scenario results and an overall verdict.
    """
    from config import load_config
    from backtester import Backtester
    import config_store as _cs

    report = BlackSwanReport(
        config_name=config_name,
        run_at=datetime.utcnow().isoformat() + "Z",
    )

    # ── Prerequisites ──────────────────────────────────────────────────────────
    if not skip_prereq_check:
        prereqs_met, missing = check_prerequisites(config_name, bot_id)
        report.prereqs_met = prereqs_met
        report.prereqs_missing = missing
        if not prereqs_met:
            report.verdict = "BLOCKED"
            return report

    # ── Load config ────────────────────────────────────────────────────────────
    logger.info(f"Black Swan: loading config '{config_name}'")
    try:
        raw = _cs.load_config_by_name(config_name)
    except Exception as exc:
        report.prereqs_met = False
        report.prereqs_missing = [f"Could not load config '{config_name}': {exc}"]
        report.verdict = "BLOCKED"
        return report

    # Resolve config file path (configs/ or farm/)
    cfg_path = BASE_DIR / "configs" / f"{config_name}.yaml"
    if not cfg_path.exists():
        cfg_path = BASE_DIR / "farm" / config_name / "config.yaml"
    if not cfg_path.exists():
        report.prereqs_missing = [f"Config file not found for '{config_name}'"]
        report.verdict = "BLOCKED"
        return report

    try:
        config = load_config(cfg_path)
    except Exception as exc:
        report.prereqs_missing = [f"Failed to parse config: {exc}"]
        report.verdict = "BLOCKED"
        return report

    original_lookback = config.backtest.lookback_months
    backtester = Backtester(config=config)

    # ── Run scenarios ──────────────────────────────────────────────────────────
    logger.info(f"Black Swan: running {len(SCENARIOS)} scenarios for '{config_name}'")
    for i, spec in enumerate(SCENARIOS, 1):
        logger.info(f"[{i}/{len(SCENARIOS)}] {spec.name}")
        result = _run_scenario(spec, backtester, original_lookback)
        report.scenarios.append(result)

    # ── Verdict ────────────────────────────────────────────────────────────────
    verdict, passed_count, failed_count, critical_failures = _compute_verdict(report.scenarios)
    report.verdict = verdict
    report.passed_count = passed_count
    report.failed_count = failed_count
    report.critical_failures = critical_failures

    logger.info(
        f"Black Swan complete — verdict: {verdict}  "
        f"({passed_count} passed / {failed_count} failed)"
    )
    return report


# ── Persistence ────────────────────────────────────────────────────────────────

def _results_path(config_name: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"black_swan_{config_name}.json"


def save_report(report: BlackSwanReport) -> Path:
    path = _results_path(report.config_name)
    path.write_text(json.dumps(asdict(report), indent=2))
    return path


def load_report(config_name: str) -> dict | None:
    path = _results_path(config_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python black_swan.py <config_name>")
        sys.exit(1)
    config_name = sys.argv[1]
    skip_prereq = "--skip-prereqs" in sys.argv
    report = run_black_swan(config_name, skip_prereq_check=skip_prereq)
    save_report(report)
    print(json.dumps(asdict(report), indent=2))
