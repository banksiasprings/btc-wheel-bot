"""
donchian_backtest.py — Gate 3 backtest harness for the Donchian Channel
Breakout bot (spec 05).

NOT a live deployment. Bot is being evaluated against Steven's
**portfolio-specialist scorecard** (Scorecard B — bull-leg amplifier).
This file runs:

  1. Per-regime parameter sweep — 27 configs × 4 regimes = 108 runs:
       entry_lookback     ∈ {20, 28, 55}
       exit_lookback      ∈ {10, 12, 20}
       position_size_pct  ∈ {0.25, 0.50, 1.00}
  2. Per-regime winner selection + a global headline pick (specialist
     scorecard: bull-leg lift vs `TrendBot`, bear/crab/crash bounded bleed).
  3. Walk-forward at the winning config across 2019-01 → 2024-09 (anti-
     cherry-pick check) + held-out tail 2024-09 → 2026-05 (K4 gate).
  4. Capital-level sensitivity at $10k / $100k / $1M with scaled slippage
     (10 / 10 / 25 bps RT) per spec §7.3 + master plan rule (added after
     Basis-Arb retro).
  5. Slippage stress at 0 / 5 / 10 / 20 bps RT (0 labelled FANTASY).
  6. Three-variant comparison (20/10 vs 28/12 vs 55/20) at headline size +
     slippage.
  7. K3 correlation: 30-day rolling correlation of Donchian-equity-curve
     returns vs `TrendBot` returns at the winning config, holdout window.
  8. Head-to-head vs `TrendBot(168h)`, `TrendBot(1200h)`, `BuyHoldBot`
     per regime + holdout using the same fee model the live paper bots use.

Outputs (all under docs/gate3-reports/05-donchian-data/):
  - sweep_results.csv            — (regime, config) → metrics row
  - regime_winners.csv           — per-regime top configs
  - winner.json                  — chosen variant + reasoning
  - walkforward_results.csv      — fold-by-fold at winning config + holdout
  - comparison_results.csv       — Donchian vs TrendBot-fast / TrendBot-slow / BuyHold per regime
  - capital_sensitivity.csv      — winning config at $10k / $100k / $1M
  - slippage_stress.csv          — winning config at 0 / 5 / 10 / 20 bps RT
  - variant_compare.csv          — 20/10 vs 28/12 vs 55/20 at headline
  - correlation_k3.csv           — rolling-30d corr vs TrendBot at holdout

The narrative pass/fail Gate 3 report is at docs/gate3-reports/05-donchian.md.

Run:
    python3.11 donchian_backtest.py            # full Gate 3 run (~seconds)
    python3.11 donchian_backtest.py --quick    # smaller sweep for iteration
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

from more_bots import DonchianBot, TrendBot, BuyHoldBot

ROOT = Path(__file__).resolve().parent.parent
HOURLY = ROOT / "data" / "raw" / "spot" / "btc_1h.csv"
OUTDIR = ROOT / "docs" / "gate3-reports" / "05-donchian-data"
FEE = 0.0006
HOURS_PER_YEAR = 24 * 365
DEFAULT_CAPITAL = 10_000.0
# Need entry_lookback + 1 daily closes before first signal; for the 55-day
# variant that is 56 days. Use 60 days of warmup with a 5-day safety margin.
WARMUP_DAYS = 60
# Production TrendBot variants (per `grid_farm.py:VARIANTS`).
TREND_FAST_MA_HOURS = 168    # 7-day MA — "trend-fast"
TREND_SLOW_MA_HOURS = 1200   # 50-day MA — "trend-slow"

# ── regime windows (same four as Phase 1 + held-out tail) ─────────────────────
# Hourly data starts 2019-01-01; Covid crash is the earliest dislocation in
# scope. The holdout window matches DCA-Smart's so cross-bot comparisons stack
# directly.

REGIMES = [
    ("bull",  "2020-10-01", "2021-04-15",
     "BTC ~$10k → ~$63k — the canonical persistent bull leg. Specialist regime."),
    ("bear",  "2021-11-10", "2022-11-22",
     "Cycle top $69k → FTX low ~$16k. Long-only Donchian sits flat most of window."),
    ("crab",  "2022-12-01", "2023-10-16",
     "Post-FTX range, ~$17k → ~$28k. The chop / whipsaw stress test."),
    ("crash", "2020-03-01", "2020-04-15",
     "Covid -50% in 2 days then v-bottom. Catastrophic-resistance check."),
]
HOLDOUT_START = "2024-09-01"
HOLDOUT_END   = "2026-05-22"

# ── parameter sweep — 3 × 3 × 3 = 27 configs per regime ──────────────────────
# Three a-priori Donchian variants (20/10, 28/12, 55/20) crossed with three
# position sizes (binary 100% headline + 50%/25% for size-sensitivity).

ENTRY_LB_SWEEP = [20, 28, 55]
EXIT_LB_SWEEP  = [10, 12, 20]
POSITION_PCT_SWEEP = [0.25, 0.50, 1.00]

QUICK_ENTRY = [20, 55]
QUICK_EXIT  = [10, 20]
QUICK_SIZE  = [1.00]

# Slippage stress rows (bps round-trip — applied as half per side on top of
# the bot's native FEE). 0 is FANTASY (matches Boring Edge's cost model).
SLIPPAGE_STRESS_BPS_RT = [0.0, 5.0, 10.0, 20.0]
HEADLINE_SLIPPAGE_BPS_RT = 10.0

# Capital sensitivity — slippage scales with size per spec §7.3.
CAPITAL_LEVELS = [
    (10_000.0,   HEADLINE_SLIPPAGE_BPS_RT),
    (100_000.0,  HEADLINE_SLIPPAGE_BPS_RT),
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
    equity_series: np.ndarray

    @classmethod
    def from_eq(cls, eq, capital, days, *, trades, btc, cash, halt=False):
        return cls(
            final_equity=float(eq[-1]),
            return_pct=float(eq[-1] / capital - 1.0) * 100.0,
            apr_pct=annualised_return(eq, len(eq), capital) * 100.0,
            max_dd_pct=max_drawdown(eq) * 100.0,
            sharpe=sharpe(eq),
            trades=int(trades),
            btc_held=float(btc),
            cash_remaining=float(cash),
            days=float(days),
            halt_active=bool(halt),
            equity_series=eq,
        )


# ── single backtest runs ─────────────────────────────────────────────────────

def _effective_fee(slippage_bps_rt: float) -> float:
    """Bot.fee is per-side. Slippage_bps_rt is the round-trip slippage charge
    (extra cost beyond the bot's native FEE); halve and add to each side."""
    return FEE + (slippage_bps_rt / 10000.0) / 2.0


def _run_donchian(df: pd.DataFrame, *,
                  entry_lookback: int, exit_lookback: int,
                  position_size_pct: float,
                  capital: float = DEFAULT_CAPITAL,
                  slippage_bps_rt: float = HEADLINE_SLIPPAGE_BPS_RT,
                  max_dd_halt: float = 0.35,
                  warmup_bars: int = WARMUP_DAYS * 24) -> RunResult:
    """One Donchian backtest. Same fill model as the live paper bots:
    market-close fill, intrinsic FEE=0.0006 + slippage applied per side."""
    closes = df["close"].values
    bot = DonchianBot(
        capital=capital,
        entry_lookback_days=entry_lookback,
        exit_lookback_days=exit_lookback,
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


def _run_trend(df: pd.DataFrame, *, ma_hours: int,
               capital: float = DEFAULT_CAPITAL,
               warmup_bars: int = WARMUP_DAYS * 24) -> RunResult:
    """`TrendBot` at production MA. No slippage — comparison baseline uses the
    same fee model the live bot does, for an apples-to-apples Δ-vs-TrendBot."""
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


# ── per-window framing ───────────────────────────────────────────────────────

def _window_df(start: str, end: str, warmup_days: int = WARMUP_DAYS) -> pd.DataFrame:
    """Pull a regime window plus `warmup_days` of pre-window data so the bot's
    daily-close deque seeds before the measured period begins."""
    warm_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    return load_hourly(warm_start, end)


# ── sweep ────────────────────────────────────────────────────────────────────

def run_sweep(entry_list, exit_list, size_list) -> pd.DataFrame:
    rows = []
    n_configs = len(entry_list) * len(exit_list) * len(size_list)
    for regime_name, start, end, _why in REGIMES:
        df = _window_df(start, end)
        warm_bars = (df["ts"] < pd.Timestamp(start)).sum()
        bh = _run_buyhold(df, warmup_bars=int(warm_bars))
        trend_fast = _run_trend(df, ma_hours=TREND_FAST_MA_HOURS,
                                warmup_bars=int(warm_bars))
        trend_slow = _run_trend(df, ma_hours=TREND_SLOW_MA_HOURS,
                                warmup_bars=int(warm_bars))
        best_trend = trend_fast if trend_fast.apr_pct >= trend_slow.apr_pct else trend_slow
        for n in entry_list:
            for m in exit_list:
                # Spec §3: M ≤ N typically. Allow M == N but skip M > N.
                if m > n:
                    continue
                for size in size_list:
                    t0 = time.time()
                    don = _run_donchian(df, entry_lookback=n, exit_lookback=m,
                                        position_size_pct=size,
                                        warmup_bars=int(warm_bars))
                    rows.append({
                        "regime": regime_name,
                        "entry_lookback": n, "exit_lookback": m,
                        "position_size_pct": size,
                        "don_final_eq": don.final_equity,
                        "don_return_pct": don.return_pct,
                        "don_apr_pct": don.apr_pct,
                        "don_dd_pct": don.max_dd_pct,
                        "don_sharpe": don.sharpe,
                        "don_trades": don.trades,
                        "trend_fast_apr_pct": trend_fast.apr_pct,
                        "trend_slow_apr_pct": trend_slow.apr_pct,
                        "best_trend_apr_pct": best_trend.apr_pct,
                        "bh_apr_pct": bh.apr_pct,
                        "don_vs_best_trend_pp": don.apr_pct - best_trend.apr_pct,
                        "don_vs_bh_pp": don.apr_pct - bh.apr_pct,
                        "runtime_s": round(time.time() - t0, 3),
                    })
        print(f"  swept regime={regime_name:6s} ({len(df)} bars, ≤{n_configs} configs)")
    return pd.DataFrame(rows)


def per_regime_winners(sweep_df: pd.DataFrame) -> pd.DataFrame:
    """Top config per regime, ranked by Δ APR vs best `TrendBot`."""
    rows = []
    for regime_name, _s, _e, _why in REGIMES:
        sub = sweep_df[sweep_df["regime"] == regime_name].copy()
        sub = sub.sort_values("don_vs_best_trend_pp", ascending=False)
        top = sub.iloc[0]
        rows.append({
            "regime": regime_name,
            "top_entry": int(top["entry_lookback"]),
            "top_exit":  int(top["exit_lookback"]),
            "top_size":  float(top["position_size_pct"]),
            "don_apr_pct": float(top["don_apr_pct"]),
            "don_dd_pct":  float(top["don_dd_pct"]),
            "best_trend_apr_pct": float(top["best_trend_apr_pct"]),
            "don_vs_trend_pp": float(top["don_vs_best_trend_pp"]),
            "don_vs_bh_pp":    float(top["don_vs_bh_pp"]),
            "don_trades":      int(top["don_trades"]),
        })
    return pd.DataFrame(rows)


def pick_headline_variant(sweep_df: pd.DataFrame,
                          variant_df: pd.DataFrame) -> dict:
    """Pick the production headline variant from {20/10, 28/12, 55/20} at
    full size (100%) under the specialist scorecard.

    Two-stage selection:
      Stage 1 — HARD FILTER on the kill conditions that apply to the deploy
                candidate specifically:
                  K4: holdout APR ≥ 5%  (variant_compare holdout column)
                  K2: full-series max DD ≤ 35%  (variant_compare full column)
      Stage 2 — among K4/K2-passing variants, rank by specialist score on
                the regime sweep:
                  TIER 1 — bull ≥ +3pp AND crab ≥ −10pp AND bear ≥ −5pp.
                  TIER 2 — bull ≥ 0pp AND crab ≥ −15pp.
                  FALLBACK composite: bull_lift − 0.5×|crab bleed| − 0.5×|bear bleed|.

    If NO variant clears K4 + K2, the harness returns the variant with the
    highest holdout APR anyway (so the downstream walk-forward / correlation
    runs have a target), and the rationale records the kill-condition fail
    — the report skeleton's verdict line is then PARK.
    """
    variants = [(20, 10), (28, 12), (55, 20)]
    by_v = {}
    for n, m in variants:
        sub = sweep_df[(sweep_df["entry_lookback"] == n)
                       & (sweep_df["exit_lookback"] == m)
                       & (sweep_df["position_size_pct"] == 1.00)]
        if len(sub) == 0:
            continue
        bull  = sub[sub["regime"] == "bull"].iloc[0]
        bear  = sub[sub["regime"] == "bear"].iloc[0]
        crab  = sub[sub["regime"] == "crab"].iloc[0]
        crash = sub[sub["regime"] == "crash"].iloc[0]
        vc = variant_df[variant_df["variant"] == f"{n}/{m}"]
        if len(vc) == 0:
            continue
        vc = vc.iloc[0]
        by_v[(n, m)] = {
            "bull_vs_trend": float(bull["don_vs_best_trend_pp"]),
            "bear_vs_trend": float(bear["don_vs_best_trend_pp"]),
            "crab_vs_trend": float(crab["don_vs_best_trend_pp"]),
            "crash_vs_trend": float(crash["don_vs_best_trend_pp"]),
            "bull_apr":  float(bull["don_apr_pct"]),
            "bear_apr":  float(bear["don_apr_pct"]),
            "crab_apr":  float(crab["don_apr_pct"]),
            "crash_apr": float(crash["don_apr_pct"]),
            "bull_dd":   float(bull["don_dd_pct"]),
            "bear_dd":   float(bear["don_dd_pct"]),
            "crab_dd":   float(crab["don_dd_pct"]),
            "crash_dd":  float(crash["don_dd_pct"]),
            "holdout_apr":  float(vc["holdout_apr_pct"]),
            "holdout_dd":   float(vc["holdout_dd_pct"]),
            "full_apr":     float(vc["full_series_apr_pct"]),
            "full_dd":      float(vc["full_series_dd_pct"]),
            "full_sharpe":  float(vc["full_series_sharpe"]),
            "passes_k4":    float(vc["holdout_apr_pct"]) >= 5.0,
            "passes_k2":    float(vc["full_series_dd_pct"]) <= 35.0,
        }

    eligible = {v: m for v, m in by_v.items() if m["passes_k4"] and m["passes_k2"]}

    def _tier_pick(pool: dict):
        tier1, tier2, fallback = [], [], []
        for v, m in pool.items():
            if (m["bull_vs_trend"] >= 3.0
                    and m["crab_vs_trend"] >= -10.0
                    and m["bear_vs_trend"] >= -5.0):
                tier1.append((v, m))
            if m["bull_vs_trend"] >= 0.0 and m["crab_vs_trend"] >= -15.0:
                tier2.append((v, m))
            score = m["bull_vs_trend"] - 0.5 * max(-m["crab_vs_trend"], 0.0) \
                                       - 0.5 * max(-m["bear_vs_trend"], 0.0)
            fallback.append((v, m, score))
        if tier1:
            tier1.sort(key=lambda x: (-x[1]["bull_vs_trend"], x[1]["crab_dd"]))
            return tier1[0][0], tier1[0][1], f"TIER 1 PASS ({len(tier1)}/{len(pool)})"
        if tier2:
            tier2.sort(key=lambda x: (-x[1]["bull_vs_trend"], x[1]["crab_dd"]))
            return tier2[0][0], tier2[0][1], f"TIER 2 PASS ({len(tier2)}/{len(pool)})"
        fallback.sort(key=lambda x: -x[2])
        return fallback[0][0], fallback[0][1], f"FALLBACK (composite score)"

    if eligible:
        pick, mets, tier_label = _tier_pick(eligible)
        rationale = (f"{tier_label} among {len(eligible)}/{len(by_v)} variants "
                     f"that cleared K2 (full-series DD ≤ 35%) AND K4 (holdout APR ≥ 5%).")
        pass_status = "K2+K4 PASS"
    else:
        # Force a pick by best holdout APR so downstream stages have a target;
        # report status flags the kill-condition failure.
        pick, mets, _ = max(
            [(v, m, m["holdout_apr"]) for v, m in by_v.items()],
            key=lambda x: x[2])
        rationale = ("NO variant cleared K2 + K4. Picked by best holdout APR "
                     "so downstream stages have a target; verdict is PARK (cite "
                     "K2/K4 in report).")
        pass_status = "K2+K4 FAIL"

    return {
        "entry_lookback": pick[0],
        "exit_lookback":  pick[1],
        "position_size_pct": 1.00,
        "rationale": rationale,
        "pass_status": pass_status,
        "n_eligible": len(eligible),
        "metrics": mets,
        "by_variant": {f"{n}/{m}": v for (n, m), v in by_v.items()},
    }


# ── walk-forward at the winning config ───────────────────────────────────────

def run_walkforward(*, entry_lookback, exit_lookback, position_size_pct,
                    test_months=24, stride_months=6,
                    series_start="2019-01-01"):
    """24-month fold windows, 6-month stride (≈1/4 of test window, per spec
    §7.3). Config is fixed at the chosen headline variant; the walk-forward
    is anti-cherry-pick characterisation, not parameter fitting (config is
    a-priori per spec §11)."""
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
        warm_used = (df["ts"] < ts).sum()
        don = _run_donchian(df, entry_lookback=entry_lookback,
                            exit_lookback=exit_lookback,
                            position_size_pct=position_size_pct,
                            warmup_bars=int(warm_used))
        bh = _run_buyhold(df, warmup_bars=int(warm_used))
        trend_fast = _run_trend(df, ma_hours=TREND_FAST_MA_HOURS,
                                warmup_bars=int(warm_used))
        trend_slow = _run_trend(df, ma_hours=TREND_SLOW_MA_HOURS,
                                warmup_bars=int(warm_used))
        rows.append({
            "fold_start": ts.strftime("%Y-%m-%d"),
            "fold_end":   te.strftime("%Y-%m-%d"),
            "don_apr": don.apr_pct,
            "don_return": don.return_pct,
            "don_dd": don.max_dd_pct,
            "don_sharpe": don.sharpe,
            "don_trades": don.trades,
            "trend_fast_apr": trend_fast.apr_pct,
            "trend_slow_apr": trend_slow.apr_pct,
            "bh_apr": bh.apr_pct,
        })

    # Holdout fold.
    df_h = _window_df(HOLDOUT_START, HOLDOUT_END)
    warm_used = (df_h["ts"] < pd.Timestamp(HOLDOUT_START)).sum()
    don_h = _run_donchian(df_h, entry_lookback=entry_lookback,
                          exit_lookback=exit_lookback,
                          position_size_pct=position_size_pct,
                          warmup_bars=int(warm_used))
    bh_h = _run_buyhold(df_h, warmup_bars=int(warm_used))
    trend_fast_h = _run_trend(df_h, ma_hours=TREND_FAST_MA_HOURS,
                              warmup_bars=int(warm_used))
    trend_slow_h = _run_trend(df_h, ma_hours=TREND_SLOW_MA_HOURS,
                              warmup_bars=int(warm_used))
    rows.append({
        "fold_start": "HOLDOUT " + HOLDOUT_START,
        "fold_end":   HOLDOUT_END,
        "don_apr": don_h.apr_pct,
        "don_return": don_h.return_pct,
        "don_dd": don_h.max_dd_pct,
        "don_sharpe": don_h.sharpe,
        "don_trades": don_h.trades,
        "trend_fast_apr": trend_fast_h.apr_pct,
        "trend_slow_apr": trend_slow_h.apr_pct,
        "bh_apr": bh_h.apr_pct,
    })
    return pd.DataFrame(rows), don_h, trend_fast_h, trend_slow_h, bh_h


# ── comparison table ─────────────────────────────────────────────────────────

def run_comparison(*, entry_lookback, exit_lookback, position_size_pct):
    rows = []
    for regime_name, start, end, _why in REGIMES + [("holdout", HOLDOUT_START, HOLDOUT_END, "")]:
        df = _window_df(start, end)
        warm_used = (df["ts"] < pd.Timestamp(start)).sum()
        don = _run_donchian(df, entry_lookback=entry_lookback,
                            exit_lookback=exit_lookback,
                            position_size_pct=position_size_pct,
                            warmup_bars=int(warm_used))
        trend_fast = _run_trend(df, ma_hours=TREND_FAST_MA_HOURS,
                                warmup_bars=int(warm_used))
        trend_slow = _run_trend(df, ma_hours=TREND_SLOW_MA_HOURS,
                                warmup_bars=int(warm_used))
        bh = _run_buyhold(df, warmup_bars=int(warm_used))
        for name, r in [("donchian", don), ("trend_fast", trend_fast),
                        ("trend_slow", trend_slow), ("buyhold", bh)]:
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


# ── capital-level sensitivity ─────────────────────────────────────────────────

def run_capital_sensitivity(*, entry_lookback, exit_lookback,
                            position_size_pct,
                            start="2019-03-01", end=HOLDOUT_END):
    """Headline config across $10k / $100k / $1M with scaled slippage.
    Long-only spot-equivalent: no margin / liquidation. Divergence (if any)
    must come from slippage and the bot's binary all-in/all-out structure
    interacting with the larger fill cost."""
    rows = []
    df = _window_df(start, end)
    warm_used = (df["ts"] < pd.Timestamp(start)).sum()
    for capital, slip_bps_rt in CAPITAL_LEVELS:
        don = _run_donchian(df, entry_lookback=entry_lookback,
                            exit_lookback=exit_lookback,
                            position_size_pct=position_size_pct,
                            capital=capital,
                            slippage_bps_rt=slip_bps_rt,
                            warmup_bars=int(warm_used))
        rows.append({
            "capital_usd": int(capital),
            "slippage_bps_rt": slip_bps_rt,
            "apr_pct": don.apr_pct,
            "max_dd_pct": don.max_dd_pct,
            "final_eq": don.final_equity,
            "return_pct": don.return_pct,
            "trades": don.trades,
            "sharpe": don.sharpe,
        })
    return pd.DataFrame(rows)


# ── slippage stress at headline config + full-series window ───────────────────

def run_slippage_stress(*, entry_lookback, exit_lookback, position_size_pct,
                        start="2019-03-01", end=HOLDOUT_END):
    rows = []
    df = _window_df(start, end)
    warm_used = (df["ts"] < pd.Timestamp(start)).sum()
    for slip in SLIPPAGE_STRESS_BPS_RT:
        don = _run_donchian(df, entry_lookback=entry_lookback,
                            exit_lookback=exit_lookback,
                            position_size_pct=position_size_pct,
                            slippage_bps_rt=slip,
                            warmup_bars=int(warm_used))
        # Holdout slice.
        df_h = _window_df(HOLDOUT_START, HOLDOUT_END)
        warm_h = (df_h["ts"] < pd.Timestamp(HOLDOUT_START)).sum()
        don_h = _run_donchian(df_h, entry_lookback=entry_lookback,
                              exit_lookback=exit_lookback,
                              position_size_pct=position_size_pct,
                              slippage_bps_rt=slip,
                              warmup_bars=int(warm_h))
        rows.append({
            "slippage_bps_rt": slip,
            "label": "FANTASY (zero-cost)" if slip == 0 else
                     ("HEADLINE" if slip == HEADLINE_SLIPPAGE_BPS_RT else ""),
            "full_series_apr_pct": don.apr_pct,
            "full_series_dd_pct": don.max_dd_pct,
            "holdout_apr_pct": don_h.apr_pct,
            "holdout_dd_pct": don_h.max_dd_pct,
            "full_series_trades": don.trades,
        })
    return pd.DataFrame(rows)


# ── three-variant compare at headline size + headline slippage ────────────────

def run_variant_compare(start="2019-03-01", end=HOLDOUT_END):
    rows = []
    df = _window_df(start, end)
    warm_used = (df["ts"] < pd.Timestamp(start)).sum()
    df_h = _window_df(HOLDOUT_START, HOLDOUT_END)
    warm_h = (df_h["ts"] < pd.Timestamp(HOLDOUT_START)).sum()
    for n, m in [(20, 10), (28, 12), (55, 20)]:
        don = _run_donchian(df, entry_lookback=n, exit_lookback=m,
                            position_size_pct=1.00,
                            warmup_bars=int(warm_used))
        don_h = _run_donchian(df_h, entry_lookback=n, exit_lookback=m,
                              position_size_pct=1.00,
                              warmup_bars=int(warm_h))
        rows.append({
            "variant": f"{n}/{m}",
            "entry_lookback": n,
            "exit_lookback": m,
            "full_series_apr_pct": don.apr_pct,
            "full_series_dd_pct":  don.max_dd_pct,
            "full_series_sharpe":  don.sharpe,
            "full_series_trades":  don.trades,
            "holdout_apr_pct":     don_h.apr_pct,
            "holdout_dd_pct":      don_h.max_dd_pct,
            "holdout_trades":      don_h.trades,
        })
    return pd.DataFrame(rows)


# ── K3 correlation check (vs TrendBot at holdout) ─────────────────────────────

def run_correlation_k3(*, entry_lookback, exit_lookback, position_size_pct,
                       start=HOLDOUT_START, end=HOLDOUT_END):
    """30-day rolling correlation of HOURLY equity-curve returns between the
    Donchian winning config and each TrendBot variant, computed on the
    holdout window. Aggregated to a single scalar via the median of monthly
    medians, matching spec §8.3 K3 ("30-day rolling corr, monthly aggregated").

    Reported numbers in the result rows: median, mean, max — the K3 gate is
    fired on `median` (robust to short transitions); max is reported for
    visibility (worst-case co-movement)."""
    df = _window_df(start, end)
    warm_used = (df["ts"] < pd.Timestamp(start)).sum()

    don = _run_donchian(df, entry_lookback=entry_lookback,
                        exit_lookback=exit_lookback,
                        position_size_pct=position_size_pct,
                        warmup_bars=int(warm_used))
    tf = _run_trend(df, ma_hours=TREND_FAST_MA_HOURS,
                    warmup_bars=int(warm_used))
    ts = _run_trend(df, ma_hours=TREND_SLOW_MA_HOURS,
                    warmup_bars=int(warm_used))
    bh = _run_buyhold(df, warmup_bars=int(warm_used))

    # Trim to common length, build per-bar returns from equity series.
    n = min(len(don.equity_series), len(tf.equity_series),
            len(ts.equity_series), len(bh.equity_series))
    def _rets(eq):
        return np.diff(eq[:n]) / np.where(eq[:n-1] > 0, eq[:n-1], 1.0)
    r_don, r_tf, r_ts, r_bh = _rets(don.equity_series), _rets(tf.equity_series), \
                              _rets(ts.equity_series), _rets(bh.equity_series)

    window_bars = 30 * 24  # 30 calendar days at hourly granularity
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
                           ("buyhold", r_bh)]:
        corr = _rolling_corr(r_don, r_other)
        if len(corr) == 0:
            rows.append({"vs": label, "median_corr": float("nan"),
                         "mean_corr": float("nan"), "max_corr": float("nan"),
                         "k3_fires": False})
            continue
        med, mean, mx = float(np.median(corr)), float(corr.mean()), float(corr.max())
        rows.append({
            "vs": label,
            "median_corr": med,
            "mean_corr": mean,
            "max_corr": mx,
            "k3_fires": (label.startswith("trend") and med >= 0.85),
        })
    return pd.DataFrame(rows)


# ── orchestrator ─────────────────────────────────────────────────────────────

def main(quick=False):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    mode = "QUICK" if quick else "FULL"
    print(f"\n=== DONCHIAN — GATE 3 BACKTEST ({mode}) ===\n")
    t0 = time.time()

    entries = QUICK_ENTRY if quick else ENTRY_LB_SWEEP
    exits   = QUICK_EXIT  if quick else EXIT_LB_SWEEP
    sizes   = QUICK_SIZE  if quick else POSITION_PCT_SWEEP

    n_cfg = sum(1 for n in entries for m in exits for _ in sizes if m <= n)
    n_total = n_cfg * len(REGIMES)
    print(f"[1/7] Parameter sweep across {len(REGIMES)} regimes...")
    print(f"     entry ∈ {entries} × exit ∈ {exits} (M≤N) × size ∈ {sizes} "
          f"= {n_cfg} configs × {len(REGIMES)} regimes = {n_total} runs")
    sweep_df = run_sweep(entries, exits, sizes)
    sweep_df.to_csv(OUTDIR / "sweep_results.csv", index=False)

    print("\n[2/7] Per-regime winners (ranked by Δ APR vs best TrendBot)...")
    regime_winners = per_regime_winners(sweep_df)
    regime_winners.to_csv(OUTDIR / "regime_winners.csv", index=False)
    for _, row in regime_winners.iterrows():
        print(f"     {row['regime']:6s}  N={int(row['top_entry']):2d} M={int(row['top_exit']):2d} "
              f"size={row['top_size']*100:5.1f}%  | don_apr={row['don_apr_pct']:+7.2f}%  "
              f"trend_apr={row['best_trend_apr_pct']:+7.2f}%  Δ={row['don_vs_trend_pp']:+6.2f}pp  "
              f"dd={row['don_dd_pct']:5.2f}%  trades={int(row['don_trades']):2d}")

    # Variant compare runs FIRST (before headline pick) — the picker uses
    # K4 (holdout) + K2 (full-series DD) as hard filters per spec §8.3.
    print("\n     Three-variant compare (20/10 vs 28/12 vs 55/20) at full 100% size...")
    var_df = run_variant_compare()
    var_df.to_csv(OUTDIR / "variant_compare.csv", index=False)
    for _, row in var_df.iterrows():
        print(f"     {row['variant']:>5s}  full: apr={row['full_series_apr_pct']:+7.2f}%  "
              f"dd={row['full_series_dd_pct']:5.2f}%  sharpe={row['full_series_sharpe']:+5.2f}  "
              f"tr={int(row['full_series_trades']):3d}  | holdout: apr={row['holdout_apr_pct']:+7.2f}%  "
              f"dd={row['holdout_dd_pct']:5.2f}%  tr={int(row['holdout_trades']):2d}")

    print("\n     HEADLINE VARIANT pick (specialist scorecard + K2+K4 filter)...")
    winner = pick_headline_variant(sweep_df, var_df)
    print(f"     {winner['pass_status']}: {winner['rationale']}")
    print(f"     PICK: Donchian {winner['entry_lookback']}/{winner['exit_lookback']} "
          f"at {int(winner['position_size_pct']*100)}%")
    for v, m in winner["by_variant"].items():
        gates = []
        if m["passes_k4"]: gates.append("K4✓")
        else: gates.append("K4✗")
        if m["passes_k2"]: gates.append("K2✓")
        else: gates.append("K2✗")
        print(f"        {v}: bull Δ={m['bull_vs_trend']:+6.2f}pp, "
              f"bear Δ={m['bear_vs_trend']:+6.2f}pp, "
              f"crab Δ={m['crab_vs_trend']:+6.2f}pp, "
              f"crash Δ={m['crash_vs_trend']:+6.2f}pp  | "
              f"hold_apr={m['holdout_apr']:+6.2f}%  full_dd={m['full_dd']:5.2f}%  [{' '.join(gates)}]")
    with open(OUTDIR / "winner.json", "w") as f:
        json.dump({k: v for k, v in winner.items() if k != "by_variant"},
                  f, indent=2, default=float)

    print("\n[3/7] Walk-forward at headline variant + holdout...")
    wf_df, don_holdout, tf_holdout, ts_holdout, bh_holdout = run_walkforward(
        entry_lookback=winner["entry_lookback"],
        exit_lookback=winner["exit_lookback"],
        position_size_pct=winner["position_size_pct"])
    wf_df.to_csv(OUTDIR / "walkforward_results.csv", index=False)
    for _, row in wf_df.iterrows():
        print(f"     {row['fold_start']:>22s} → {row['fold_end']:<12s}  "
              f"don={row['don_apr']:+7.2f}%  tf={row['trend_fast_apr']:+7.2f}%  "
              f"ts={row['trend_slow_apr']:+7.2f}%  bh={row['bh_apr']:+7.2f}%  "
              f"dd={row['don_dd']:5.2f}%  tr={int(row['don_trades']):2d}")

    print("\n[4/7] Head-to-head comparison per regime + holdout...")
    cmp_df = run_comparison(entry_lookback=winner["entry_lookback"],
                            exit_lookback=winner["exit_lookback"],
                            position_size_pct=winner["position_size_pct"])
    cmp_df.to_csv(OUTDIR / "comparison_results.csv", index=False)
    pivot_apr = cmp_df.pivot(index="regime", columns="bot", values="apr_pct")
    pivot_dd  = cmp_df.pivot(index="regime", columns="bot", values="max_dd_pct")
    print("\n     APR % by regime:")
    print(pivot_apr.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:+8.2f}"))
    print("\n     Max DD % by regime:")
    print(pivot_dd.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:7.2f}"))

    print("\n[5/7] Capital-level sensitivity ($10k / $100k / $1M)...")
    cap_df = run_capital_sensitivity(entry_lookback=winner["entry_lookback"],
                                     exit_lookback=winner["exit_lookback"],
                                     position_size_pct=winner["position_size_pct"])
    cap_df.to_csv(OUTDIR / "capital_sensitivity.csv", index=False)
    for _, row in cap_df.iterrows():
        print(f"     ${int(row['capital_usd']):>9,d}  slip={row['slippage_bps_rt']:5.1f}bps  "
              f"apr={row['apr_pct']:+7.2f}%  dd={row['max_dd_pct']:5.2f}%  "
              f"final=${row['final_eq']:>15,.0f}  trades={int(row['trades'])}")

    print("\n[6/7] Slippage stress (0 / 5 / 10 / 20 bps RT)...")
    slip_df = run_slippage_stress(entry_lookback=winner["entry_lookback"],
                                  exit_lookback=winner["exit_lookback"],
                                  position_size_pct=winner["position_size_pct"])
    slip_df.to_csv(OUTDIR / "slippage_stress.csv", index=False)
    for _, row in slip_df.iterrows():
        print(f"     slip={row['slippage_bps_rt']:5.1f}bps {row['label']:>22s}  "
              f"full_apr={row['full_series_apr_pct']:+7.2f}%  "
              f"holdout_apr={row['holdout_apr_pct']:+7.2f}%  "
              f"full_dd={row['full_series_dd_pct']:5.2f}%  "
              f"trades={int(row['full_series_trades'])}")

    print("\n[7/7] K3 correlation vs TrendBot at holdout...")
    corr_df = run_correlation_k3(entry_lookback=winner["entry_lookback"],
                                 exit_lookback=winner["exit_lookback"],
                                 position_size_pct=winner["position_size_pct"])
    corr_df.to_csv(OUTDIR / "correlation_k3.csv", index=False)
    for _, row in corr_df.iterrows():
        flag = " 🚨 K3 FIRES" if row["k3_fires"] else ""
        print(f"     vs {row['vs']:>10s}  median={row['median_corr']:+.3f}  "
              f"mean={row['mean_corr']:+.3f}  max={row['max_corr']:+.3f}{flag}")

    print(f"\nDONE in {time.time() - t0:.1f}s. Artifacts under {OUTDIR}")
    print("Next: write docs/gate3-reports/05-donchian.md from these CSVs.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="smaller sweep for iteration")
    args = parser.parse_args()
    main(quick=args.quick)
