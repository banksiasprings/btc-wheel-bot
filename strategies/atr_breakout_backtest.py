"""
atr_breakout_backtest.py — Gate 3 backtest harness for the ATR Breakout bot
(spec 06).

NOT a live deployment. Evaluated against Steven's Scorecard B
(portfolio-specialist — bull-leg specialist). This file runs:

  1. Per-regime parameter sweep — 54 configs × 4 regimes = 216 runs:
       entry_lookback     ∈ {10, 20, 40}
       atr_period         ∈ {10, 14, 21}
       atr_K              ∈ {1.5, 2.0, 3.0}
       position_size_pct  ∈ {0.50, 1.00}
  2. Per-regime winners (Δ APR vs *best* of TrendBot-fast / TrendBot-slow /
     Donchian-20-10).
  3. K-sweep override check: only displace the a-priori 20/14/3.0/100% pick
     if another config leads by ≥+5pp annualised *and* matches that lead on
     the 2024-09→2026-05 holdout (per spec §12 Open Q #3 + Steven's default).
  4. Walk-forward at the headline config + holdout fold (K4 gate, K2 gate).
  5. Capital-level sensitivity at $10k / $100k / $1M with scaled slippage
     (10 / 10 / 25 bps RT).
  6. Slippage stress at 0 / 5 / 10 / 20 bps RT (0 labelled FANTASY,
     reference-only for Boring Edge reconciliation).
  7. Three-baseline triangulation per regime + holdout: TrendBot-fast,
     TrendBot-slow, Donchian-20-10, BuyHold.
  8. K3 correlation matrix on the holdout: 30-day rolling hourly equity
     returns vs TrendBot-fast / TrendBot-slow / Donchian-20-10 / BuyHold;
     median aggregated. K3 fires if BOTH (TrendBot-best ≥ 0.85) AND
     (Donchian ≥ 0.85).
  9. Catastrophic-resistance check (K5) — scans every run's equity series
     for NaN / negative-cash / liquidation flags.

Outputs (all under docs/gate3-reports/06-atr-breakout-data/):
  - sweep_results.csv            — (regime, config) → metrics row
  - regime_winners.csv           — per-regime top configs
  - winner.json                  — chosen variant + reasoning
  - walkforward_results.csv      — fold-by-fold at winning config + holdout
  - comparison_results.csv       — ATR vs three baselines per regime + holdout
  - capital_sensitivity.csv      — winning config at $10k / $100k / $1M
  - slippage_stress.csv          — winning config at 0 / 5 / 10 / 20 bps RT
  - sweep_holdout.csv            — every sweep config on the holdout slice
  - correlation_k3.csv           — rolling-30d corr vs the three cousins + BH
  - catastrophic_check.csv       — K5 per-run pass/fail audit

The narrative pass/fail Gate 3 report is at
~/Documents/bsf-research-briefs/results/06-atr-breakout-gate3-results.md
(per the task spec — outside the repo, in the research briefs).

Run:
    python3.11 atr_breakout_backtest.py           # full Gate 3 run
    python3.11 atr_breakout_backtest.py --quick   # smaller sweep
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from more_bots import ATRBreakoutBot, DonchianBot, TrendBot, BuyHoldBot

ROOT = Path(__file__).resolve().parent.parent
HOURLY = ROOT / "data" / "raw" / "spot" / "btc_1h.csv"
OUTDIR = ROOT / "docs" / "gate3-reports" / "06-atr-breakout-data"
FEE = 0.0006
HOURS_PER_YEAR = 24 * 365
DEFAULT_CAPITAL = 10_000.0
# Need max(N, p) + 1 daily closes before the first valid signal. Worst case:
# N=40, p=21 → 41 days. Use 60-day warmup with a comfortable safety margin —
# matches the Donchian harness exactly for direct comparability.
WARMUP_DAYS = 60
# Live cousins: TrendBot variants from grid_farm.py and the deployed Donchian
# variant (donchian-20-10).
TREND_FAST_MA_HOURS = 168    # 7-day MA — "trend-fast"
TREND_SLOW_MA_HOURS = 1200   # 50-day MA — "trend-slow"
DONCHIAN_N = 20
DONCHIAN_M = 10

# ── regime windows (identical to Donchian §7.2 — direct comparability) ────────
REGIMES = [
    ("bull",  "2020-10-01", "2021-04-15",
     "BTC ~$10k → ~$63k — canonical persistent bull leg. Specialist regime."),
    ("bear",  "2021-11-10", "2022-11-22",
     "Cycle top $69k → FTX low ~$16k. Long-only ATR sits flat most of window."),
    ("crab",  "2022-12-01", "2023-10-16",
     "Post-FTX range, ~$17k → ~$28k. Chop / whipsaw stress test."),
    ("crash", "2020-03-01", "2020-04-15",
     "Covid −50% in 2 days then v-bottom. K5 / black-swan check."),
]
HOLDOUT_START = "2024-09-01"
HOLDOUT_END   = "2026-05-22"

# ── parameter sweep — 3 × 3 × 3 × 2 = 54 configs per regime ───────────────────
ENTRY_LB_SWEEP = [10, 20, 40]
ATR_PERIOD_SWEEP = [10, 14, 21]
ATR_K_SWEEP = [1.5, 2.0, 3.0]
POSITION_PCT_SWEEP = [0.50, 1.00]

QUICK_ENTRY = [20, 40]
QUICK_PERIOD = [14]
QUICK_K = [1.5, 3.0]
QUICK_SIZE = [1.00]

# A-priori headline pick (spec §3 + Steven's defaults on Open Q #3)
APRIORI_N = 20
APRIORI_P = 14
APRIORI_K = 3.0
APRIORI_SIZE = 1.00

# K-sweep override threshold (Steven's default on Open Q #3): only displace
# the a-priori 20/14/3.0/100% pick if another config leads by ≥+5pp annualised
# *and* matches that lead on the holdout.
OVERRIDE_LEAD_PP = 5.0

# Slippage stress rows (bps round-trip).
SLIPPAGE_STRESS_BPS_RT = [0.0, 5.0, 10.0, 20.0]
HEADLINE_SLIPPAGE_BPS_RT = 10.0
CAPITAL_LEVELS = [
    (10_000.0,    HEADLINE_SLIPPAGE_BPS_RT),
    (100_000.0,   HEADLINE_SLIPPAGE_BPS_RT),
    (1_000_000.0, 25.0),
]


# ── data ──────────────────────────────────────────────────────────────────────

def load_hourly(start=None, end=None) -> pd.DataFrame:
    df = pd.read_csv(HOURLY)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df[["ts", "high", "low", "close"]].astype(
        {"high": float, "low": float, "close": float})
    if start is not None:
        df = df[df["ts"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["ts"] < pd.Timestamp(end)]
    return df.reset_index(drop=True)


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
    r = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1.0)
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
    trades: int
    btc_held: float
    cash_remaining: float
    days: float
    halt_active: bool
    nan_seen: bool
    negative_eq_seen: bool
    equity_series: np.ndarray

    @classmethod
    def from_eq(cls, eq, capital, days, *, trades, btc, cash, halt=False):
        nan_seen = bool(np.isnan(eq).any())
        neg_seen = bool((eq <= 0).any())
        return cls(
            final_equity=float(eq[-1]) if len(eq) else 0.0,
            return_pct=float(eq[-1] / capital - 1.0) * 100.0 if len(eq) else 0.0,
            apr_pct=annualised_return(eq, len(eq), capital) * 100.0,
            max_dd_pct=max_drawdown(eq) * 100.0,
            sharpe=sharpe(eq),
            trades=int(trades),
            btc_held=float(btc),
            cash_remaining=float(cash),
            days=float(days),
            halt_active=bool(halt),
            nan_seen=nan_seen,
            negative_eq_seen=neg_seen,
            equity_series=eq,
        )


# ── single backtest runs ─────────────────────────────────────────────────────

def _effective_fee(slippage_bps_rt: float) -> float:
    """Bot.fee is per side. slippage_bps_rt is round-trip extra cost; halve
    and add to each side."""
    return FEE + (slippage_bps_rt / 10000.0) / 2.0


def _run_atr(df: pd.DataFrame, *,
             entry_lookback: int, atr_period: int, atr_K: float,
             position_size_pct: float,
             capital: float = DEFAULT_CAPITAL,
             slippage_bps_rt: float = HEADLINE_SLIPPAGE_BPS_RT,
             max_dd_halt: float = 0.35,
             warmup_bars: int = WARMUP_DAYS * 24) -> RunResult:
    """One ATR Breakout backtest. Same fill model as the live paper bots:
    market-close fill, intrinsic FEE=0.0006 + slippage applied per side."""
    closes = df["close"].values
    bot = ATRBreakoutBot(
        capital=capital,
        entry_lookback_days=entry_lookback,
        atr_period_days=atr_period,
        atr_multiplier_K=atr_K,
        position_size_pct=position_size_pct,
        long_only=True,
        max_drawdown_halt_pct=max_dd_halt,
        fee=_effective_fee(slippage_bps_rt),
    )
    if warmup_bars > 0:
        bot.warmup(closes[:warmup_bars].tolist())
        closes = closes[warmup_bars:]
    eq = np.empty(len(closes))
    for i, c in enumerate(closes):
        bot.step(float(c))
        eq[i] = bot.equity(c)
    days = len(closes) / 24.0
    return RunResult.from_eq(eq, capital, days,
                             trades=bot.trades, btc=bot.btc, cash=bot.cash,
                             halt=bot.halt_active)


def _run_donchian(df: pd.DataFrame, *, entry_lookback: int, exit_lookback: int,
                  capital: float = DEFAULT_CAPITAL,
                  warmup_bars: int = WARMUP_DAYS * 24) -> RunResult:
    """Cousin baseline. Uses the same FEE the live donchian-20-10 paper bot
    uses (no slippage stress) — apples-to-apples Δ-vs-Donchian."""
    closes = df["close"].values
    bot = DonchianBot(capital=capital, entry_lookback_days=entry_lookback,
                      exit_lookback_days=exit_lookback,
                      position_size_pct=1.0, long_only=True, fee=FEE)
    if warmup_bars > 0:
        bot.warmup(closes[:warmup_bars].tolist())
        closes = closes[warmup_bars:]
    eq = np.empty(len(closes))
    for i, c in enumerate(closes):
        bot.step(float(c))
        eq[i] = bot.equity(c)
    days = len(closes) / 24.0
    return RunResult.from_eq(eq, capital, days,
                             trades=bot.trades, btc=bot.btc, cash=bot.cash)


def _run_trend(df: pd.DataFrame, *, ma_hours: int,
               capital: float = DEFAULT_CAPITAL,
               warmup_bars: int = WARMUP_DAYS * 24) -> RunResult:
    closes = df["close"].values
    bot = TrendBot(capital=capital, ma_hours=ma_hours, fee=FEE)
    if warmup_bars > 0:
        bot.warmup(closes[:warmup_bars].tolist())
        closes = closes[warmup_bars:]
    eq = np.empty(len(closes))
    for i, c in enumerate(closes):
        bot.step(float(c))
        eq[i] = bot.equity(c)
    days = len(closes) / 24.0
    return RunResult.from_eq(eq, capital, days,
                             trades=bot.trades, btc=bot.btc, cash=bot.cash)


def _run_buyhold(df: pd.DataFrame,
                 capital: float = DEFAULT_CAPITAL,
                 warmup_bars: int = WARMUP_DAYS * 24) -> RunResult:
    closes = df["close"].values
    if warmup_bars > 0:
        closes = closes[warmup_bars:]
    bot = BuyHoldBot(capital=capital, fee=FEE)
    eq = np.empty(len(closes))
    for i, c in enumerate(closes):
        bot.step(float(c))
        eq[i] = bot.equity(c)
    days = len(closes) / 24.0
    return RunResult.from_eq(eq, capital, days,
                             trades=bot.trades, btc=bot.btc, cash=bot.cash)


def _window_df(start: str, end: str, warmup_days: int = WARMUP_DAYS) -> pd.DataFrame:
    warm_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    return load_hourly(warm_start, end)


# ── sweep ────────────────────────────────────────────────────────────────────

def run_sweep(entry_list, period_list, k_list, size_list) -> pd.DataFrame:
    rows = []
    n_configs = (len(entry_list) * len(period_list)
                 * len(k_list) * len(size_list))
    for regime_name, start, end, _why in REGIMES:
        df = _window_df(start, end)
        warm_bars = int((df["ts"] < pd.Timestamp(start)).sum())
        bh = _run_buyhold(df, warmup_bars=warm_bars)
        trend_fast = _run_trend(df, ma_hours=TREND_FAST_MA_HOURS, warmup_bars=warm_bars)
        trend_slow = _run_trend(df, ma_hours=TREND_SLOW_MA_HOURS, warmup_bars=warm_bars)
        don = _run_donchian(df, entry_lookback=DONCHIAN_N,
                            exit_lookback=DONCHIAN_M, warmup_bars=warm_bars)
        # Best of the cousin row — what K1 fires on.
        cousin_aprs = [trend_fast.apr_pct, trend_slow.apr_pct, don.apr_pct]
        best_cousin_apr = max(cousin_aprs)
        for n in entry_list:
            for p in period_list:
                for k in k_list:
                    for size in size_list:
                        t0 = time.time()
                        atr = _run_atr(df, entry_lookback=n, atr_period=p,
                                       atr_K=k, position_size_pct=size,
                                       warmup_bars=warm_bars)
                        rows.append({
                            "regime": regime_name,
                            "entry_lookback": n, "atr_period": p,
                            "atr_K": k, "position_size_pct": size,
                            "atr_final_eq": atr.final_equity,
                            "atr_return_pct": atr.return_pct,
                            "atr_apr_pct": atr.apr_pct,
                            "atr_dd_pct": atr.max_dd_pct,
                            "atr_sharpe": atr.sharpe,
                            "atr_trades": atr.trades,
                            "trend_fast_apr_pct": trend_fast.apr_pct,
                            "trend_slow_apr_pct": trend_slow.apr_pct,
                            "donchian_apr_pct": don.apr_pct,
                            "donchian_dd_pct": don.max_dd_pct,
                            "bh_apr_pct": bh.apr_pct,
                            "best_cousin_apr_pct": best_cousin_apr,
                            "atr_vs_best_cousin_pp": atr.apr_pct - best_cousin_apr,
                            "atr_vs_bh_pp": atr.apr_pct - bh.apr_pct,
                            "runtime_s": round(time.time() - t0, 3),
                        })
        print(f"  swept regime={regime_name:6s} ({len(df)} bars, {n_configs} configs)")
    return pd.DataFrame(rows)


def per_regime_winners(sweep_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for regime_name, _s, _e, _why in REGIMES:
        sub = sweep_df[sweep_df["regime"] == regime_name].copy()
        sub = sub.sort_values("atr_vs_best_cousin_pp", ascending=False)
        top = sub.iloc[0]
        rows.append({
            "regime": regime_name,
            "top_N": int(top["entry_lookback"]),
            "top_p": int(top["atr_period"]),
            "top_K": float(top["atr_K"]),
            "top_size": float(top["position_size_pct"]),
            "atr_apr_pct": float(top["atr_apr_pct"]),
            "atr_dd_pct": float(top["atr_dd_pct"]),
            "best_cousin_apr_pct": float(top["best_cousin_apr_pct"]),
            "atr_vs_best_cousin_pp": float(top["atr_vs_best_cousin_pp"]),
            "atr_vs_bh_pp": float(top["atr_vs_bh_pp"]),
            "atr_trades": int(top["atr_trades"]),
        })
    return pd.DataFrame(rows)


# ── sweep on holdout: every config on 2024-09 → 2026-05 ───────────────────────

def run_sweep_holdout(entry_list, period_list, k_list, size_list) -> pd.DataFrame:
    df_h = _window_df(HOLDOUT_START, HOLDOUT_END)
    warm_h = int((df_h["ts"] < pd.Timestamp(HOLDOUT_START)).sum())
    rows = []
    for n in entry_list:
        for p in period_list:
            for k in k_list:
                for size in size_list:
                    atr = _run_atr(df_h, entry_lookback=n, atr_period=p,
                                   atr_K=k, position_size_pct=size,
                                   warmup_bars=warm_h)
                    rows.append({
                        "entry_lookback": n, "atr_period": p,
                        "atr_K": k, "position_size_pct": size,
                        "holdout_apr_pct": atr.apr_pct,
                        "holdout_dd_pct": atr.max_dd_pct,
                        "holdout_final_eq": atr.final_equity,
                        "holdout_trades": atr.trades,
                        "holdout_sharpe": atr.sharpe,
                    })
    return pd.DataFrame(rows)


# ── headline pick (Steven's Open Q #3 default — strict override gate) ─────────

def pick_headline_config(sweep_df: pd.DataFrame, holdout_df: pd.DataFrame) -> dict:
    """Apply Steven's Open Q #3 default: KEEP the a-priori 20/14/3.0/100%
    UNLESS another config wins by ≥+5pp annualised on the *training* regimes
    (mean Δ-vs-best-cousin across the four regimes) AND matches that lead
    on the 2024-09→2026-05 holdout APR.

    "Lead by ≥+5pp" is checked vs the a-priori config's own training-mean
    Δ-vs-best-cousin score; the same threshold is applied on holdout APR
    (challenger_holdout − apriori_holdout ≥ +5pp).

    If no challenger clears both bars → return the a-priori. Otherwise return
    the leading challenger.
    """
    # Score every config by mean Δ-vs-best-cousin across the four regimes.
    score = (sweep_df.groupby(
                ["entry_lookback", "atr_period", "atr_K", "position_size_pct"]
            )["atr_vs_best_cousin_pp"].mean()
            .reset_index().rename(columns={"atr_vs_best_cousin_pp": "mean_train_delta_pp"}))
    # Merge holdout APR for every config.
    score = score.merge(
        holdout_df[["entry_lookback", "atr_period", "atr_K",
                    "position_size_pct", "holdout_apr_pct", "holdout_dd_pct",
                    "holdout_trades"]],
        on=["entry_lookback", "atr_period", "atr_K", "position_size_pct"],
        how="left")

    apriori = score[
        (score["entry_lookback"] == APRIORI_N)
        & (score["atr_period"] == APRIORI_P)
        & (score["atr_K"] == APRIORI_K)
        & (score["position_size_pct"] == APRIORI_SIZE)]
    if len(apriori) == 0:
        # A-priori not in the sweep — happens only in --quick; fall back to
        # the best mean-train-delta config.
        score_sorted = score.sort_values("mean_train_delta_pp", ascending=False)
        pick = score_sorted.iloc[0]
        return {
            "entry_lookback": int(pick["entry_lookback"]),
            "atr_period": int(pick["atr_period"]),
            "atr_K": float(pick["atr_K"]),
            "position_size_pct": float(pick["position_size_pct"]),
            "rationale": "A-priori 20/14/3.0/100% not in sweep grid; picked best mean Δ-vs-cousin instead.",
            "override_fired": False,
        }
    apriori = apriori.iloc[0]
    apriori_train = float(apriori["mean_train_delta_pp"])
    apriori_hold = float(apriori["holdout_apr_pct"])

    # Eligible challengers: train_delta ≥ apriori + 5pp AND holdout ≥ apriori + 5pp.
    challengers = score[
        (score["mean_train_delta_pp"] >= apriori_train + OVERRIDE_LEAD_PP)
        & (score["holdout_apr_pct"] >= apriori_hold + OVERRIDE_LEAD_PP)
        & ~((score["entry_lookback"] == APRIORI_N)
            & (score["atr_period"] == APRIORI_P)
            & (score["atr_K"] == APRIORI_K)
            & (score["position_size_pct"] == APRIORI_SIZE))]
    if len(challengers) > 0:
        # Order by training lead, break ties by holdout.
        challengers = challengers.sort_values(
            ["mean_train_delta_pp", "holdout_apr_pct"], ascending=False)
        c = challengers.iloc[0]
        return {
            "entry_lookback": int(c["entry_lookback"]),
            "atr_period": int(c["atr_period"]),
            "atr_K": float(c["atr_K"]),
            "position_size_pct": float(c["position_size_pct"]),
            "rationale": (f"K-sweep override: training Δ={c['mean_train_delta_pp']:+.2f}pp "
                          f"vs a-priori {apriori_train:+.2f}pp (lead "
                          f"{c['mean_train_delta_pp']-apriori_train:+.2f}pp); "
                          f"holdout APR {c['holdout_apr_pct']:+.2f}% vs a-priori "
                          f"{apriori_hold:+.2f}% (lead "
                          f"{c['holdout_apr_pct']-apriori_hold:+.2f}pp). Both ≥ +5pp."),
            "override_fired": True,
            "apriori_train_delta_pp": apriori_train,
            "apriori_holdout_apr_pct": apriori_hold,
            "challenger_train_delta_pp": float(c["mean_train_delta_pp"]),
            "challenger_holdout_apr_pct": float(c["holdout_apr_pct"]),
        }
    # No override.
    return {
        "entry_lookback": APRIORI_N,
        "atr_period": APRIORI_P,
        "atr_K": APRIORI_K,
        "position_size_pct": APRIORI_SIZE,
        "rationale": (f"A-priori kept (Steven's Open Q #3 default). Training Δ="
                      f"{apriori_train:+.2f}pp, holdout APR={apriori_hold:+.2f}%. "
                      f"No challenger cleared the ≥+5pp double-lead bar."),
        "override_fired": False,
        "apriori_train_delta_pp": apriori_train,
        "apriori_holdout_apr_pct": apriori_hold,
    }


# ── walk-forward at headline config ─────────────────────────────────────────

def run_walkforward(*, entry_lookback, atr_period, atr_K, position_size_pct,
                    test_months=24, stride_months=6,
                    series_start="2019-01-01"):
    folds = []
    series_start_ts = pd.Timestamp(series_start)
    holdout_ts = pd.Timestamp(HOLDOUT_START)
    cursor = series_start_ts + pd.Timedelta(days=WARMUP_DAYS)
    while cursor + pd.DateOffset(months=test_months) <= holdout_ts:
        folds.append((cursor, cursor + pd.DateOffset(months=test_months)))
        cursor += pd.DateOffset(months=stride_months)

    rows = []
    for ts, te in folds:
        df = _window_df(ts.strftime("%Y-%m-%d"), te.strftime("%Y-%m-%d"))
        warm_used = int((df["ts"] < ts).sum())
        atr = _run_atr(df, entry_lookback=entry_lookback,
                       atr_period=atr_period, atr_K=atr_K,
                       position_size_pct=position_size_pct,
                       warmup_bars=warm_used)
        bh = _run_buyhold(df, warmup_bars=warm_used)
        tf = _run_trend(df, ma_hours=TREND_FAST_MA_HOURS, warmup_bars=warm_used)
        ts_b = _run_trend(df, ma_hours=TREND_SLOW_MA_HOURS, warmup_bars=warm_used)
        don = _run_donchian(df, entry_lookback=DONCHIAN_N,
                            exit_lookback=DONCHIAN_M, warmup_bars=warm_used)
        rows.append({
            "fold_start": ts.strftime("%Y-%m-%d"),
            "fold_end":   te.strftime("%Y-%m-%d"),
            "atr_apr": atr.apr_pct, "atr_return": atr.return_pct,
            "atr_dd": atr.max_dd_pct, "atr_sharpe": atr.sharpe,
            "atr_trades": atr.trades,
            "trend_fast_apr": tf.apr_pct, "trend_slow_apr": ts_b.apr_pct,
            "donchian_apr": don.apr_pct, "bh_apr": bh.apr_pct,
        })

    # Holdout fold.
    df_h = _window_df(HOLDOUT_START, HOLDOUT_END)
    warm_h = int((df_h["ts"] < pd.Timestamp(HOLDOUT_START)).sum())
    atr_h = _run_atr(df_h, entry_lookback=entry_lookback,
                     atr_period=atr_period, atr_K=atr_K,
                     position_size_pct=position_size_pct, warmup_bars=warm_h)
    bh_h = _run_buyhold(df_h, warmup_bars=warm_h)
    tf_h = _run_trend(df_h, ma_hours=TREND_FAST_MA_HOURS, warmup_bars=warm_h)
    ts_h = _run_trend(df_h, ma_hours=TREND_SLOW_MA_HOURS, warmup_bars=warm_h)
    don_h = _run_donchian(df_h, entry_lookback=DONCHIAN_N,
                          exit_lookback=DONCHIAN_M, warmup_bars=warm_h)
    rows.append({
        "fold_start": "HOLDOUT " + HOLDOUT_START,
        "fold_end":   HOLDOUT_END,
        "atr_apr": atr_h.apr_pct, "atr_return": atr_h.return_pct,
        "atr_dd": atr_h.max_dd_pct, "atr_sharpe": atr_h.sharpe,
        "atr_trades": atr_h.trades,
        "trend_fast_apr": tf_h.apr_pct, "trend_slow_apr": ts_h.apr_pct,
        "donchian_apr": don_h.apr_pct, "bh_apr": bh_h.apr_pct,
    })
    return pd.DataFrame(rows), atr_h, tf_h, ts_h, don_h, bh_h


# ── per-regime + holdout head-to-head comparison ──────────────────────────────

def run_comparison(*, entry_lookback, atr_period, atr_K, position_size_pct):
    rows = []
    windows = REGIMES + [("holdout", HOLDOUT_START, HOLDOUT_END, "")]
    for regime_name, start, end, _why in windows:
        df = _window_df(start, end)
        warm_used = int((df["ts"] < pd.Timestamp(start)).sum())
        atr = _run_atr(df, entry_lookback=entry_lookback,
                       atr_period=atr_period, atr_K=atr_K,
                       position_size_pct=position_size_pct,
                       warmup_bars=warm_used)
        tf = _run_trend(df, ma_hours=TREND_FAST_MA_HOURS, warmup_bars=warm_used)
        ts_b = _run_trend(df, ma_hours=TREND_SLOW_MA_HOURS, warmup_bars=warm_used)
        don = _run_donchian(df, entry_lookback=DONCHIAN_N,
                            exit_lookback=DONCHIAN_M, warmup_bars=warm_used)
        bh = _run_buyhold(df, warmup_bars=warm_used)
        for name, r in [("atr_breakout", atr), ("trend_fast", tf),
                         ("trend_slow", ts_b), ("donchian_20_10", don),
                         ("buyhold", bh)]:
            rows.append({
                "regime": regime_name, "bot": name,
                "final_eq": r.final_equity,
                "return_pct": r.return_pct,
                "apr_pct": r.apr_pct,
                "max_dd_pct": r.max_dd_pct,
                "sharpe": r.sharpe,
                "trades": r.trades,
                "btc_held": r.btc_held,
                "days": r.days,
            })
    return pd.DataFrame(rows)


# ── capital sensitivity ───────────────────────────────────────────────────────

def run_capital_sensitivity(*, entry_lookback, atr_period, atr_K,
                            position_size_pct,
                            start="2019-03-01", end=HOLDOUT_END):
    rows = []
    df = _window_df(start, end)
    warm_used = int((df["ts"] < pd.Timestamp(start)).sum())
    for capital, slip_bps_rt in CAPITAL_LEVELS:
        atr = _run_atr(df, entry_lookback=entry_lookback,
                       atr_period=atr_period, atr_K=atr_K,
                       position_size_pct=position_size_pct,
                       capital=capital, slippage_bps_rt=slip_bps_rt,
                       warmup_bars=warm_used)
        rows.append({
            "capital_usd": int(capital),
            "slippage_bps_rt": slip_bps_rt,
            "apr_pct": atr.apr_pct,
            "max_dd_pct": atr.max_dd_pct,
            "final_eq": atr.final_equity,
            "return_pct": atr.return_pct,
            "trades": atr.trades,
            "sharpe": atr.sharpe,
        })
    return pd.DataFrame(rows)


# ── slippage stress ───────────────────────────────────────────────────────────

def run_slippage_stress(*, entry_lookback, atr_period, atr_K,
                        position_size_pct,
                        start="2019-03-01", end=HOLDOUT_END):
    rows = []
    df = _window_df(start, end)
    warm_used = int((df["ts"] < pd.Timestamp(start)).sum())
    df_h = _window_df(HOLDOUT_START, HOLDOUT_END)
    warm_h = int((df_h["ts"] < pd.Timestamp(HOLDOUT_START)).sum())
    for slip in SLIPPAGE_STRESS_BPS_RT:
        atr_full = _run_atr(df, entry_lookback=entry_lookback,
                            atr_period=atr_period, atr_K=atr_K,
                            position_size_pct=position_size_pct,
                            slippage_bps_rt=slip, warmup_bars=warm_used)
        atr_h = _run_atr(df_h, entry_lookback=entry_lookback,
                         atr_period=atr_period, atr_K=atr_K,
                         position_size_pct=position_size_pct,
                         slippage_bps_rt=slip, warmup_bars=warm_h)
        rows.append({
            "slippage_bps_rt": slip,
            "label": "FANTASY (zero-cost)" if slip == 0 else
                     ("HEADLINE" if slip == HEADLINE_SLIPPAGE_BPS_RT else ""),
            "full_series_apr_pct": atr_full.apr_pct,
            "full_series_dd_pct": atr_full.max_dd_pct,
            "holdout_apr_pct": atr_h.apr_pct,
            "holdout_dd_pct": atr_h.max_dd_pct,
            "full_series_trades": atr_full.trades,
        })
    return pd.DataFrame(rows)


# ── K3 correlation on holdout (vs trend cousins + Donchian + BH) ──────────────

def run_correlation_k3(*, entry_lookback, atr_period, atr_K, position_size_pct,
                       start=HOLDOUT_START, end=HOLDOUT_END):
    """30-day rolling correlation of hourly equity-curve returns on the
    holdout window. Aggregated to a single scalar via the median. K3 gate
    fires only if BOTH (best of TrendBot-fast/slow ≥ 0.85) AND
    (Donchian-20-10 ≥ 0.85) — per spec §8.2 K3."""
    df = _window_df(start, end)
    warm_used = int((df["ts"] < pd.Timestamp(start)).sum())

    atr = _run_atr(df, entry_lookback=entry_lookback, atr_period=atr_period,
                   atr_K=atr_K, position_size_pct=position_size_pct,
                   warmup_bars=warm_used)
    tf = _run_trend(df, ma_hours=TREND_FAST_MA_HOURS, warmup_bars=warm_used)
    ts_b = _run_trend(df, ma_hours=TREND_SLOW_MA_HOURS, warmup_bars=warm_used)
    don = _run_donchian(df, entry_lookback=DONCHIAN_N,
                        exit_lookback=DONCHIAN_M, warmup_bars=warm_used)
    bh = _run_buyhold(df, warmup_bars=warm_used)

    n = min(len(atr.equity_series), len(tf.equity_series),
            len(ts_b.equity_series), len(don.equity_series),
            len(bh.equity_series))
    def _rets(eq):
        return np.diff(eq[:n]) / np.where(eq[:n-1] > 0, eq[:n-1], 1.0)
    r_atr  = _rets(atr.equity_series)
    r_tf   = _rets(tf.equity_series)
    r_ts   = _rets(ts_b.equity_series)
    r_don  = _rets(don.equity_series)
    r_bh   = _rets(bh.equity_series)

    window_bars = 30 * 24
    def _rolling_corr(a, b):
        if len(a) < window_bars:
            return np.array([])
        out = np.empty(len(a) - window_bars + 1)
        for i in range(len(out)):
            wa = a[i:i + window_bars]
            wb = b[i:i + window_bars]
            sa, sb = wa.std(), wb.std()
            if sa == 0 or sb == 0:
                out[i] = 0.0
            else:
                out[i] = float(np.corrcoef(wa, wb)[0, 1])
        return out

    rows = []
    for label, r_other in [("trend_fast", r_tf), ("trend_slow", r_ts),
                            ("donchian_20_10", r_don), ("buyhold", r_bh)]:
        corr = _rolling_corr(r_atr, r_other)
        if len(corr) == 0:
            rows.append({"vs": label, "median_corr": float("nan"),
                         "mean_corr": float("nan"), "max_corr": float("nan")})
            continue
        rows.append({
            "vs": label,
            "median_corr": float(np.median(corr)),
            "mean_corr": float(corr.mean()),
            "max_corr": float(corr.max()),
        })
    return pd.DataFrame(rows)


# ── K5 catastrophic check — scan every sweep run + capital/slippage ───────────

def run_catastrophic_check(sweep_df: pd.DataFrame, cap_df: pd.DataFrame,
                            slip_df: pd.DataFrame, headline: dict) -> pd.DataFrame:
    """K5 fires if any backtest run produced a NaN equity, a non-positive
    equity point, or a 'halted' final state that locked in below-zero. The
    sweep_df / cap_df / slip_df rows already carry final equity + dd; we
    confirm by re-running the headline at crash windows and inspecting series
    explicitly."""
    issues = []
    # Sweep rows: negative final equity is a flag.
    for _, row in sweep_df.iterrows():
        if row["atr_final_eq"] <= 0:
            issues.append({"context": f"sweep regime={row['regime']} "
                                       f"N={row['entry_lookback']}/p={row['atr_period']}"
                                       f"/K={row['atr_K']}/sz={row['position_size_pct']}",
                            "issue": "non-positive final equity"})
        if row["atr_dd_pct"] >= 100.0:
            issues.append({"context": f"sweep regime={row['regime']} "
                                       f"N={row['entry_lookback']}/p={row['atr_period']}"
                                       f"/K={row['atr_K']}/sz={row['position_size_pct']}",
                            "issue": "100% drawdown"})
    for _, row in cap_df.iterrows():
        if row["final_eq"] <= 0 or row["max_dd_pct"] >= 100.0:
            issues.append({"context": f"capital ${int(row['capital_usd']):,}",
                            "issue": "non-positive eq / 100% DD"})
    for _, row in slip_df.iterrows():
        if row["full_series_apr_pct"] < -100.0:
            issues.append({"context": f"slippage {row['slippage_bps_rt']}bps",
                            "issue": "below-zero APR"})

    # Re-run headline at crash micro-window for explicit equity-series scan.
    for label, start, end in [("crash_2020_03", "2020-03-01", "2020-04-15"),
                               ("crash_2022_11", "2022-10-15", "2022-12-01"),
                               ("crash_2024_08", "2024-07-15", "2024-09-01")]:
        df = _window_df(start, end)
        warm_used = int((df["ts"] < pd.Timestamp(start)).sum())
        atr = _run_atr(df, entry_lookback=headline["entry_lookback"],
                       atr_period=headline["atr_period"],
                       atr_K=headline["atr_K"],
                       position_size_pct=headline["position_size_pct"],
                       warmup_bars=warm_used)
        if atr.nan_seen:
            issues.append({"context": label, "issue": "NaN in equity series"})
        if atr.negative_eq_seen:
            issues.append({"context": label, "issue": "non-positive eq in series"})

    if not issues:
        return pd.DataFrame([{"context": "ALL RUNS", "issue": "none — K5 PASS"}])
    return pd.DataFrame(issues)


# ── orchestrator ──────────────────────────────────────────────────────────────

def main(quick=False):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    mode = "QUICK" if quick else "FULL"
    print(f"\n=== ATR BREAKOUT — GATE 3 BACKTEST ({mode}) ===\n")
    t0 = time.time()

    entries = QUICK_ENTRY if quick else ENTRY_LB_SWEEP
    periods = QUICK_PERIOD if quick else ATR_PERIOD_SWEEP
    ks = QUICK_K if quick else ATR_K_SWEEP
    sizes = QUICK_SIZE if quick else POSITION_PCT_SWEEP

    n_cfg = len(entries) * len(periods) * len(ks) * len(sizes)
    n_total = n_cfg * len(REGIMES)
    print(f"[1/8] Parameter sweep across {len(REGIMES)} training regimes...")
    print(f"     N ∈ {entries} × p ∈ {periods} × K ∈ {ks} × size ∈ {sizes} "
          f"= {n_cfg} configs × {len(REGIMES)} regimes = {n_total} runs")
    sweep_df = run_sweep(entries, periods, ks, sizes)
    sweep_df.to_csv(OUTDIR / "sweep_results.csv", index=False)

    print("\n[2/8] Per-regime winners (Δ APR vs best cousin)...")
    regime_winners = per_regime_winners(sweep_df)
    regime_winners.to_csv(OUTDIR / "regime_winners.csv", index=False)
    for _, row in regime_winners.iterrows():
        print(f"     {row['regime']:6s}  N={int(row['top_N']):2d} p={int(row['top_p']):2d} "
              f"K={row['top_K']:.1f} size={row['top_size']*100:5.1f}%  | "
              f"atr_apr={row['atr_apr_pct']:+7.2f}%  "
              f"cousin_apr={row['best_cousin_apr_pct']:+7.2f}%  "
              f"Δ={row['atr_vs_best_cousin_pp']:+6.2f}pp  "
              f"dd={row['atr_dd_pct']:5.2f}%  tr={int(row['atr_trades']):2d}")

    print("\n[3/8] Sweep on holdout (every config, 2024-09 → 2026-05)...")
    holdout_df = run_sweep_holdout(entries, periods, ks, sizes)
    holdout_df.to_csv(OUTDIR / "sweep_holdout.csv", index=False)
    # Sort for inspection.
    hold_show = holdout_df.sort_values("holdout_apr_pct", ascending=False).head(8)
    print("     Top 8 holdout configs:")
    for _, row in hold_show.iterrows():
        print(f"       N={int(row['entry_lookback']):2d} p={int(row['atr_period']):2d} "
              f"K={row['atr_K']:.1f} size={row['position_size_pct']*100:5.1f}%  | "
              f"holdout_apr={row['holdout_apr_pct']:+7.2f}%  "
              f"dd={row['holdout_dd_pct']:5.2f}%  tr={int(row['holdout_trades'])}")

    print("\n     HEADLINE CONFIG pick (Steven's Open Q #3 default — strict override gate)...")
    headline = pick_headline_config(sweep_df, holdout_df)
    print(f"     {headline['rationale']}")
    print(f"     PICK: N={headline['entry_lookback']}, p={headline['atr_period']}, "
          f"K={headline['atr_K']:.1f}, size={int(headline['position_size_pct']*100)}%  "
          f"(override_fired={headline['override_fired']})")
    with open(OUTDIR / "winner.json", "w") as f:
        json.dump(headline, f, indent=2, default=float)

    print("\n[4/8] Walk-forward at headline config + holdout...")
    wf_df, atr_holdout, tf_holdout, ts_holdout, don_holdout, bh_holdout = run_walkforward(
        entry_lookback=headline["entry_lookback"],
        atr_period=headline["atr_period"],
        atr_K=headline["atr_K"],
        position_size_pct=headline["position_size_pct"])
    wf_df.to_csv(OUTDIR / "walkforward_results.csv", index=False)
    for _, row in wf_df.iterrows():
        print(f"     {row['fold_start']:>22s} → {row['fold_end']:<12s}  "
              f"atr={row['atr_apr']:+7.2f}%  tf={row['trend_fast_apr']:+7.2f}%  "
              f"ts={row['trend_slow_apr']:+7.2f}%  don={row['donchian_apr']:+7.2f}%  "
              f"bh={row['bh_apr']:+7.2f}%  dd={row['atr_dd']:5.2f}%  "
              f"tr={int(row['atr_trades']):2d}")

    print("\n[5/8] Head-to-head comparison per regime + holdout...")
    cmp_df = run_comparison(entry_lookback=headline["entry_lookback"],
                            atr_period=headline["atr_period"],
                            atr_K=headline["atr_K"],
                            position_size_pct=headline["position_size_pct"])
    cmp_df.to_csv(OUTDIR / "comparison_results.csv", index=False)
    pivot_apr = cmp_df.pivot(index="regime", columns="bot", values="apr_pct")
    pivot_dd  = cmp_df.pivot(index="regime", columns="bot", values="max_dd_pct")
    print("\n     APR % by regime:")
    print(pivot_apr.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:+8.2f}"))
    print("\n     Max DD % by regime:")
    print(pivot_dd.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:7.2f}"))

    print("\n[6/8] Capital sensitivity ($10k / $100k / $1M)...")
    cap_df = run_capital_sensitivity(entry_lookback=headline["entry_lookback"],
                                      atr_period=headline["atr_period"],
                                      atr_K=headline["atr_K"],
                                      position_size_pct=headline["position_size_pct"])
    cap_df.to_csv(OUTDIR / "capital_sensitivity.csv", index=False)
    for _, row in cap_df.iterrows():
        print(f"     ${int(row['capital_usd']):>10,d}  slip={row['slippage_bps_rt']:5.1f}bps  "
              f"apr={row['apr_pct']:+7.2f}%  dd={row['max_dd_pct']:5.2f}%  "
              f"final=${row['final_eq']:>15,.0f}  trades={int(row['trades'])}")

    print("\n[7/8] Slippage stress (0 / 5 / 10 / 20 bps RT)...")
    slip_df = run_slippage_stress(entry_lookback=headline["entry_lookback"],
                                   atr_period=headline["atr_period"],
                                   atr_K=headline["atr_K"],
                                   position_size_pct=headline["position_size_pct"])
    slip_df.to_csv(OUTDIR / "slippage_stress.csv", index=False)
    for _, row in slip_df.iterrows():
        print(f"     slip={row['slippage_bps_rt']:5.1f}bps {row['label']:>22s}  "
              f"full_apr={row['full_series_apr_pct']:+7.2f}%  "
              f"holdout_apr={row['holdout_apr_pct']:+7.2f}%  "
              f"full_dd={row['full_series_dd_pct']:5.2f}%  "
              f"trades={int(row['full_series_trades'])}")

    print("\n[8/8] K3 correlation + K5 catastrophic check...")
    corr_df = run_correlation_k3(entry_lookback=headline["entry_lookback"],
                                  atr_period=headline["atr_period"],
                                  atr_K=headline["atr_K"],
                                  position_size_pct=headline["position_size_pct"])
    corr_df.to_csv(OUTDIR / "correlation_k3.csv", index=False)
    for _, row in corr_df.iterrows():
        print(f"     vs {row['vs']:>16s}  median={row['median_corr']:+.3f}  "
              f"mean={row['mean_corr']:+.3f}  max={row['max_corr']:+.3f}")

    cat_df = run_catastrophic_check(sweep_df, cap_df, slip_df, headline)
    cat_df.to_csv(OUTDIR / "catastrophic_check.csv", index=False)
    print("     K5 check:")
    for _, row in cat_df.iterrows():
        print(f"       {row['context']:>40s}  {row['issue']}")

    print(f"\nDONE in {time.time() - t0:.1f}s. Artifacts under {OUTDIR}")
    print("Next: write ~/Documents/bsf-research-briefs/results/06-atr-breakout-gate3-results.md from these CSVs.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="smaller sweep for iteration")
    args = parser.parse_args()
    main(quick=args.quick)
