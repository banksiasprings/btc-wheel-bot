"""
funding_dynamic_backtest.py — Gate 3 backtest harness for the Funding-Dynamic bot.

NOT a live deployment. The bot is being evaluated against the strict
go / no-go bar in spec §8.1 (`specs/04-funding-dynamic-spec.md`):

  - **Beat better of `funding`/`funding-smart` by ≥ +2 %/yr** in ≥ 2 of 3
    volatile windows (April 2021, LUNA cascade May 2022, ETF launch Jan 2024).
  - **Steady-baseline loss ≤ −2 %/yr** vs `funding-smart` (specialist allowance).
  - **Correlation with `funding-smart` < 0.85** (else parked as redundant).

The spec is structured so a clean null result IS a Gate 3 deliverable. If slope
contains no marginal edge over level, the bot gets parked with a written
negative-evidence verdict — same status as a kill, not a re-tune.

What this harness runs:

  1. Per-regime full-window backtest at all 27 sweep configs (108 runs total):
       slope_threshold       ∈ {1e-7, 2e-7, 4e-7}  / h     (Q4 anchor sweep)
       size_increment_step   ∈ {0.05, 0.10, 0.20}          (Q5 sizing sweep)
       slope_lookback_hours  ∈ {16, 24, 32}                 (Q5 brief Q2)
  2. Winning-config selection under the strict §8.1 bar.
  3. Trade-cost stress at winner: trade_cost_bps ∈ {3, 6, 12} × 4 regimes.
  4. Walk-forward 70/30 split of the full 2019-05 → 2026-05 series at the
     winning config (anti-cherry-pick check the regime windows weren't lucky).
  5. Head-to-head vs `funding` (positive_only=False) and `funding-smart`
     (positive_only=True) at all four regime windows + the walk-forward folds.
  6. Rolling 30-day correlation of Funding-Dynamic equity vs `funding-smart`
     equity at the winning config — strict bar: median rolling corr < 0.85.

All outputs under docs/gate3-reports/04-funding-dynamic-data/:
  - sweep_results.csv          — every (regime, config) → metrics row
  - winner.json                — chosen config + strict-bar verdict
  - fee_stress.csv             — winner × {3, 6, 12} bps × 4 regimes
  - walkforward_results.csv    — winning config across walk-forward folds
  - comparison_results.csv     — Dynamic vs Funding vs Funding-Smart per window
  - correlation_rolling.csv    — 30d-rolling corr of equity curves

The narrative pass/fail Gate 3 report lives at:
  docs/gate3-reports/04-funding-dynamic.md

Run:
    python3.11 funding_dynamic_backtest.py            # full Gate 3 run
    python3.11 funding_dynamic_backtest.py --quick    # smaller sweep for iteration
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

from income_bots import FundingBot, FundingDynamicBot

ROOT = Path(__file__).resolve().parent.parent
FUNDING_JSON = ROOT / "data" / "raw" / "deribit" / "funding_rates.json"
OUTDIR = ROOT / "docs" / "gate3-reports" / "04-funding-dynamic-data"
HOURS_PER_YEAR = 24 * 365
DEFAULT_CAPITAL = 10_000.0
WARMUP_BUFFER_HRS = 32   # max(slope_lookback_hours sweep) so every config seeds

# ── regime windows ────────────────────────────────────────────────────────────
# DATA CONSTRAINT: the cached Deribit funding history (`data/raw/deribit/
# funding_rates.json`) is monthly snapshots with ~59-day gaps between blocks.
# The spec's named windows (April 2021 alt-season, LUNA cascade May 2022, ETF
# launch Jan-Mar 2024) are mostly inside these gaps. The substitutions below
# pick the available 30-day contiguous block that best matches the spirit of
# each named regime. This adaptation is flagged in the Gate 3 report under
# "Data caveats" — the bake-off pattern stands; the calendar months don't.
#
# Volatile windows (3):
#   - 2021 ramp:   2021-02-18 → 2021-03-21 — the lead-in to the April spike.
#                  Mean rate +3.7e-5/h (≈0.09 %/8h), max-slope spikes ≥1.5e-5/h
#                  → the dataset's representation of the alt-season acceleration.
#   - LUNA tail:   2022-05-14 → 2022-06-14 — second half of the LUNA cascade
#                  + the immediate post-cascade unwind. Tests the negative-
#                  funding halt and saturation guard in their headline regime.
#   - ETF launch:  2024-02-03 → 2024-03-05 — tail of the Jan-Mar 2024 ETF
#                  window. Modern fee tiers; closest to real-money conditions.
#
# Steady baseline (1):
#   - 2023 mid:    2023-05-09 → 2023-06-09 — std_rate ≈ 8e-6 (one of the
#                  flattest 30-day stretches in the dataset). Bot expected to
#                  bleed here; specialist allowance is −2 %/yr vs funding-smart.

REGIMES = [
    ("vol_2021_ramp",  "2021-02-18", "2021-03-21",
     "Lead-in to the April-2021 alt-season blow-out (textbook accelerating funding). "
     "Substitute for the spec's named '2021-04-01 → 2021-05-15' window, which sits "
     "inside a 59-day data gap."),
    ("vol_luna_2022",  "2022-05-14", "2022-06-14",
     "LUNA cascade tail + immediate post-cascade unwind. Tests the negative-funding "
     "halt and slope-saturation guard. Partial overlap with the spec's named window."),
    ("vol_etf_2024",   "2024-02-03", "2024-03-05",
     "ETF-launch tail (Jan-Mar 2024 institutional flow). Modern Deribit fee tiers; "
     "closest available block to real-money conditions. Partial overlap with the spec's "
     "named '2024-01-08 → 2024-03-15' window."),
    ("steady_2023",    "2023-05-09", "2023-06-09",
     "Cleanest available 30-day steady-funding stretch (std_rate ≈ 8e-6). Bot expected "
     "to bleed; specialist allowance is −2 %/yr vs funding-smart."),
]
# Walk-forward split: 70% in-sample (= sweep windows already cover specific
# events) / 30% out-of-sample (held-out tail of full series).
WALKFWD_FULL_START = "2019-05-30"
WALKFWD_FULL_END   = "2026-05-22"
WALKFWD_SPLIT_FRAC = 0.70

# ── parameter sweep — 3 × 3 × 3 = 27 configs per regime ──────────────────────
# Steven's Q5 decisions: slope_threshold and size_increment_step are the headline
# knobs. slope_lookback_hours is the brief Q2 — expected sweet spot at 24.
# trade_cost_bps is *stressed*, not tuned — held at 6 bps headline through the
# sweep and varied {3, 6, 12} on the winning config only.

SLOPE_THRESHOLD_SWEEP = [1e-7, 2e-7, 4e-7]
SIZE_STEP_SWEEP       = [0.05, 0.10, 0.20]
LOOKBACK_SWEEP        = [16, 24, 32]
FEE_STRESS            = [3.0, 6.0, 12.0]   # bps; 6 is headline

QUICK_SLOPE_THRESHOLD = [2e-7]
QUICK_SIZE_STEP       = [0.10]
QUICK_LOOKBACK        = [24]


# ── data ──────────────────────────────────────────────────────────────────────

def load_funding() -> pd.DataFrame:
    """Load the cached Deribit funding history. interest_1h is the signed
    hourly funding rate (a positive value means shorts receive that rate that
    hour). 21,574 hourly records, 2019-05-30 → 2026-05-22."""
    d = json.load(open(FUNDING_JSON))
    rows = sorted(d["data"], key=lambda r: r["timestamp"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df[["ts", "interest_1h", "index_price"]].astype(
        {"interest_1h": float, "index_price": float})
    return df.reset_index(drop=True)


def window_slice(df: pd.DataFrame, start: str, end: str,
                 warmup_hrs: int = WARMUP_BUFFER_HRS) -> tuple[pd.DataFrame, int]:
    """Return (slice, warmup_bars) — the slice covers `warmup_hrs` pre-window
    rates + the measurement window. Caller seeds the slope buffer with the
    first `warmup_bars` rates so slope is computable from step 1 of the window."""
    warm_start = pd.Timestamp(start) - pd.Timedelta(hours=warmup_hrs)
    sub = df[(df["ts"] >= warm_start) & (df["ts"] < pd.Timestamp(end))].reset_index(drop=True)
    warmup_bars = int((sub["ts"] < pd.Timestamp(start)).sum())
    return sub, warmup_bars


# ── metrics ───────────────────────────────────────────────────────────────────

def max_drawdown(eq: np.ndarray) -> float:
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.max((peak - eq) / np.where(peak > 0, peak, 1.0)))


def annualised_return(eq: np.ndarray, n_bars: int, capital: float) -> float:
    if n_bars <= 0 or capital <= 0:
        return 0.0
    years = n_bars / HOURS_PER_YEAR
    if years <= 0:
        return 0.0
    ratio = eq[-1] / capital
    if ratio <= 0:
        return -1.0
    return ratio ** (1.0 / years) - 1.0


def sharpe(eq: np.ndarray) -> float:
    if len(eq) < 2:
        return 0.0
    r = np.diff(eq) / np.where(eq[:-1] != 0, eq[:-1], 1.0)
    sd = r.std()
    if sd == 0:
        return 0.0
    return float(r.mean() / sd * math.sqrt(HOURS_PER_YEAR))


@dataclass
class RunResult:
    final_equity: float
    return_pct: float
    apr_pct: float
    max_dd_pct: float
    sharpe: float
    rebalances: int
    trade_cost_paid: float
    avg_abs_position: float
    halt_fired: bool
    n_bars: int

    @classmethod
    def from_eq(cls, eq, capital, *, rebalances, cost, avg_pos, halt, n_bars):
        return cls(
            final_equity=float(eq[-1]),
            return_pct=float(eq[-1] / capital - 1.0) * 100.0,
            apr_pct=annualised_return(eq, n_bars, capital) * 100.0,
            max_dd_pct=max_drawdown(eq) * 100.0,
            sharpe=sharpe(eq),
            rebalances=int(rebalances),
            trade_cost_paid=float(cost),
            avg_abs_position=float(avg_pos),
            halt_fired=bool(halt),
            n_bars=int(n_bars),
        )


# ── single backtest runs ─────────────────────────────────────────────────────

def _run_dynamic(rates: np.ndarray, warmup_bars: int, *,
                 slope_threshold: float, size_increment_step: float,
                 slope_lookback_hours: int, trade_cost_bps: float,
                 capital: float = DEFAULT_CAPITAL,
                 record_equity: bool = False) -> tuple[RunResult, np.ndarray | None]:
    bot = FundingDynamicBot(
        capital=capital,
        slope_lookback_hours=slope_lookback_hours,
        slope_threshold=slope_threshold,
        size_increment_step=size_increment_step,
        trade_cost_bps=trade_cost_bps,
        # v1 defaults from spec §3 — disabled long leg, no positive-only skip,
        # leverage=1 (real-money path).
        allow_long_perp=False,
        positive_only=False,
        leverage=1.0,
    )
    if warmup_bars > 0:
        bot.warmup(rates[:warmup_bars].tolist())
        measured = rates[warmup_bars:]
    else:
        measured = rates
    n = len(measured)
    eq = np.empty(n)
    pos_sum = 0.0
    halt_fired = False
    for i, r in enumerate(measured):
        bot.step(float(r))
        eq[i] = bot.equity
        pos_sum += abs(bot.current_position)
        if bot.in_negative_halt:
            halt_fired = True
    avg_pos = pos_sum / n if n > 0 else 0.0
    res = RunResult.from_eq(eq, capital,
                            rebalances=bot.rebalances,
                            cost=bot.total_trade_cost_paid,
                            avg_pos=avg_pos, halt=halt_fired, n_bars=n)
    return res, (eq if record_equity else None)


def _run_funding(rates: np.ndarray, warmup_bars: int, *,
                 positive_only: bool, capital: float = DEFAULT_CAPITAL,
                 record_equity: bool = False) -> tuple[RunResult, np.ndarray | None]:
    """Plain FundingBot (cousin baseline). `funding` = positive_only=False,
    `funding-smart` = positive_only=True. Leverage forced to 1× for the
    real-money-path comparison the spec §8.1 mandates."""
    bot = FundingBot(capital=capital, positive_only=positive_only, leverage=1.0)
    measured = rates[warmup_bars:] if warmup_bars > 0 else rates
    n = len(measured)
    eq = np.empty(n)
    for i, r in enumerate(measured):
        bot.step(float(r))
        eq[i] = bot.equity
    res = RunResult.from_eq(eq, capital,
                            rebalances=0, cost=0.0,
                            avg_pos=1.0,   # plain funding is always full-size short
                            halt=False, n_bars=n)
    return res, (eq if record_equity else None)


# ── parameter sweep ──────────────────────────────────────────────────────────

def run_sweep(df: pd.DataFrame, *,
              slope_thresh_list, size_step_list, lookback_list) -> pd.DataFrame:
    rows = []
    for regime_name, start, end, _why in REGIMES:
        # Use the largest lookback as warmup so all configs are comparable.
        sub, warm_bars = window_slice(df, start, end, warmup_hrs=max(lookback_list))
        rates = sub["interest_1h"].values
        # Cousin baselines for this window — same warmup discard so the bars
        # measured are identical.
        funding_r, _ = _run_funding(rates, warm_bars, positive_only=False)
        smart_r, _   = _run_funding(rates, warm_bars, positive_only=True)
        best_cousin_apr = max(funding_r.apr_pct, smart_r.apr_pct)
        for st in slope_thresh_list:
            for ss in size_step_list:
                for lb in lookback_list:
                    # Use only `lb` warmup bars for this config (we have extra,
                    # but the bot will only consume up to its lookback).
                    config_warm = min(warm_bars, lb)
                    dyn, _ = _run_dynamic(rates, config_warm,
                                          slope_threshold=st,
                                          size_increment_step=ss,
                                          slope_lookback_hours=lb,
                                          trade_cost_bps=6.0)
                    rows.append({
                        "regime": regime_name,
                        "slope_threshold": st,
                        "size_increment_step": ss,
                        "slope_lookback_hours": lb,
                        "trade_cost_bps": 6.0,
                        "dyn_apr": dyn.apr_pct,
                        "dyn_return": dyn.return_pct,
                        "dyn_dd": dyn.max_dd_pct,
                        "dyn_sharpe": dyn.sharpe,
                        "dyn_rebalances": dyn.rebalances,
                        "dyn_trade_cost": dyn.trade_cost_paid,
                        "dyn_avg_pos": dyn.avg_abs_position,
                        "dyn_halt": dyn.halt_fired,
                        "funding_apr": funding_r.apr_pct,
                        "smart_apr": smart_r.apr_pct,
                        "best_cousin_apr": best_cousin_apr,
                        "dyn_minus_best_apr": dyn.apr_pct - best_cousin_apr,
                        "dyn_minus_smart_apr": dyn.apr_pct - smart_r.apr_pct,
                    })
        print(f"  swept regime={regime_name:14s} ({len(sub)} rows, {len(slope_thresh_list)*len(size_step_list)*len(lookback_list)} configs)")
    return pd.DataFrame(rows)


def pick_winner(sweep_df: pd.DataFrame) -> dict:
    """Pick the config that comes closest to passing the strict §8.1 bar:
       1. dyn − best_cousin ≥ +2 %/yr in ≥ 2 of 3 volatile windows
       2. dyn − smart ≥ −2 %/yr in the steady baseline (specialist allowance)

    Scoring: count of volatile windows where the +2pp bar is met, with a
    tie-breaker that penalises steady-window underperformance below −2pp.
    Within ties, prefer the config with the *highest sum of volatile-window
    APR uplift* (= raw alpha magnitude), then prefer the smaller
    size_increment_step (more responsive). The winner-pick step does NOT
    decide the strict-bar verdict — that's done later on the chosen config,
    against the cousin baselines from this same sweep."""
    volatile_names = ["vol_2021_ramp", "vol_luna_2022", "vol_etf_2024"]
    steady_name    = "steady_2023"

    keys = ["slope_threshold", "size_increment_step", "slope_lookback_hours"]
    pivot = sweep_df.pivot_table(index=keys, columns="regime",
                                 values="dyn_minus_best_apr").reset_index()
    pivot_steady_vs_smart = sweep_df[sweep_df["regime"] == steady_name].set_index(keys)["dyn_minus_smart_apr"]

    rows = []
    for _, row in pivot.iterrows():
        cfg = tuple(row[k] for k in keys)
        beats = sum(1 for w in volatile_names if row.get(w, -math.inf) >= 2.0)
        sum_uplift = sum(row.get(w, 0.0) for w in volatile_names)
        steady_vs_smart = float(pivot_steady_vs_smart.loc[cfg])
        steady_pass = steady_vs_smart >= -2.0
        rows.append({
            **{k: float(v) for k, v in zip(keys, cfg)},
            "n_windows_beat_2pp": beats,
            "sum_uplift_volatile": sum_uplift,
            "steady_vs_smart_pp": steady_vs_smart,
            "steady_pass": steady_pass,
        })
    rank_df = pd.DataFrame(rows)

    # Sort: most volatile windows beating +2pp, then steady pass, then total
    # uplift in volatile windows, then prefer smaller size_increment_step
    # (more responsive, less hysteretic).
    rank_df = rank_df.sort_values(
        ["n_windows_beat_2pp", "steady_pass", "sum_uplift_volatile",
         "size_increment_step"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)

    top = rank_df.iloc[0]
    rationale = (
        f"Winner picked from {len(rank_df)} configs. "
        f"Beats best-cousin by ≥+2%/yr in {int(top['n_windows_beat_2pp'])} of 3 "
        f"volatile windows; steady vs funding-smart: {top['steady_vs_smart_pp']:+.2f}pp "
        f"({'PASS' if top['steady_pass'] else 'FAIL'} on −2pp specialist allowance). "
        f"Sum of volatile uplift: {top['sum_uplift_volatile']:+.2f}pp."
    )
    return {
        "slope_threshold": float(top["slope_threshold"]),
        "size_increment_step": float(top["size_increment_step"]),
        "slope_lookback_hours": int(top["slope_lookback_hours"]),
        "n_volatile_beats": int(top["n_windows_beat_2pp"]),
        "steady_vs_smart_pp": float(top["steady_vs_smart_pp"]),
        "sum_uplift_volatile": float(top["sum_uplift_volatile"]),
        "rationale": rationale,
        "top10": rank_df.head(10).to_dict(orient="records"),
    }


# ── fee stress at winning config ─────────────────────────────────────────────

def run_fee_stress(df: pd.DataFrame, *, slope_threshold, size_increment_step,
                   slope_lookback_hours) -> pd.DataFrame:
    rows = []
    for regime_name, start, end, _why in REGIMES:
        sub, warm = window_slice(df, start, end, warmup_hrs=slope_lookback_hours)
        rates = sub["interest_1h"].values
        funding_r, _ = _run_funding(rates, warm, positive_only=False)
        smart_r, _   = _run_funding(rates, warm, positive_only=True)
        for bps in FEE_STRESS:
            dyn, _ = _run_dynamic(rates, warm,
                                  slope_threshold=slope_threshold,
                                  size_increment_step=size_increment_step,
                                  slope_lookback_hours=slope_lookback_hours,
                                  trade_cost_bps=bps)
            rows.append({
                "regime": regime_name,
                "trade_cost_bps": bps,
                "dyn_apr": dyn.apr_pct,
                "dyn_return": dyn.return_pct,
                "dyn_dd": dyn.max_dd_pct,
                "dyn_rebalances": dyn.rebalances,
                "dyn_trade_cost": dyn.trade_cost_paid,
                "funding_apr": funding_r.apr_pct,
                "smart_apr": smart_r.apr_pct,
                "best_cousin_apr": max(funding_r.apr_pct, smart_r.apr_pct),
                "dyn_minus_best_apr": dyn.apr_pct - max(funding_r.apr_pct, smart_r.apr_pct),
            })
    return pd.DataFrame(rows)


# ── walk-forward 70/30 + holdout ─────────────────────────────────────────────

def run_walkforward(df: pd.DataFrame, *, slope_threshold, size_increment_step,
                    slope_lookback_hours) -> pd.DataFrame:
    """Walk-forward 70/30 of the full 2019-05 → 2026-05 series. The 70%
    in-sample is reported as one fold; the 30% out-of-sample is the held-out
    validation fold. Within each, we also report quarterly sub-folds so a
    fragility pattern (alpha concentrated in one quarter) shows up."""
    full = df[(df["ts"] >= pd.Timestamp(WALKFWD_FULL_START)) &
              (df["ts"] < pd.Timestamp(WALKFWD_FULL_END))].reset_index(drop=True)
    n = len(full)
    split_idx = int(n * WALKFWD_SPLIT_FRAC)
    in_sample  = full.iloc[:split_idx].reset_index(drop=True)
    out_sample = full.iloc[split_idx:].reset_index(drop=True)

    def _seg(seg_df, label):
        rates = seg_df["interest_1h"].values
        warm = min(slope_lookback_hours, len(rates))
        # For walk-forward we don't have *pre-window* warmup — we just let the
        # bot warm on the first `lookback` bars of the segment itself. This
        # matches how the spec's `load_variant()` warmup will behave in live.
        dyn, _ = _run_dynamic(rates, warm,
                              slope_threshold=slope_threshold,
                              size_increment_step=size_increment_step,
                              slope_lookback_hours=slope_lookback_hours,
                              trade_cost_bps=6.0)
        funding_r, _ = _run_funding(rates, warm, positive_only=False)
        smart_r, _   = _run_funding(rates, warm, positive_only=True)
        return {
            "fold": label,
            "start": str(seg_df["ts"].iloc[0])[:10],
            "end": str(seg_df["ts"].iloc[-1])[:10],
            "n_hours": len(rates) - warm,
            "dyn_apr": dyn.apr_pct,
            "dyn_return": dyn.return_pct,
            "dyn_dd": dyn.max_dd_pct,
            "dyn_sharpe": dyn.sharpe,
            "dyn_rebalances": dyn.rebalances,
            "dyn_trade_cost": dyn.trade_cost_paid,
            "dyn_avg_pos": dyn.avg_abs_position,
            "dyn_halt": dyn.halt_fired,
            "funding_apr": funding_r.apr_pct,
            "smart_apr": smart_r.apr_pct,
            "best_cousin_apr": max(funding_r.apr_pct, smart_r.apr_pct),
            "dyn_minus_best_apr": dyn.apr_pct - max(funding_r.apr_pct, smart_r.apr_pct),
        }

    rows = [_seg(in_sample, f"in_sample_70pct"),
            _seg(out_sample, f"out_sample_30pct")]
    # Quarterly sub-folds across the full series — every 91 days, fragility check.
    cursor = full["ts"].iloc[0]
    end_ts = full["ts"].iloc[-1]
    while cursor + pd.Timedelta(days=91) <= end_ts:
        nxt = cursor + pd.Timedelta(days=91)
        seg = full[(full["ts"] >= cursor) & (full["ts"] < nxt)].reset_index(drop=True)
        if len(seg) > slope_lookback_hours * 2:
            rows.append(_seg(seg, f"q_{str(cursor)[:10]}"))
        cursor = nxt
    return pd.DataFrame(rows)


# ── head-to-head per regime ──────────────────────────────────────────────────

def run_comparison(df: pd.DataFrame, *, slope_threshold, size_increment_step,
                   slope_lookback_hours) -> pd.DataFrame:
    rows = []
    for regime_name, start, end, _why in REGIMES:
        sub, warm = window_slice(df, start, end, warmup_hrs=slope_lookback_hours)
        rates = sub["interest_1h"].values
        dyn, _ = _run_dynamic(rates, warm,
                              slope_threshold=slope_threshold,
                              size_increment_step=size_increment_step,
                              slope_lookback_hours=slope_lookback_hours,
                              trade_cost_bps=6.0)
        funding_r, _ = _run_funding(rates, warm, positive_only=False)
        smart_r, _   = _run_funding(rates, warm, positive_only=True)
        for name, r in [("funding_dynamic", dyn), ("funding", funding_r), ("funding_smart", smart_r)]:
            rows.append({
                "regime": regime_name, "bot": name,
                "final_eq": r.final_equity,
                "return_pct": r.return_pct,
                "apr_pct": r.apr_pct,
                "max_dd_pct": r.max_dd_pct,
                "sharpe": r.sharpe,
                "rebalances": r.rebalances,
                "trade_cost_paid": r.trade_cost_paid,
                "avg_abs_position": r.avg_abs_position,
                "halt_fired": r.halt_fired,
                "n_bars": r.n_bars,
            })
    return pd.DataFrame(rows)


# ── correlation check vs funding-smart ───────────────────────────────────────

def run_correlation(df: pd.DataFrame, *, slope_threshold, size_increment_step,
                    slope_lookback_hours) -> tuple[pd.DataFrame, dict]:
    """30-day rolling correlation of Dynamic equity returns vs funding-smart
    equity returns, computed over the full series. Strict bar §8.1: median
    rolling correlation < 0.85 ⇒ PASS (the bot is genuinely complementary).
    Median > 0.95 ⇒ park as redundant. In between ⇒ flag."""
    full = df[(df["ts"] >= pd.Timestamp(WALKFWD_FULL_START)) &
              (df["ts"] < pd.Timestamp(WALKFWD_FULL_END))].reset_index(drop=True)
    rates = full["interest_1h"].values
    warm = min(slope_lookback_hours, len(rates))
    _, dyn_eq = _run_dynamic(rates, warm,
                             slope_threshold=slope_threshold,
                             size_increment_step=size_increment_step,
                             slope_lookback_hours=slope_lookback_hours,
                             trade_cost_bps=6.0, record_equity=True)
    _, smart_eq = _run_funding(rates, warm, positive_only=True, record_equity=True)

    # Hourly returns.
    dyn_ret = np.diff(dyn_eq) / np.where(dyn_eq[:-1] != 0, dyn_eq[:-1], 1.0)
    smart_ret = np.diff(smart_eq) / np.where(smart_eq[:-1] != 0, smart_eq[:-1], 1.0)

    win = 24 * 30   # 30-day rolling window
    n_ret = len(dyn_ret)
    ts = full["ts"].iloc[warm + 1:].reset_index(drop=True)
    corr_rows = []
    for i in range(win, n_ret + 1):
        a = dyn_ret[i - win:i]
        b = smart_ret[i - win:i]
        sd_a = a.std(); sd_b = b.std()
        if sd_a == 0 or sd_b == 0:
            c = 0.0
        else:
            c = float(np.corrcoef(a, b)[0, 1])
        corr_rows.append({"ts": str(ts.iloc[i - 1])[:10], "rolling_corr_30d": c})
    corr_df = pd.DataFrame(corr_rows)
    median_corr = float(corr_df["rolling_corr_30d"].median()) if len(corr_df) else float("nan")
    mean_corr = float(corr_df["rolling_corr_30d"].mean()) if len(corr_df) else float("nan")
    p95_corr = float(corr_df["rolling_corr_30d"].quantile(0.95)) if len(corr_df) else float("nan")
    summary = {
        "median_corr": median_corr,
        "mean_corr": mean_corr,
        "p95_corr": p95_corr,
        "pass_below_085": median_corr < 0.85,
        "park_above_095": median_corr > 0.95,
    }
    return corr_df, summary


# ── strict-bar verdict ───────────────────────────────────────────────────────

def strict_bar_verdict(cmp_df: pd.DataFrame, corr_summary: dict) -> dict:
    """Apply the locked §8.1 bar to the comparison numbers."""
    volatile = ["vol_2021_ramp", "vol_luna_2022", "vol_etf_2024"]
    steady   = "steady_2023"

    def get_apr(regime, bot):
        sel = cmp_df[(cmp_df["regime"] == regime) & (cmp_df["bot"] == bot)]
        if len(sel) == 0:
            return float("nan")
        return float(sel["apr_pct"].iloc[0])

    rows = []
    for w in volatile:
        dyn = get_apr(w, "funding_dynamic")
        funding = get_apr(w, "funding")
        smart = get_apr(w, "funding_smart")
        best = max(funding, smart)
        rows.append({
            "window": w, "dyn_apr": dyn, "funding_apr": funding,
            "smart_apr": smart, "best_cousin_apr": best,
            "dyn_minus_best": dyn - best,
            "pass_2pp": dyn - best >= 2.0,
        })
    vol_df = pd.DataFrame(rows)
    n_volatile_pass = int(vol_df["pass_2pp"].sum())
    volatile_bar = n_volatile_pass >= 2

    steady_dyn = get_apr(steady, "funding_dynamic")
    steady_smart = get_apr(steady, "funding_smart")
    steady_delta = steady_dyn - steady_smart
    steady_bar = steady_delta >= -2.0

    corr_bar = corr_summary["pass_below_085"]
    park_redundant = corr_summary["park_above_095"]

    all_pass = volatile_bar and steady_bar and corr_bar
    if all_pass:
        verdict = "PASS-AS-SPECIALIST"
    elif park_redundant:
        verdict = "PARK-REDUNDANT"
    elif not volatile_bar and not corr_bar:
        verdict = "FAIL-PARK"
    elif not volatile_bar:
        verdict = "FAIL-PARK (volatile-window edge insufficient)"
    elif not corr_bar:
        verdict = "MIXED (volatile edge OK, but correlated with funding-smart)"
    elif not steady_bar:
        verdict = "MIXED (volatile edge OK, but steady bleed exceeds −2%/yr)"
    else:
        verdict = "MIXED"

    return {
        "volatile_windows_passing_2pp": n_volatile_pass,
        "volatile_bar_pass": bool(volatile_bar),
        "steady_delta_vs_smart_pp": steady_delta,
        "steady_bar_pass": bool(steady_bar),
        "median_corr_vs_smart": corr_summary["median_corr"],
        "corr_bar_pass": bool(corr_bar),
        "park_redundant": bool(park_redundant),
        "verdict": verdict,
        "volatile_detail": rows,
    }


# ── orchestrator ─────────────────────────────────────────────────────────────

def main(quick=False):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    mode = "QUICK" if quick else "FULL"
    print(f"\n=== FUNDING-DYNAMIC — GATE 3 BACKTEST ({mode}) ===\n")
    t0 = time.time()

    df = load_funding()
    print(f"Loaded {len(df)} hourly funding records "
          f"{str(df['ts'].iloc[0])[:10]} → {str(df['ts'].iloc[-1])[:10]}\n")

    st_list = QUICK_SLOPE_THRESHOLD if quick else SLOPE_THRESHOLD_SWEEP
    ss_list = QUICK_SIZE_STEP if quick else SIZE_STEP_SWEEP
    lb_list = QUICK_LOOKBACK if quick else LOOKBACK_SWEEP

    n_cfg = len(st_list) * len(ss_list) * len(lb_list)
    n_total = n_cfg * len(REGIMES)
    print(f"[1/5] Parameter sweep — {n_cfg} configs × {len(REGIMES)} regimes = {n_total} runs")
    sweep_df = run_sweep(df, slope_thresh_list=st_list,
                         size_step_list=ss_list, lookback_list=lb_list)
    sweep_df.to_csv(OUTDIR / "sweep_results.csv", index=False)

    print("\n[2/5] Winning-config pick under strict §8.1 bar...")
    winner = pick_winner(sweep_df)
    print(f"     {winner['rationale']}")
    print(f"     slope_threshold={winner['slope_threshold']:.0e}/h  "
          f"size_increment_step={winner['size_increment_step']:.2f}  "
          f"slope_lookback_hours={winner['slope_lookback_hours']}")
    with open(OUTDIR / "winner.json", "w") as f:
        json.dump({k: v for k, v in winner.items() if k != "top10"}, f, indent=2, default=float)

    print("\n[3/5] Trade-cost stress at winner...")
    fee_df = run_fee_stress(df,
                            slope_threshold=winner["slope_threshold"],
                            size_increment_step=winner["size_increment_step"],
                            slope_lookback_hours=winner["slope_lookback_hours"])
    fee_df.to_csv(OUTDIR / "fee_stress.csv", index=False)
    pivot_fee = fee_df.pivot(index="regime", columns="trade_cost_bps", values="dyn_apr")
    print(pivot_fee.reindex([r[0] for r in REGIMES])
          .to_string(float_format=lambda x: f"{x:+7.2f}"))

    print("\n[4/5] Walk-forward 70/30 + quarterly sub-folds at winner...")
    wf_df = run_walkforward(df,
                            slope_threshold=winner["slope_threshold"],
                            size_increment_step=winner["size_increment_step"],
                            slope_lookback_hours=winner["slope_lookback_hours"])
    wf_df.to_csv(OUTDIR / "walkforward_results.csv", index=False)
    head_rows = wf_df[wf_df["fold"].str.startswith(("in_sample", "out_sample"))]
    for _, row in head_rows.iterrows():
        print(f"     {row['fold']:20s} {row['start']} → {row['end']}  "
              f"dyn={row['dyn_apr']:+6.2f}%/yr  funding={row['funding_apr']:+6.2f}%/yr  "
              f"smart={row['smart_apr']:+6.2f}%/yr  vs-best={row['dyn_minus_best_apr']:+5.2f}pp  "
              f"rebals={int(row['dyn_rebalances'])}")

    print("\n[5/5] Head-to-head comparison + correlation check...")
    cmp_df = run_comparison(df,
                            slope_threshold=winner["slope_threshold"],
                            size_increment_step=winner["size_increment_step"],
                            slope_lookback_hours=winner["slope_lookback_hours"])
    cmp_df.to_csv(OUTDIR / "comparison_results.csv", index=False)
    pivot_apr = cmp_df.pivot(index="regime", columns="bot", values="apr_pct")
    print("\n     APR % by regime (at winner config, 6 bps cost):")
    print(pivot_apr.reindex([r[0] for r in REGIMES])
          .to_string(float_format=lambda x: f"{x:+7.2f}"))

    corr_df, corr_summary = run_correlation(df,
                                            slope_threshold=winner["slope_threshold"],
                                            size_increment_step=winner["size_increment_step"],
                                            slope_lookback_hours=winner["slope_lookback_hours"])
    corr_df.to_csv(OUTDIR / "correlation_rolling.csv", index=False)
    print(f"\n     30d-rolling corr vs funding-smart  median={corr_summary['median_corr']:+.3f}  "
          f"mean={corr_summary['mean_corr']:+.3f}  p95={corr_summary['p95_corr']:+.3f}  "
          f"→ {'PASS (<0.85)' if corr_summary['pass_below_085'] else 'FAIL (≥0.85)'}"
          f"{'  ⚠ PARK (>0.95)' if corr_summary['park_above_095'] else ''}")

    # Strict-bar verdict.
    verdict = strict_bar_verdict(cmp_df, corr_summary)
    with open(OUTDIR / "verdict.json", "w") as f:
        json.dump(verdict, f, indent=2, default=float)
    print(f"\n=== STRICT-BAR VERDICT: {verdict['verdict']} ===")
    print(f"     volatile windows ≥+2pp: {verdict['volatile_windows_passing_2pp']}/3  "
          f"({'PASS' if verdict['volatile_bar_pass'] else 'FAIL'})")
    print(f"     steady vs funding-smart: {verdict['steady_delta_vs_smart_pp']:+.2f}pp  "
          f"({'PASS' if verdict['steady_bar_pass'] else 'FAIL'} on −2pp allowance)")
    print(f"     median rolling corr:     {verdict['median_corr_vs_smart']:+.3f}  "
          f"({'PASS' if verdict['corr_bar_pass'] else 'FAIL'} on <0.85)")

    print(f"\nDONE in {time.time() - t0:.1f}s. Artifacts under {OUTDIR}")
    print("Next: write docs/gate3-reports/04-funding-dynamic.md from these CSVs.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="smaller sweep for iteration")
    args = parser.parse_args()
    main(quick=args.quick)
