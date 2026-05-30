"""
infinity_grid_backtest.py — Gate 3 backtest harness for the Infinity Grid bot.

NOT a live deployment. The bot is being evaluated against Steven's survival-first
scorecard before it can be considered for paper deploy (Gate 4). This file runs:

  1. Per-regime full-window backtest at all 48 sweep configs (192 runs):
       infinity_tail_pct  ∈ {15, 30, 50, 70}   (Steven Q3)
       spacing_pct        ∈ {1.0, 1.5, 2.0, 3.0}
       trend_ma_days      ∈ {20, 30, 45}
  2. Identification of the dominant config per regime and globally.
  3. Walk-forward across the full series at the winning config (anti-survivor-
     bias check — no re-tuning across folds).
  4. Capacity sweep at $10k / $50k / $250k notional.
  5. Head-to-head vs GridBot(Balanced) and BuyHoldBot at the four regimes
     using the same fee model + the same hourly close-fill assumption the live
     paper bots use (FEE=0.0006 baked into the bot, on_close(close, low=) drives
     all fills — see grid_farm.py step_all() and grid_bot.py:on_close).

Outputs (all under docs/gate3-reports/01-infinity-grid-data/):
  - sweep_results.csv             — every (regime, config) → metrics
  - regime_summary.csv            — per-regime winners
  - walkforward_results.csv       — winning config across walk-forward folds
  - comparison_results.csv        — Infinity vs Balanced vs BuyHold per regime
  - capacity_results.csv          — winning config at three notional sizes

The narrative pass/fail Gate 3 report is at docs/gate3-reports/01-infinity-grid.md.

Run:
    python3.11 infinity_grid_backtest.py            # full Gate 3 run (~minutes)
    python3.11 infinity_grid_backtest.py --quick    # smaller sweep for iteration
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

from grid_bot import GridBot
from more_bots import BuyHoldBot
from infinity_grid_bot import InfinityGridBot

ROOT = Path(__file__).resolve().parent.parent
HOURLY = ROOT / "data" / "raw" / "spot" / "btc_1h.csv"
OUTDIR = ROOT / "docs" / "gate3-reports" / "01-infinity-grid-data"
FEE = 0.0006
HOURS_PER_YEAR = 24 * 365
DEFAULT_CAPITAL = 10_000.0

# ── regime windows ────────────────────────────────────────────────────────────
# Picked from the public BTC chart history. Hourly data starts 2019-01-01 so the
# 2017-18 cycle is not available; the 2020 COVID crash is the earliest dislocation
# in scope. Each window is named after the dominant regime; reported numbers go
# only as far back as 2019-01.

REGIMES = [
    # name,        start,         end,           why
    ("bull",       "2020-10-01",  "2021-04-15",  "BTC ~$10k → ~$63k, the canonical creeping uptrend the strategy is designed to harvest"),
    ("bear",       "2021-11-10",  "2022-11-22",  "cycle top $69k → FTX low ~$16k — the trend-stop's reason for existing"),
    ("crab",       "2022-12-01",  "2023-10-16",  "post-FTX range, ~$17k → ~$28k with deep oscillations — the whipsaw stress test"),
    ("crash",      "2020-03-01",  "2020-04-15",  "Covid -50% in 2 days then v-bottom — the flash-crash failure mode"),
]

# ── parameter sweep (Gate 3 v2 — corners pushed outward per Steven 2026-05-31) ──

TAIL_PCT_SWEEP    = [0.05, 0.10, 0.15, 0.20]      # v2: was {0.15-0.70}, data said lower-tail = more survival
SPACING_SWEEP     = [0.030, 0.050, 0.070]         # v2: was {0.010-0.030}, winning v1 config sat at 0.030 (corner)
MA_DAYS_SWEEP     = [20, 30, 45]                  # unchanged from v1
# v2: 4 × 3 × 3 = 36 configs × 4 regimes = 144 runs (v1 was 192).

QUICK_TAIL_SWEEP    = [0.05, 0.10, 0.15, 0.20]
QUICK_SPACING_SWEEP = [0.030, 0.050]
QUICK_MA_SWEEP      = [30]


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


def sortino(eq: np.ndarray) -> float:
    if len(eq) < 2:
        return 0.0
    r = np.diff(eq) / eq[:-1]
    downside = r[r < 0]
    if len(downside) < 1:
        return 0.0
    sd_down = downside.std()
    if sd_down == 0:
        return 0.0
    return float(r.mean() / sd_down * math.sqrt(HOURS_PER_YEAR))


@dataclass
class RunResult:
    final_equity: float
    return_pct: float
    apr_pct: float
    max_dd_pct: float
    sharpe: float
    sortino: float
    trades: int
    stop_events: int        # slow MA-hysteresis trigger fires
    fast_stop_events: int   # v2 single-bar > 3×ATR fast trigger fires
    halt_event: bool
    end_state: str
    tail_btc: float
    days: float

    @classmethod
    def from_equity(cls, eq, capital, days, trades, stops, fast_stops, halted, end_state, tail):
        return cls(
            final_equity=float(eq[-1]),
            return_pct=float(eq[-1] / capital - 1.0) * 100.0,
            apr_pct=annualised_return(eq, len(eq), capital) * 100.0,
            max_dd_pct=max_drawdown(eq) * 100.0,
            sharpe=sharpe(eq),
            sortino=sortino(eq),
            trades=int(trades),
            stop_events=int(stops),
            fast_stop_events=int(fast_stops),
            halt_event=bool(halted),
            end_state=str(end_state),
            tail_btc=float(tail),
            days=float(days),
        )


# ── single backtest ──────────────────────────────────────────────────────────

def run_infinity(df: pd.DataFrame, *,
                 tail_pct: float, spacing: float, ma_days: int,
                 capital: float = DEFAULT_CAPITAL,
                 warmup_bars: int = 0,
                 max_drawdown_halt_pct: float = 0.25,
                 fast_stop_atr_mult=None) -> RunResult:
    """One backtest run. Same fill model as grid_farm.py: hourly close-fed
    on_close(close, low=low). FEE is intrinsic to the bot (0.0006).

    `max_drawdown_halt_pct=1.0` effectively disables the drawdown halt at
    leverage=1 (the bot's equity cannot drop 100% from peak without holdings
    going to zero, which doesn't happen with unleveraged spot).
    """
    closes = df["close"].values
    lows = df["low"].values
    bot = InfinityGridBot(
        spacing=spacing, max_lots=20, ma_hours=ma_days * 24,
        infinity_tail_pct=tail_pct, capital=capital, fee=FEE,
        max_drawdown_halt_pct=max_drawdown_halt_pct,
        fast_stop_atr_mult=fast_stop_atr_mult,
    )
    if warmup_bars > 0:
        bot.warmup(closes[:warmup_bars].tolist())
        closes = closes[warmup_bars:]
        lows = lows[warmup_bars:]

    eq = np.empty(len(closes))
    stops = 0
    fast_stops = 0
    halted = False
    for i, (c, lo) in enumerate(zip(closes, lows)):
        events = bot.on_close(c, low=lo)
        for tag, _, _ in events:
            if tag == "SELL_STOP":
                stops += 1
            elif tag == "FAST_STOP":
                fast_stops += 1
            elif tag == "HALTED_DRAWDOWN":
                halted = True
        eq[i] = bot.equity(c)

    days = len(closes) / 24.0
    return RunResult.from_equity(eq, capital, days, bot.trades, stops, fast_stops,
                                 halted, bot.state, bot.infinity_tail_qty)


def run_balanced(df: pd.DataFrame, capital: float = DEFAULT_CAPITAL,
                 warmup_bars: int = 0) -> RunResult:
    """Production Balanced grid: 5% / 20 lots / 15-day trend-stop / no leverage."""
    closes = df["close"].values
    lows = df["low"].values
    bot = GridBot(spacing=0.05, max_lots=20, ma_hours=360,
                  capital=capital, fee=FEE, leverage=1.0)
    if warmup_bars > 0:
        bot.warmup(closes[:warmup_bars].tolist())
        closes = closes[warmup_bars:]
        lows = lows[warmup_bars:]
    eq = np.empty(len(closes))
    stops = 0
    for i, (c, lo) in enumerate(zip(closes, lows)):
        events = bot.on_close(c, low=lo)
        for tag, _, _ in events:
            if tag == "SELL_STOP":
                stops += 1
        eq[i] = bot.equity(c)
    days = len(closes) / 24.0
    return RunResult.from_equity(eq, capital, days, bot.trades, stops, 0,
                                 False, "RUNNING", 0.0)


def run_buyhold(df: pd.DataFrame, capital: float = DEFAULT_CAPITAL,
                warmup_bars: int = 0) -> RunResult:
    closes = df["close"].values
    if warmup_bars > 0:
        closes = closes[warmup_bars:]
    bot = BuyHoldBot(capital=capital, fee=FEE)
    eq = np.empty(len(closes))
    for i, c in enumerate(closes):
        bot.step(c)
        eq[i] = bot.equity(c)
    days = len(closes) / 24.0
    return RunResult.from_equity(eq, capital, days, bot.trades, 0, 0,
                                 False, "HOLD", bot.btc)


# ── headline sweeps ──────────────────────────────────────────────────────────

def regime_window(regime_name: str) -> tuple[str, str]:
    for name, s, e, _ in REGIMES:
        if name == regime_name:
            return s, e
    raise KeyError(regime_name)


def run_sweep(tail_sweep, spacing_sweep, ma_sweep, warmup_bars: int = 720):
    """Per-regime full-window sweep. Returns a long-form DataFrame."""
    rows = []
    for regime_name, start, end, _why in REGIMES:
        df_full = load_hourly(start, end)
        # Warmup: feed the bot some pre-window history so MA + ATR are real
        # numbers from bar 1 of the regime. 720h = 30d, covers the longest MA we sweep.
        df_warm = load_hourly(
            (pd.Timestamp(start) - pd.Timedelta(hours=warmup_bars)).strftime("%Y-%m-%d %H:%M:%S"),
            end,
        )
        warm_used = min(warmup_bars, len(df_warm) - len(df_full))
        for tail in tail_sweep:
            for sp in spacing_sweep:
                for ma in ma_sweep:
                    t0 = time.time()
                    res = run_infinity(df_warm, tail_pct=tail, spacing=sp, ma_days=ma,
                                       warmup_bars=warm_used)
                    rows.append({
                        "regime": regime_name,
                        "tail_pct": tail, "spacing_pct": sp, "ma_days": ma,
                        "final_equity": res.final_equity,
                        "return_pct": res.return_pct,
                        "apr_pct": res.apr_pct,
                        "max_dd_pct": res.max_dd_pct,
                        "sharpe": res.sharpe,
                        "sortino": res.sortino,
                        "trades": res.trades,
                        "stop_events": res.stop_events,
                        "fast_stop_events": res.fast_stop_events,
                        "halted": res.halt_event,
                        "end_state": res.end_state,
                        "tail_btc": res.tail_btc,
                        "days": res.days,
                        "runtime_s": round(time.time() - t0, 2),
                    })
        print(f"  swept regime={regime_name} ({len(df_full)} bars)")
    return pd.DataFrame(rows)


def pick_winner(sweep_df: pd.DataFrame) -> dict:
    """Per the survival-first scorecard: rank configs by APR / (MaxDD + 0.5pp)
    averaged across regimes, BUT eliminate configs that halted in any regime."""
    agg = (sweep_df
           .assign(score=lambda d: d["apr_pct"] / (d["max_dd_pct"] + 0.5))
           .groupby(["tail_pct", "spacing_pct", "ma_days"])
           .agg(score=("score", "mean"),
                worst_dd=("max_dd_pct", "max"),
                mean_apr=("apr_pct", "mean"),
                mean_sharpe=("sharpe", "mean"),
                halted_any=("halted", "any"))
           .reset_index())
    # Strict survivors only
    survivors = agg[~agg["halted_any"]].copy()
    if len(survivors) == 0:
        # Fallback: nothing survived → ranked by score even with halts
        survivors = agg.copy()
    survivors = survivors.sort_values("score", ascending=False)
    top = survivors.iloc[0].to_dict()
    top["all_ranked"] = survivors.head(10).to_dict(orient="records")
    return top


def per_regime_winners(sweep_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for regime_name in [r[0] for r in REGIMES]:
        sub = sweep_df[sweep_df["regime"] == regime_name].copy()
        sub["score"] = sub["apr_pct"] / (sub["max_dd_pct"] + 0.5)
        sub = sub.sort_values("score", ascending=False)
        rows.append({
            "regime": regime_name,
            "top_tail": sub["tail_pct"].iloc[0],
            "top_spacing": sub["spacing_pct"].iloc[0],
            "top_ma": int(sub["ma_days"].iloc[0]),
            "top_apr_pct": sub["apr_pct"].iloc[0],
            "top_dd_pct": sub["max_dd_pct"].iloc[0],
            "top_sharpe": sub["sharpe"].iloc[0],
        })
    return pd.DataFrame(rows)


# ── walk-forward at the winning config ───────────────────────────────────────

def run_walkforward(*, tail_pct, spacing, ma_days,
                    test_months=6, stride_months=3,
                    train_warmup_days=365,
                    holdout_start="2024-09-01"):
    """At the winning config, walk forward across the full series with
    fixed-size test windows (spec §7.4: 6 months test, 3 months stride).
    No tuning across folds — this is the anti-survivor-bias check that the
    chosen config wasn't a 2021 fluke."""
    folds = []
    start_test = pd.Timestamp("2019-01-01") + pd.Timedelta(days=train_warmup_days)
    holdout = pd.Timestamp(holdout_start)
    while start_test + pd.DateOffset(months=test_months) <= holdout:
        test_start = start_test
        test_end = start_test + pd.DateOffset(months=test_months)
        folds.append((test_start, test_end))
        start_test += pd.DateOffset(months=stride_months)

    rows = []
    for ts, te in folds:
        # Feed 30 days of warmup before the test window
        warm_start = ts - pd.Timedelta(days=30)
        df = load_hourly(warm_start.strftime("%Y-%m-%d"), te.strftime("%Y-%m-%d"))
        warm_used = (df["ts"] < ts).sum()
        res = run_infinity(df, tail_pct=tail_pct, spacing=spacing, ma_days=ma_days,
                           warmup_bars=int(warm_used))
        rows.append({
            "fold_start": ts.strftime("%Y-%m-%d"),
            "fold_end":   te.strftime("%Y-%m-%d"),
            "apr_pct":    res.apr_pct,
            "return_pct": res.return_pct,
            "max_dd_pct": res.max_dd_pct,
            "sharpe":     res.sharpe,
            "trades":     res.trades,
            "stop_events":res.stop_events,
            "fast_stop_events": res.fast_stop_events,
            "halted":     res.halt_event,
            "end_state":  res.end_state,
        })

    # Held-out test: 2024-09-01 → end of data, never touched during sweep
    df_h = load_hourly("2024-08-01", "2026-05-22")
    warm_used = (df_h["ts"] < holdout).sum()
    res_h = run_infinity(df_h, tail_pct=tail_pct, spacing=spacing, ma_days=ma_days,
                         warmup_bars=int(warm_used))
    rows.append({
        "fold_start": "HOLDOUT " + holdout_start,
        "fold_end":   "2026-05-22",
        "apr_pct":    res_h.apr_pct,
        "return_pct": res_h.return_pct,
        "max_dd_pct": res_h.max_dd_pct,
        "sharpe":     res_h.sharpe,
        "trades":     res_h.trades,
        "stop_events":res_h.stop_events,
        "fast_stop_events": res_h.fast_stop_events,
        "halted":     res_h.halt_event,
        "end_state":  res_h.end_state,
    })
    return pd.DataFrame(rows)


# ── comparison + capacity ────────────────────────────────────────────────────

def run_comparison(*, tail_pct, spacing, ma_days, warmup_bars=720):
    rows = []
    for regime_name, start, end, _why in REGIMES:
        warm_start = (pd.Timestamp(start) - pd.Timedelta(hours=warmup_bars)).strftime("%Y-%m-%d %H:%M:%S")
        df = load_hourly(warm_start, end)
        warm_used = (df["ts"] < pd.Timestamp(start)).sum()
        inf_res = run_infinity(df, tail_pct=tail_pct, spacing=spacing, ma_days=ma_days,
                               warmup_bars=int(warm_used))
        bal_res = run_balanced(df, warmup_bars=int(warm_used))
        bh_res  = run_buyhold (df, warmup_bars=int(warm_used))
        for name, r in [("infinity", inf_res), ("balanced", bal_res), ("buyhold", bh_res)]:
            rows.append({
                "regime": regime_name, "bot": name,
                "apr_pct": r.apr_pct, "return_pct": r.return_pct,
                "max_dd_pct": r.max_dd_pct, "sharpe": r.sharpe,
                "sortino": r.sortino, "trades": r.trades,
                "halted": r.halt_event,
            })
    return pd.DataFrame(rows)


def run_capacity(*, tail_pct, spacing, ma_days, warmup_bars=720):
    """Re-run the winning config across the full series at three notional sizes.
    The grid is equal-weight buy_usd = capital / max_lots, so per-fill clip
    scales linearly. BTC depth absorbs $50k clips without observable slippage
    per the spec; the test is whether ANY edge degradation shows up in metrics."""
    rows = []
    df_full = load_hourly("2019-01-01", "2026-05-22")
    warm_used = 720
    for capital in (10_000, 50_000, 250_000):
        res = run_infinity(df_full, tail_pct=tail_pct, spacing=spacing, ma_days=ma_days,
                           capital=capital, warmup_bars=warm_used)
        rows.append({
            "capital": capital,
            "buy_usd_clip": capital / 20.0,
            "apr_pct": res.apr_pct,
            "return_pct": res.return_pct,
            "max_dd_pct": res.max_dd_pct,
            "sharpe": res.sharpe,
            "trades": res.trades,
            "halted": res.halt_event,
        })
    return pd.DataFrame(rows)


# ── halt-only sweep (v3, 2026-05-31) ─────────────────────────────────────────
# Lock the bot's mechanism + config at v1's bull-capture winner and sweep ONLY
# the drawdown-halt threshold. v1 winner config: tail=15%, spacing=3%, MA=45d.
# Hysteresis 6/12 (v1), no fast trigger (v2 add was rejected). The variant is
# being framed as a specialist bull-leg-capture bot, not a master-scorecard
# survivor — the halt setting trades the "hard stop wipeout protection" against
# the "stop-flip-stop bleed" the bot can't survive in v1 form.

V3_TAIL_PCT  = 0.15
V3_SPACING   = 0.030
V3_MA_DAYS   = 45
V3_HALT_SWEEP = [
    ("halt-25",  0.25),
    ("halt-35",  0.35),
    ("halt-50",  0.50),
    ("halt-75",  0.75),
    ("no-halt",  1.00),   # leverage=1 + spot → equity can't drop 100% → halt never fires
]


def run_halt_sweep(warmup_bars: int = 720):
    """For each halt setting, run the 4 regime windows + the full walk-forward
    + the 2024-09 → 2026-05 holdout. Comparison vs Balanced/BuyHold is the same
    across halt settings (computed once at the bottom)."""
    regime_rows = []
    wf_rows = []
    for halt_name, halt_pct in V3_HALT_SWEEP:
        print(f"  halt {halt_name} (max_dd_halt={halt_pct})...")
        # per regime
        for regime_name, start, end, _why in REGIMES:
            warm_start = (pd.Timestamp(start) - pd.Timedelta(hours=warmup_bars)).strftime("%Y-%m-%d %H:%M:%S")
            df = load_hourly(warm_start, end)
            warm_used = (df["ts"] < pd.Timestamp(start)).sum()
            res = run_infinity(df, tail_pct=V3_TAIL_PCT, spacing=V3_SPACING, ma_days=V3_MA_DAYS,
                               warmup_bars=int(warm_used),
                               max_drawdown_halt_pct=halt_pct)
            regime_rows.append({
                "halt": halt_name, "halt_pct": halt_pct,
                "regime": regime_name,
                "apr_pct": res.apr_pct, "return_pct": res.return_pct,
                "max_dd_pct": res.max_dd_pct, "sharpe": res.sharpe,
                "trades": res.trades, "stop_events": res.stop_events,
                "halted": res.halt_event, "end_state": res.end_state,
                "tail_btc": res.tail_btc,
            })
        # walk-forward folds (the run_walkforward function already plumbs through to run_infinity,
        # but we need to thread the halt setting through — quickest is to call it inline here).
        from pandas.tseries.offsets import DateOffset
        folds = []
        start_test = pd.Timestamp("2019-01-01") + pd.Timedelta(days=365)
        holdout = pd.Timestamp("2024-09-01")
        while start_test + pd.DateOffset(months=6) <= holdout:
            folds.append((start_test, start_test + pd.DateOffset(months=6)))
            start_test += pd.DateOffset(months=3)
        for ts, te in folds:
            warm_start = ts - pd.Timedelta(days=30)
            df = load_hourly(warm_start.strftime("%Y-%m-%d"), te.strftime("%Y-%m-%d"))
            warm_used = (df["ts"] < ts).sum()
            res = run_infinity(df, tail_pct=V3_TAIL_PCT, spacing=V3_SPACING, ma_days=V3_MA_DAYS,
                               warmup_bars=int(warm_used),
                               max_drawdown_halt_pct=halt_pct)
            wf_rows.append({
                "halt": halt_name, "halt_pct": halt_pct,
                "fold_start": ts.strftime("%Y-%m-%d"),
                "fold_end":   te.strftime("%Y-%m-%d"),
                "apr_pct":    res.apr_pct, "return_pct": res.return_pct,
                "max_dd_pct": res.max_dd_pct, "sharpe": res.sharpe,
                "trades":     res.trades, "stop_events": res.stop_events,
                "halted":     res.halt_event, "end_state": res.end_state,
            })
        # holdout
        df_h = load_hourly("2024-08-01", "2026-05-22")
        warm_used = (df_h["ts"] < holdout).sum()
        res_h = run_infinity(df_h, tail_pct=V3_TAIL_PCT, spacing=V3_SPACING, ma_days=V3_MA_DAYS,
                             warmup_bars=int(warm_used),
                             max_drawdown_halt_pct=halt_pct)
        wf_rows.append({
            "halt": halt_name, "halt_pct": halt_pct,
            "fold_start": "HOLDOUT 2024-09-01",
            "fold_end":   "2026-05-22",
            "apr_pct":    res_h.apr_pct, "return_pct": res_h.return_pct,
            "max_dd_pct": res_h.max_dd_pct, "sharpe": res_h.sharpe,
            "trades":     res_h.trades, "stop_events": res_h.stop_events,
            "halted":     res_h.halt_event, "end_state": res_h.end_state,
        })
    return pd.DataFrame(regime_rows), pd.DataFrame(wf_rows)


def halt_sweep_main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    print("\n=== INFINITY GRID — GATE 3 v3 (DRAWDOWN-HALT SWEEP) ===")
    print(f"Locked: tail={V3_TAIL_PCT:.0%}, spacing={V3_SPACING*100:.1f}%, MA={V3_MA_DAYS}d, "
          f"hysteresis 6/12, no fast trigger.")
    print(f"Sweeping max_drawdown_halt_pct ∈ {[h[1] for h in V3_HALT_SWEEP]}.\n")
    t0 = time.time()
    regime_df, wf_df = run_halt_sweep()
    regime_df.to_csv(OUTDIR / "v3_halt_regime_results.csv", index=False)
    wf_df.to_csv(OUTDIR / "v3_halt_walkforward_results.csv", index=False)

    # comparison baselines — computed once at the locked config (halt doesn't affect them)
    print("\n  Comparison: Balanced + BuyHold per regime (constant across halt settings)...")
    cmp_df = run_comparison(tail_pct=V3_TAIL_PCT, spacing=V3_SPACING, ma_days=V3_MA_DAYS)
    cmp_df.to_csv(OUTDIR / "v3_halt_comparison_results.csv", index=False)

    # print compact summary
    print("\n=== PER-REGIME (APR % | DD %, halt-fire count after each row) ===")
    apr_p = regime_df.pivot(index="halt", columns="regime", values="apr_pct").reindex([h[0] for h in V3_HALT_SWEEP])
    dd_p  = regime_df.pivot(index="halt", columns="regime", values="max_dd_pct").reindex([h[0] for h in V3_HALT_SWEEP])
    halt_cnt = regime_df[regime_df["halted"]].groupby("halt").size().reindex([h[0] for h in V3_HALT_SWEEP]).fillna(0).astype(int)
    print("\n-- APR % --")
    print(apr_p.round(1).to_string())
    print("\n-- MaxDD % --")
    print(dd_p.round(1).to_string())
    print("\n-- Halt fires (across 4 regimes) --")
    print(halt_cnt.to_string())

    print("\n=== WALK-FORWARD SUMMARY ===")
    tuning = wf_df[~wf_df["fold_start"].str.contains("HOLDOUT")]
    holdout = wf_df[wf_df["fold_start"].str.contains("HOLDOUT")]
    summary = tuning.groupby("halt").agg(
        wf_mean_apr=("apr_pct", "mean"),
        wf_median_apr=("apr_pct", "median"),
        wf_worst_dd=("max_dd_pct", "max"),
        wf_mean_sharpe=("sharpe", "mean"),
        wf_halts=("halted", "sum"),
    ).reindex([h[0] for h in V3_HALT_SWEEP])
    hold = holdout.set_index("halt")[["apr_pct", "max_dd_pct", "sharpe", "halted"]].reindex([h[0] for h in V3_HALT_SWEEP])
    hold.columns = ["holdout_apr", "holdout_dd", "holdout_sharpe", "holdout_halted"]
    out = pd.concat([summary, hold], axis=1)
    print(out.round(2).to_string())

    print("\n=== COMPARISON BASELINES (constant across halt settings) ===")
    cmp_pivot = cmp_df.pivot(index="regime", columns="bot", values="apr_pct").reindex([r[0] for r in REGIMES])
    print("APR % by bot and regime:")
    print(cmp_pivot.to_string(float_format=lambda x: f"{x:+7.1f}"))

    print(f"\nDONE in {time.time() - t0:.1f}s. Artifacts under {OUTDIR}/v3_halt_*.csv")


# ── orchestrator ─────────────────────────────────────────────────────────────

def main(quick=False):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    print(f"\n=== INFINITY GRID — GATE 3 BACKTEST ({'QUICK' if quick else 'FULL'}) ===\n")
    t0 = time.time()

    print("[1/5] Parameter sweep across 4 regimes...")
    tails = QUICK_TAIL_SWEEP if quick else TAIL_PCT_SWEEP
    spacings = QUICK_SPACING_SWEEP if quick else SPACING_SWEEP
    mas = QUICK_MA_SWEEP if quick else MA_DAYS_SWEEP
    n = len(tails) * len(spacings) * len(mas) * len(REGIMES)
    print(f"     {len(tails)} tail × {len(spacings)} spacing × {len(mas)} MA × {len(REGIMES)} regimes = {n} runs")
    sweep_df = run_sweep(tails, spacings, mas)
    sweep_df.to_csv(OUTDIR / "sweep_results.csv", index=False)

    print("\n[2/5] Picking winner (survival-first composite)...")
    winner = pick_winner(sweep_df)
    print(f"     WINNER  tail={winner['tail_pct']:.0%}  spacing={winner['spacing_pct']*100:.1f}%  "
          f"MA={int(winner['ma_days'])}d  | score={winner['score']:.2f}  "
          f"mean APR={winner['mean_apr']:.1f}%  worst DD={winner['worst_dd']:.1f}%")
    with open(OUTDIR / "winner.json", "w") as f:
        json.dump({k: v for k, v in winner.items() if k != "all_ranked"}, f, indent=2, default=float)

    regime_winners = per_regime_winners(sweep_df)
    regime_winners.to_csv(OUTDIR / "regime_winners.csv", index=False)
    print("\n     per-regime winners:")
    for _, row in regime_winners.iterrows():
        print(f"       {row['regime']:6s}  tail={row['top_tail']:.0%}  spacing={row['top_spacing']*100:.1f}%  "
              f"MA={row['top_ma']}d  APR={row['top_apr_pct']:+.1f}%  DD={row['top_dd_pct']:.1f}%  Sharpe={row['top_sharpe']:.2f}")

    print("\n[3/5] Walk-forward at winning config (anti-survivor-bias)...")
    wf_df = run_walkforward(tail_pct=winner['tail_pct'],
                            spacing=winner['spacing_pct'],
                            ma_days=int(winner['ma_days']))
    wf_df.to_csv(OUTDIR / "walkforward_results.csv", index=False)
    for _, row in wf_df.iterrows():
        print(f"     {row['fold_start']:>20s} → {row['fold_end']:<12s}  "
              f"APR={row['apr_pct']:+6.1f}%  DD={row['max_dd_pct']:5.1f}%  Sharpe={row['sharpe']:5.2f}  "
              f"trades={int(row['trades']):4d}  slow-stops={int(row['stop_events']):2d}  fast-stops={int(row['fast_stop_events']):2d}")

    print("\n[4/5] Head-to-head: Infinity vs Balanced vs BuyHold...")
    cmp_df = run_comparison(tail_pct=winner['tail_pct'],
                            spacing=winner['spacing_pct'],
                            ma_days=int(winner['ma_days']))
    cmp_df.to_csv(OUTDIR / "comparison_results.csv", index=False)
    pivot = cmp_df.pivot(index="regime", columns="bot", values="apr_pct").reindex([r[0] for r in REGIMES])
    print("     APR% by bot and regime:")
    print(pivot.to_string(float_format=lambda x: f"{x:+6.1f}"))

    print("\n[5/5] Capacity test at $10k / $50k / $250k...")
    cap_df = run_capacity(tail_pct=winner['tail_pct'],
                          spacing=winner['spacing_pct'],
                          ma_days=int(winner['ma_days']))
    cap_df.to_csv(OUTDIR / "capacity_results.csv", index=False)
    print(cap_df.to_string(index=False, float_format=lambda x: f"{x:+.2f}"))

    print(f"\nDONE in {time.time() - t0:.1f}s.  Artifacts: {OUTDIR}")
    print(f"Next: write docs/gate3-reports/01-infinity-grid.md from these CSVs.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="smaller sweep for iteration (skips full grid)")
    parser.add_argument("--halt-sweep", action="store_true",
                        help="Gate 3 v3: lock config at v1 winner, sweep ONLY max_drawdown_halt_pct")
    args = parser.parse_args()
    if args.halt_sweep:
        halt_sweep_main()
    else:
        main(quick=args.quick)
