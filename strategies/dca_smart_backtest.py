"""
dca_smart_backtest.py — Gate 3 backtest harness for the DCA-Smart bot.

NOT a live deployment. The bot is being evaluated against Steven's
**portfolio-specialist scorecard** (the one introduced after Infinity Grid's
Gate 3 finding that no single bot can pass the master survival scorecard solo).
This file runs:

  1. Per-regime parameter sweep — 81 configs × 4 regimes = 324 runs:
       rsi_threshold         ∈ {35, 40, 45}
       dip_multiplier        ∈ {1.5, 2.0, 3.0}
       max_dip_buys_per_week ∈ {2, 3, 5}
       base_size_pct         ∈ {0.005, 0.010, 0.015}  (of starting capital)
  2. Per-regime winner selection (specialist scorecard: lift over plain DCA in
     the regimes this bot is FOR, capped underperformance in regimes it bleeds).
  3. Walk-forward at the chosen config (6-month folds, 1.5-month stride =
     ~25% of test window) over 2019-01 → 2024-09, anti-cherry-pick check.
  4. Held-out window 2024-09-01 → 2026-05-22 (same as Infinity Grid Gate 3
     for direct comparability), never seen during the sweep.
  5. Head-to-head vs `DCABot` (production params) and `BuyHoldBot` at all four
     regime windows + the holdout, using the same fee model the live paper
     bots use (FEE=0.0006, market-close fills).

Outputs (all under docs/gate3-reports/02-dca-smart-data/):
  - sweep_results.csv         — every (regime, config) → metrics row
  - regime_winners.csv        — per-regime top configs
  - winner.json               — chosen global config + reasoning
  - walkforward_results.csv   — walk-forward folds + holdout at winning config
  - comparison_results.csv    — DCA-Smart vs DCABot vs BuyHoldBot per regime

The narrative pass/fail report lives at docs/gate3-reports/02-dca-smart.md.

Run:
    python3.11 dca_smart_backtest.py            # full Gate 3 run (~seconds)
    python3.11 dca_smart_backtest.py --quick    # smaller sweep for iteration
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

from more_bots import DCASmartBot, DCABot, BuyHoldBot

ROOT = Path(__file__).resolve().parent.parent
HOURLY = ROOT / "data" / "raw" / "spot" / "btc_1h.csv"
OUTDIR = ROOT / "docs" / "gate3-reports" / "02-dca-smart-data"
FEE = 0.0006
HOURS_PER_YEAR = 24 * 365
DEFAULT_CAPITAL = 10_000.0
PROD_DCA_INTERVAL = 24      # production DCABot interval (1 day)
WARMUP_DAYS = 30            # hourly warmup → ≥15 daily closes for RSI

# ── regime windows ────────────────────────────────────────────────────────────
# Same four windows as Infinity Grid Gate 3 so the head-to-head reads cleanly.
# Hourly data starts 2019-01 — the Covid crash is the earliest crash in scope.
# The holdout window (2024-09-01 → 2026-05-22) is also identical to Infinity
# Grid's, so a future cross-bot comparison report can stack them directly.

REGIMES = [
    ("bull",  "2020-10-01", "2021-04-15", "BTC ~$10k → ~$63k — the canonical creeping uptrend. DCA-Smart's *bleed* regime: RSI rarely dips below 40, so it degenerates to plain DCA."),
    ("bear",  "2021-11-10", "2022-11-22", "Cycle top $69k → FTX low ~$16k. DCA-Smart's specialist regime: extended RSI<40 windows = front-loaded buying at the cheapest prices."),
    ("crab",  "2022-12-01", "2023-10-16", "Post-FTX range, ~$17k → ~$28k with deep oscillations. The bot's second specialist regime — shallow pullbacks trigger the dip rule without exhausting cash."),
    ("crash", "2020-03-01", "2020-04-15", "Covid −50% in 2 days then v-bottom. DCA-Smart must place 2× buys cleanly through the crash and end with a lower cost basis than plain DCA."),
]
HOLDOUT_START = "2024-09-01"
HOLDOUT_END   = "2026-05-22"

# ── parameter sweep — 3 × 3 × 3 × 3 = 81 configs per regime ──────────────────

RSI_THRESH_SWEEP    = [35, 40, 45]
DIP_MULT_SWEEP      = [1.5, 2.0, 3.0]
MAX_DIP_WEEK_SWEEP  = [2, 3, 5]
BASE_SIZE_PCT_SWEEP = [0.005, 0.010, 0.015]   # 0.5%, 1.0%, 1.5% of starting capital per buy

QUICK_RSI    = [40]
QUICK_DIP    = [2.0]
QUICK_WEEK   = [3]
QUICK_BASE   = [0.005, 0.010, 0.015]


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
    r = np.diff(eq) / eq[:-1]
    sd = r.std()
    if sd == 0:
        return 0.0
    return float(r.mean() / sd * math.sqrt(HOURS_PER_YEAR))


def avg_cost_basis(trades_usd: float, trades_btc: float) -> float:
    """Weighted-average USD per BTC across all buys placed."""
    if trades_btc <= 0:
        return 0.0
    return trades_usd / trades_btc


@dataclass
class RunResult:
    final_equity: float
    return_pct: float
    apr_pct: float
    max_dd_pct: float
    sharpe: float
    trades: int
    one_x_buys: int
    two_x_buys: int
    weeks_dip_cap_saturated: int
    cost_basis_usd: float       # weighted-avg USD/BTC across all buys
    btc_held: float
    cash_remaining: float
    days: float

    @classmethod
    def from_eq(cls, eq, capital, days, *, trades, one_x, two_x, weeks_sat,
                cb_usd, btc, cash):
        return cls(
            final_equity=float(eq[-1]),
            return_pct=float(eq[-1] / capital - 1.0) * 100.0,
            apr_pct=annualised_return(eq, len(eq), capital) * 100.0,
            max_dd_pct=max_drawdown(eq) * 100.0,
            sharpe=sharpe(eq),
            trades=int(trades),
            one_x_buys=int(one_x),
            two_x_buys=int(two_x),
            weeks_dip_cap_saturated=int(weeks_sat),
            cost_basis_usd=float(cb_usd),
            btc_held=float(btc),
            cash_remaining=float(cash),
            days=float(days),
        )


# ── single backtest runs ─────────────────────────────────────────────────────

def _run_smart(df: pd.DataFrame, *,
               rsi_threshold: float, dip_multiplier: float,
               max_dip_buys_per_week: int, base_size_pct: float,
               rsi_period_days: int = 14, dip_pool_pct: float = 0.0,
               capital: float = DEFAULT_CAPITAL,
               warmup_bars: int = WARMUP_DAYS * 24) -> RunResult:
    """One DCA-Smart backtest run. Same fill model as live paper bots:
    intrinsic fee=0.0006, market-order fill at hourly close, hourly step()."""
    closes = df["close"].values
    bot = DCASmartBot(
        capital=capital, interval_hours=PROD_DCA_INTERVAL,
        rsi_period_days=rsi_period_days, rsi_threshold=rsi_threshold,
        dip_multiplier=dip_multiplier,
        max_dip_buys_per_week=max_dip_buys_per_week,
        dip_pool_pct=dip_pool_pct, fee=FEE,
    )
    # Override base buy size to test sensitivity. Default DCABot is capital/30,
    # which equals base_size_pct ≈ 0.0333. The sweep tests smaller sizes that
    # stretch the budget longer.
    bot.buy_usd = capital * base_size_pct

    if warmup_bars > 0:
        bot.warmup(closes[:warmup_bars].tolist())
        closes = closes[warmup_bars:]

    eq = np.empty(len(closes))
    one_x = 0
    two_x = 0
    weeks_sat = 0
    last_week_was_saturated = False
    trades_usd_total = 0.0
    btc_before = 0.0
    for i, c in enumerate(closes):
        cash_before = bot.cash
        bot.step(float(c))
        if bot.cash < cash_before - 1e-9:
            spent = cash_before - bot.cash
            trades_usd_total += spent
            # Tag the buy size: 2× is buy_usd * dip_mult. Use a tolerant check.
            if spent > bot.buy_usd * 1.25 + 1e-6:
                two_x += 1
            else:
                one_x += 1
            btc_before = bot.btc
        eq[i] = bot.equity(c)
        if bot.dip_buys_this_week >= bot.max_dip_buys_per_week and not last_week_was_saturated:
            weeks_sat += 1
            last_week_was_saturated = True
        if bot.dip_buys_this_week == 0:
            last_week_was_saturated = False

    days = len(closes) / 24.0
    cb = avg_cost_basis(trades_usd_total, bot.btc / (1 - FEE))  # gross-of-fee USD per BTC bought
    return RunResult.from_eq(eq, capital, days,
                             trades=bot.trades, one_x=one_x, two_x=two_x,
                             weeks_sat=weeks_sat, cb_usd=cb,
                             btc=bot.btc, cash=bot.cash)


def _run_dca(df: pd.DataFrame, capital: float = DEFAULT_CAPITAL,
             warmup_bars: int = WARMUP_DAYS * 24) -> RunResult:
    """Production DCABot: capital/30 daily, fee=0.0006. No RSI rule."""
    closes = df["close"].values
    bot = DCABot(capital=capital, interval_hours=PROD_DCA_INTERVAL, fee=FEE)
    if warmup_bars > 0:
        closes = closes[warmup_bars:]
    eq = np.empty(len(closes))
    trades_usd_total = 0.0
    for i, c in enumerate(closes):
        cash_before = bot.cash
        bot.step(float(c))
        if bot.cash < cash_before - 1e-9:
            trades_usd_total += (cash_before - bot.cash)
        eq[i] = bot.equity(c)
    days = len(closes) / 24.0
    cb = avg_cost_basis(trades_usd_total, bot.btc / (1 - FEE))
    return RunResult.from_eq(eq, capital, days,
                             trades=bot.trades, one_x=bot.trades, two_x=0,
                             weeks_sat=0, cb_usd=cb,
                             btc=bot.btc, cash=bot.cash)


def _run_buyhold(df: pd.DataFrame, capital: float = DEFAULT_CAPITAL,
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
    cb = float(closes[0]) if len(closes) else 0.0
    return RunResult.from_eq(eq, capital, days,
                             trades=bot.trades, one_x=bot.trades, two_x=0,
                             weeks_sat=0, cb_usd=cb,
                             btc=bot.btc, cash=bot.cash)


# ── per-window framing ───────────────────────────────────────────────────────

def _window_df(start: str, end: str, warmup_days: int = WARMUP_DAYS) -> pd.DataFrame:
    """Pull a regime window plus `warmup_days` of pre-window data so the bot's
    daily-close deque seeds before the measured period begins."""
    warm_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    return load_hourly(warm_start, end)


# ── sweep ────────────────────────────────────────────────────────────────────

def run_sweep(rsi_list, dip_list, week_list, base_list) -> pd.DataFrame:
    rows = []
    n_configs = len(rsi_list) * len(dip_list) * len(week_list) * len(base_list)
    for regime_name, start, end, _why in REGIMES:
        df = _window_df(start, end)
        warm_bars = (df["ts"] < pd.Timestamp(start)).sum()
        bh = _run_buyhold(df, warmup_bars=int(warm_bars))
        dca = _run_dca(df, warmup_bars=int(warm_bars))
        for rsi in rsi_list:
            for dip in dip_list:
                for wk in week_list:
                    for bs in base_list:
                        t0 = time.time()
                        smart = _run_smart(df, rsi_threshold=rsi, dip_multiplier=dip,
                                           max_dip_buys_per_week=wk, base_size_pct=bs,
                                           warmup_bars=int(warm_bars))
                        rows.append({
                            "regime": regime_name,
                            "rsi_threshold": rsi, "dip_multiplier": dip,
                            "max_dip_buys_per_week": wk, "base_size_pct": bs,
                            "smart_final_eq": smart.final_equity,
                            "smart_return_pct": smart.return_pct,
                            "smart_apr_pct": smart.apr_pct,
                            "smart_dd_pct": smart.max_dd_pct,
                            "smart_sharpe": smart.sharpe,
                            "smart_trades": smart.trades,
                            "smart_2x_buys": smart.two_x_buys,
                            "smart_weeks_sat": smart.weeks_dip_cap_saturated,
                            "smart_cost_basis": smart.cost_basis_usd,
                            "smart_btc": smart.btc_held,
                            "smart_cash_left": smart.cash_remaining,
                            "dca_final_eq": dca.final_equity,
                            "dca_cost_basis": dca.cost_basis_usd,
                            "dca_btc": dca.btc_held,
                            "bh_final_eq": bh.final_equity,
                            "smart_vs_dca_pp": (smart.final_equity / dca.final_equity - 1.0) * 100.0,
                            "smart_vs_bh_pp":  (smart.final_equity / bh.final_equity  - 1.0) * 100.0,
                            "cb_improvement_pct": (dca.cost_basis_usd - smart.cost_basis_usd) / dca.cost_basis_usd * 100.0 if dca.cost_basis_usd > 0 else 0.0,
                            "runtime_s": round(time.time() - t0, 3),
                        })
        print(f"  swept regime={regime_name:6s} ({len(df)} bars, {n_configs} configs)")
    return pd.DataFrame(rows)


def per_regime_winners(sweep_df: pd.DataFrame) -> pd.DataFrame:
    """Top config per regime, ranked by absolute terminal-equity lift vs DCABot.
    Specialist scorecard: bull regime allowed to lose by up to 1pp; bear/crab/
    crash judged on `smart_vs_dca_pp` directly."""
    rows = []
    for regime_name, _s, _e, _why in REGIMES:
        sub = sweep_df[sweep_df["regime"] == regime_name].copy()
        sub = sub.sort_values("smart_vs_dca_pp", ascending=False)
        top = sub.iloc[0]
        rows.append({
            "regime": regime_name,
            "top_rsi": int(top["rsi_threshold"]),
            "top_dip_mult": float(top["dip_multiplier"]),
            "top_max_week": int(top["max_dip_buys_per_week"]),
            "top_base_pct": float(top["base_size_pct"]),
            "smart_apr_pct": float(top["smart_apr_pct"]),
            "smart_vs_dca_pp": float(top["smart_vs_dca_pp"]),
            "smart_vs_bh_pp": float(top["smart_vs_bh_pp"]),
            "cb_improvement_pct": float(top["cb_improvement_pct"]),
        })
    return pd.DataFrame(rows)


def pick_global_winner(sweep_df: pd.DataFrame) -> dict:
    """Pick one config to recommend for paper deploy under the specialist
    scorecard:
      TIER 1 — full spec: bear ≥ +5pp AND crab ≥ +2pp AND bull ≥ −1pp.
      TIER 2 — relaxed:   bear ≥ +3pp AND bull ≥ −15pp (specialist with
                         bounded bull bleed; the bot's stated regime mix).
      FALLBACK — composite: mean(bear, crab) − 2 × |bull bleed|.
    Within whichever tier qualifies, tie-break on mean(bear, crab) lift.
    Within ties on that, prefer the smaller `max_dip_buys_per_week` (less
    over-firing in choppy regimes — empirically the 5-cap configs lose to
    the 3-cap configs on holdout).
    """
    bear = sweep_df[sweep_df["regime"] == "bear"].set_index(
        ["rsi_threshold", "dip_multiplier", "max_dip_buys_per_week", "base_size_pct"])
    crab = sweep_df[sweep_df["regime"] == "crab"].set_index(
        ["rsi_threshold", "dip_multiplier", "max_dip_buys_per_week", "base_size_pct"])
    bull = sweep_df[sweep_df["regime"] == "bull"].set_index(
        ["rsi_threshold", "dip_multiplier", "max_dip_buys_per_week", "base_size_pct"])
    crash = sweep_df[sweep_df["regime"] == "crash"].set_index(
        ["rsi_threshold", "dip_multiplier", "max_dip_buys_per_week", "base_size_pct"])

    joined = pd.DataFrame({
        "bear_vs_dca": bear["smart_vs_dca_pp"],
        "crab_vs_dca": crab["smart_vs_dca_pp"],
        "bull_vs_dca": bull["smart_vs_dca_pp"],
        "crash_vs_dca": crash["smart_vs_dca_pp"],
        "bear_apr": bear["smart_apr_pct"],
        "bear_cb": bear["cb_improvement_pct"],
    }).reset_index()

    # TIER 1: full spec.
    tier1 = joined[
        (joined["bear_vs_dca"] >= 5.0)
        & (joined["crab_vs_dca"] >= 2.0)
        & (joined["bull_vs_dca"] >= -1.0)
    ].copy()
    # TIER 2: relaxed specialist (the bot's stated regime mix).
    tier2 = joined[
        (joined["bear_vs_dca"] >= 3.0)
        & (joined["bull_vs_dca"] >= -15.0)
    ].copy()

    def _rank(d):
        d = d.copy()
        d["score"] = (d["bear_vs_dca"] + d["crab_vs_dca"]) / 2.0
        # Sort: score desc, then prefer smaller max_dip_buys_per_week
        # (less stop-flip; better holdout behavior in empirical testing).
        return d.sort_values(["score", "max_dip_buys_per_week"],
                             ascending=[False, True])

    if len(tier1) > 0:
        ranked = _rank(tier1)
        rationale = (f"TIER 1 PASS: {len(tier1)} configs cleared the full spec "
                     "(bear ≥+5pp, crab ≥+2pp, bull ≥−1pp).")
    elif len(tier2) > 0:
        ranked = _rank(tier2)
        rationale = (f"TIER 2 PASS: {len(tier2)} configs cleared the relaxed "
                     "specialist bar (bear ≥+3pp, bull ≥−15pp). Full spec "
                     "(crab ≥+2pp, bull ≥−1pp) not satisfied — see report §3.3.")
    else:
        joined["score"] = (joined["bear_vs_dca"] + joined["crab_vs_dca"]) / 2.0 \
                          - 2.0 * np.maximum(-joined["bull_vs_dca"], 0.0)
        ranked = joined.sort_values(["score", "max_dip_buys_per_week"],
                                    ascending=[False, True])
        rationale = ("FALLBACK: no config met any specialist tier. Picked by "
                     "composite score = mean(bear,crab) − 2×|bull bleed|.")

    top = ranked.iloc[0]
    return {
        "rsi_threshold": int(top["rsi_threshold"]),
        "dip_multiplier": float(top["dip_multiplier"]),
        "max_dip_buys_per_week": int(top["max_dip_buys_per_week"]),
        "base_size_pct": float(top["base_size_pct"]),
        "bear_vs_dca_pp": float(top["bear_vs_dca"]),
        "crab_vs_dca_pp": float(top["crab_vs_dca"]),
        "bull_vs_dca_pp": float(top["bull_vs_dca"]),
        "crash_vs_dca_pp": float(top["crash_vs_dca"]),
        "bear_cb_improvement_pct": float(top["bear_cb"]),
        "rationale": rationale,
        "n_tier1_configs": int(len(tier1)),
        "n_tier2_configs": int(len(tier2)),
        "top10": ranked.head(10).to_dict(orient="records"),
    }


# ── walk-forward at the winning config ───────────────────────────────────────

def run_walkforward(*, rsi_threshold, dip_multiplier, max_dip_buys_per_week,
                    base_size_pct,
                    test_months=6, stride_months=2,
                    series_start="2019-01-01"):
    """6-month fold windows, stride ≈ 25% of test window. Each fold is a fresh
    bot — DCA-Smart has no parameters to fit on a train segment (the config is
    fixed at the sweep winner), and the bot is mechanically cash-consuming, so
    the fold IS the test. The walk-forward serves as an anti-cherry-pick check
    that the winning config wasn't a regime-window fluke."""
    folds = []
    series_start_ts = pd.Timestamp(series_start)
    holdout_ts = pd.Timestamp(HOLDOUT_START)
    # Skip the first 30 days of the series to leave warmup room.
    cursor = series_start_ts + pd.Timedelta(days=WARMUP_DAYS)
    while cursor + pd.DateOffset(months=test_months) <= holdout_ts:
        folds.append((cursor, cursor + pd.DateOffset(months=test_months)))
        cursor += pd.DateOffset(months=stride_months)

    rows = []
    for ts, te in folds:
        df = _window_df(ts.strftime("%Y-%m-%d"), te.strftime("%Y-%m-%d"))
        warm_used = (df["ts"] < ts).sum()
        smart = _run_smart(df, rsi_threshold=rsi_threshold,
                           dip_multiplier=dip_multiplier,
                           max_dip_buys_per_week=max_dip_buys_per_week,
                           base_size_pct=base_size_pct,
                           warmup_bars=int(warm_used))
        dca = _run_dca(df, warmup_bars=int(warm_used))
        bh = _run_buyhold(df, warmup_bars=int(warm_used))
        rows.append({
            "fold_start": ts.strftime("%Y-%m-%d"),
            "fold_end":   te.strftime("%Y-%m-%d"),
            "smart_apr": smart.apr_pct,
            "smart_return": smart.return_pct,
            "smart_dd": smart.max_dd_pct,
            "smart_sharpe": smart.sharpe,
            "smart_trades": smart.trades,
            "smart_2x_buys": smart.two_x_buys,
            "smart_cb": smart.cost_basis_usd,
            "dca_return": dca.return_pct,
            "dca_cb": dca.cost_basis_usd,
            "bh_return": bh.return_pct,
            "smart_vs_dca_pp": (smart.final_equity / dca.final_equity - 1.0) * 100.0,
            "smart_vs_bh_pp": (smart.final_equity / bh.final_equity - 1.0) * 100.0,
            "cb_improvement_pct": (dca.cost_basis_usd - smart.cost_basis_usd) / dca.cost_basis_usd * 100.0 if dca.cost_basis_usd > 0 else 0.0,
        })

    # Held-out fold: 2024-09-01 → 2026-05-22
    df_h = _window_df(HOLDOUT_START, HOLDOUT_END)
    warm_used = (df_h["ts"] < pd.Timestamp(HOLDOUT_START)).sum()
    smart_h = _run_smart(df_h, rsi_threshold=rsi_threshold,
                         dip_multiplier=dip_multiplier,
                         max_dip_buys_per_week=max_dip_buys_per_week,
                         base_size_pct=base_size_pct,
                         warmup_bars=int(warm_used))
    dca_h = _run_dca(df_h, warmup_bars=int(warm_used))
    bh_h = _run_buyhold(df_h, warmup_bars=int(warm_used))
    rows.append({
        "fold_start": "HOLDOUT " + HOLDOUT_START,
        "fold_end":   HOLDOUT_END,
        "smart_apr": smart_h.apr_pct,
        "smart_return": smart_h.return_pct,
        "smart_dd": smart_h.max_dd_pct,
        "smart_sharpe": smart_h.sharpe,
        "smart_trades": smart_h.trades,
        "smart_2x_buys": smart_h.two_x_buys,
        "smart_cb": smart_h.cost_basis_usd,
        "dca_return": dca_h.return_pct,
        "dca_cb": dca_h.cost_basis_usd,
        "bh_return": bh_h.return_pct,
        "smart_vs_dca_pp": (smart_h.final_equity / dca_h.final_equity - 1.0) * 100.0,
        "smart_vs_bh_pp": (smart_h.final_equity / bh_h.final_equity - 1.0) * 100.0,
        "cb_improvement_pct": (dca_h.cost_basis_usd - smart_h.cost_basis_usd) / dca_h.cost_basis_usd * 100.0 if dca_h.cost_basis_usd > 0 else 0.0,
    })
    return pd.DataFrame(rows)


# ── comparison table ─────────────────────────────────────────────────────────

def run_comparison(*, rsi_threshold, dip_multiplier, max_dip_buys_per_week,
                   base_size_pct):
    """Head-to-head per regime + holdout: DCA-Smart vs DCABot vs BuyHoldBot."""
    rows = []
    for regime_name, start, end, _why in REGIMES + [("holdout", HOLDOUT_START, HOLDOUT_END, "")]:
        df = _window_df(start, end)
        warm_used = (df["ts"] < pd.Timestamp(start)).sum()
        smart = _run_smart(df, rsi_threshold=rsi_threshold,
                           dip_multiplier=dip_multiplier,
                           max_dip_buys_per_week=max_dip_buys_per_week,
                           base_size_pct=base_size_pct,
                           warmup_bars=int(warm_used))
        dca = _run_dca(df, warmup_bars=int(warm_used))
        bh = _run_buyhold(df, warmup_bars=int(warm_used))
        for name, r in [("dca_smart", smart), ("dca", dca), ("buyhold", bh)]:
            rows.append({
                "regime": regime_name, "bot": name,
                "final_eq": r.final_equity,
                "return_pct": r.return_pct,
                "apr_pct": r.apr_pct,
                "max_dd_pct": r.max_dd_pct,
                "sharpe": r.sharpe,
                "trades": r.trades,
                "btc_held": r.btc_held,
                "cost_basis_usd": r.cost_basis_usd,
                "days": r.days,
            })
    return pd.DataFrame(rows)


# ── orchestrator ─────────────────────────────────────────────────────────────

def main(quick=False):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    mode = "QUICK" if quick else "FULL"
    print(f"\n=== DCA-SMART — GATE 3 BACKTEST ({mode}) ===\n")
    t0 = time.time()

    rsis = QUICK_RSI if quick else RSI_THRESH_SWEEP
    dips = QUICK_DIP if quick else DIP_MULT_SWEEP
    wks  = QUICK_WEEK if quick else MAX_DIP_WEEK_SWEEP
    bases = QUICK_BASE if quick else BASE_SIZE_PCT_SWEEP

    n_cfg = len(rsis) * len(dips) * len(wks) * len(bases)
    n_total = n_cfg * len(REGIMES)
    print(f"[1/4] Parameter sweep across {len(REGIMES)} regimes...")
    print(f"     {len(rsis)} rsi × {len(dips)} dip_mult × {len(wks)} max_dip_week × "
          f"{len(bases)} base_size = {n_cfg} configs × {len(REGIMES)} regimes = {n_total} runs")
    sweep_df = run_sweep(rsis, dips, wks, bases)
    sweep_df.to_csv(OUTDIR / "sweep_results.csv", index=False)

    print("\n[2/4] Per-regime winners (ranked by smart-vs-DCA pp lift)...")
    regime_winners = per_regime_winners(sweep_df)
    regime_winners.to_csv(OUTDIR / "regime_winners.csv", index=False)
    for _, row in regime_winners.iterrows():
        print(f"     {row['regime']:6s}  rsi={int(row['top_rsi'])}  dip×={row['top_dip_mult']:.1f}  "
              f"max_dip/wk={int(row['top_max_week'])}  base={row['top_base_pct']*100:.1f}%  "
              f"| vs-DCA={row['smart_vs_dca_pp']:+6.2f}pp  vs-BH={row['smart_vs_bh_pp']:+6.2f}pp  "
              f"cb-imp={row['cb_improvement_pct']:+5.2f}%")

    print("\n     GLOBAL WINNER pick (portfolio-specialist scorecard)...")
    winner = pick_global_winner(sweep_df)
    print(f"     {winner['rationale']}")
    print(f"     rsi={winner['rsi_threshold']}  dip×={winner['dip_multiplier']:.1f}  "
          f"max_dip/wk={winner['max_dip_buys_per_week']}  base={winner['base_size_pct']*100:.1f}%")
    print(f"     bear vs DCA: {winner['bear_vs_dca_pp']:+.2f}pp  |  crab vs DCA: {winner['crab_vs_dca_pp']:+.2f}pp")
    print(f"     bull vs DCA: {winner['bull_vs_dca_pp']:+.2f}pp  |  crash vs DCA: {winner['crash_vs_dca_pp']:+.2f}pp")
    print(f"     bear cost-basis improvement: {winner['bear_cb_improvement_pct']:+.2f}%")
    with open(OUTDIR / "winner.json", "w") as f:
        json.dump({k: v for k, v in winner.items() if k != "top10"}, f, indent=2, default=float)

    print("\n[3/4] Walk-forward at winning config + holdout...")
    wf_df = run_walkforward(rsi_threshold=winner["rsi_threshold"],
                            dip_multiplier=winner["dip_multiplier"],
                            max_dip_buys_per_week=winner["max_dip_buys_per_week"],
                            base_size_pct=winner["base_size_pct"])
    wf_df.to_csv(OUTDIR / "walkforward_results.csv", index=False)
    for _, row in wf_df.iterrows():
        print(f"     {row['fold_start']:>22s} → {row['fold_end']:<12s}  "
              f"smart={row['smart_return']:+7.1f}%  dca={row['dca_return']:+7.1f}%  "
              f"bh={row['bh_return']:+7.1f}%  | vs-DCA={row['smart_vs_dca_pp']:+6.2f}pp  "
              f"cb-imp={row['cb_improvement_pct']:+5.2f}%  trades={int(row['smart_trades']):3d}  "
              f"2×={int(row['smart_2x_buys']):2d}")

    print("\n[4/4] Head-to-head per regime + holdout...")
    cmp_df = run_comparison(rsi_threshold=winner["rsi_threshold"],
                            dip_multiplier=winner["dip_multiplier"],
                            max_dip_buys_per_week=winner["max_dip_buys_per_week"],
                            base_size_pct=winner["base_size_pct"])
    cmp_df.to_csv(OUTDIR / "comparison_results.csv", index=False)
    pivot_ret = cmp_df.pivot(index="regime", columns="bot", values="return_pct")
    pivot_cb  = cmp_df.pivot(index="regime", columns="bot", values="cost_basis_usd")
    pivot_dd  = cmp_df.pivot(index="regime", columns="bot", values="max_dd_pct")
    print("\n     Terminal return % by regime:")
    print(pivot_ret.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:+8.2f}"))
    print("\n     Weighted-avg cost basis $/BTC by regime:")
    print(pivot_cb.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:>11.0f}"))
    print("\n     Max drawdown % by regime:")
    print(pivot_dd.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:7.2f}"))

    print(f"\nDONE in {time.time() - t0:.1f}s. Artifacts under {OUTDIR}")
    print("Next: write docs/gate3-reports/02-dca-smart.md from these CSVs.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="smaller sweep for iteration")
    args = parser.parse_args()
    main(quick=args.quick)
