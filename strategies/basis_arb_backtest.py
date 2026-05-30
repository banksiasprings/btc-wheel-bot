"""
basis_arb_backtest.py — Gate 3 backtest harness for the BasisArbBot.

NOT a live deployment. The bot is being evaluated against Steven's
**portfolio-specialist scorecard** plus three Basis-Arb-specific items
that the spec promotes to scorecard-relevant (Steven's locked decisions
on `03-basis-arb-spec.md` open questions):
  - Q2 spot reference: run twice — Deribit index vs Binance spot — to
    quantify whether single-venue Deribit is honest.
  - Q3 perp_margin_frac: 1.0 / 0.5 / 0.2 are *scorecard-relevant*, not
    diagnostic. Steven wants to see how the bot dies, not just how it
    lives.
  - Q4 single-venue feasibility: the 4.24 bps median cross-reference
    noise from the data infrastructure run is the green light to plan
    Deribit-only; Gate 3 proves or disproves it.

Inputs
  - data/processed/basis_dataset_1h.csv (perp + deribit-index + binance
    spot, joined on hourly grid by scripts/build_basis_dataset.py).
  - data/raw/deribit/funding_rates.json (hourly `interest_1h` field).

Outputs (all under docs/gate3-reports/03-basis-arb-data/)
  - sweep_results.csv         — every (regime, config) → metrics row
  - regime_winners.csv        — per-regime top configs
  - winner.json               — chosen winning config + rationale
  - walkforward_results.csv   — walk-forward folds + holdout at winner
  - comparison_results.csv    — Basis-Arb vs FundingBot vs FundingBot-
                                (positive-only) vs BuyHoldBot per regime
  - failure_mode_results.csv  — perp_margin_frac {1.0, 0.5, 0.2}
                                head-to-head at the winning config
  - single_venue_results.csv  — winner config rerun with `spot =
                                binance` instead of `spot = deribit
                                index` — proves single-venue viability.

Run:
    python3.11 basis_arb_backtest.py            # full Gate 3 run (~minutes)
    python3.11 basis_arb_backtest.py --quick    # smaller sweep for iteration
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

from basis_arb_bot import BasisArbBot
from income_bots import FundingBot
from more_bots import BuyHoldBot

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "processed" / "basis_dataset_1h.csv"
FUNDING_PATH = ROOT / "data" / "raw" / "deribit" / "funding_rates.json"
OUTDIR = ROOT / "docs" / "gate3-reports" / "03-basis-arb-data"
DEFAULT_CAPITAL = 10_000.0
HOURS_PER_YEAR = 24 * 365
WARMUP_HOURS = 168  # one full lookback window

# ── regime windows ────────────────────────────────────────────────────────────
# Steven's locked regimes for Basis-Arb: vol-spike specialist windows + one
# calm baseline (the bleed test). These are different from the DCA-Smart /
# Infinity Grid regimes because Basis-Arb's edge fires on basis dislocation,
# not on price direction.

REGIMES = [
    ("covid_2020",  "2020-03-01", "2020-04-30",
     "Covid -50 % in 2 days. Largest basis dislocation in BTC history; spot oracles diverged from perp by hundreds of bps."),
    ("luna_2021",   "2021-05-15", "2021-05-25",
     "BTC -45 % over a week (LUNA's first crack came in 2022 but this 2021-05 cascade is the basis-event window the spec named)."),
    ("ftx_2022",    "2022-11-06", "2022-11-18",
     "FTX collapse. Dislocations + counterparty contagion — the strict test of dislocation_guard_z."),
    ("etf_2024",    "2024-01-01", "2024-01-31",
     "Bitcoin ETF launch (Jan 10). Basis ran wide in the run-up; sharp mean-reversion after approval."),
    ("calm_2023",   "2023-06-01", "2023-09-30",
     "Post-FTX calm baseline. Bot should be flat ~80 % of the time; net loss must stay < 1 % over the 4-month window."),
]
HOLDOUT_START = "2024-09-01"
HOLDOUT_END   = "2026-05-22"

# ── parameter sweep ──────────────────────────────────────────────────────────
# Per Steven's brief: lookback_days × entry_z × exit_z. max_position_btc and
# perp_margin_frac are dimensions-of-interest with their own dedicated runs
# (failure-mode test) rather than crossed in the main sweep.

LOOKBACK_DAYS_SWEEP   = [7, 14, 30]
ENTRY_Z_SWEEP         = [1.0, 1.5, 2.0]
EXIT_Z_SWEEP          = [0.0, 0.5]

QUICK_LOOKBACK        = [7]
QUICK_ENTRY_Z         = [1.5, 2.0]
QUICK_EXIT_Z          = [0.0]

# Failure-mode test — Steven's Q3 decision: SCORECARD-RELEVANT.
PERP_MARGIN_FRAC_GRID = [1.0, 0.5, 0.2]


# ── data loaders ─────────────────────────────────────────────────────────────

def load_dataset() -> pd.DataFrame:
    if not DATASET.exists():
        raise FileNotFoundError(
            f"{DATASET} missing — run scripts/build_basis_dataset.py first."
        )
    df = pd.read_csv(DATASET)
    df["ts"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    return df


def load_funding_map() -> dict[int, float]:
    """Returns {hour_floor_ms: interest_1h} for every funding-rate sample."""
    raw = json.loads(FUNDING_PATH.read_text())
    out: dict[int, float] = {}
    for r in raw.get("data", []):
        ts = int(r["timestamp"])
        hour_floor = (ts // 3_600_000) * 3_600_000
        # If multiple snapshots in one hour, keep the most recent.
        out[hour_floor] = float(r.get("interest_1h", 0.0))
    return out


def window_df(df: pd.DataFrame, start: str, end: str,
              warmup_hours: int = WARMUP_HOURS) -> tuple[pd.DataFrame, int]:
    """Return (sliced_df_including_warmup, warmup_bar_count)."""
    warm_start = (pd.Timestamp(start, tz="UTC")
                  - pd.Timedelta(hours=warmup_hours))
    end_ts = pd.Timestamp(end, tz="UTC")
    sub = df[(df["ts"] >= warm_start) & (df["ts"] < end_ts)].reset_index(drop=True)
    warm = int((sub["ts"] < pd.Timestamp(start, tz="UTC")).sum())
    return sub, warm


# ── metrics ───────────────────────────────────────────────────────────────────

def max_drawdown(eq: np.ndarray) -> float:
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.max((peak - eq) / np.where(peak > 0, peak, 1.0)))


def annualised(eq: np.ndarray, n_bars: int, capital: float) -> float:
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
    r = np.diff(eq) / np.maximum(1e-12, eq[:-1])
    sd = r.std()
    if sd == 0:
        return 0.0
    return float(r.mean() / sd * math.sqrt(HOURS_PER_YEAR))


def monthly_returns(ts: pd.Series, eq: np.ndarray) -> pd.Series:
    """Calendar-month terminal returns (used for cross-bot correlation)."""
    s = pd.DataFrame({"ts": ts.values, "eq": eq})
    # PeriodIndex drops the timezone; that's fine for grouping by calendar
    # month — silence the harmless deprecation chatter.
    s["month"] = pd.to_datetime(s["ts"], utc=True).dt.tz_localize(None).dt.to_period("M")
    grp = s.groupby("month")["eq"].agg(["first", "last"])
    return (grp["last"] / grp["first"] - 1.0)


@dataclass
class RunResult:
    final_equity: float
    return_pct: float
    apr_pct: float
    max_dd_pct: float
    sharpe_: float
    trades: int
    n_open_trades: int
    n_halt_events: int
    halt_reason: str | None
    pct_time_in_position: float
    total_funding: float
    total_convergence_pnl: float
    eq_curve: np.ndarray
    ts_curve: pd.Series

    @classmethod
    def from_eq(cls, eq, ts, capital, *, trades, n_open, n_halt, halt_reason,
                pct_in_pos, funding, conv_pnl):
        return cls(
            final_equity=float(eq[-1]),
            return_pct=float(eq[-1] / capital - 1.0) * 100.0,
            apr_pct=annualised(eq, len(eq), capital) * 100.0,
            max_dd_pct=max_drawdown(eq) * 100.0,
            sharpe_=sharpe(eq),
            trades=int(trades),
            n_open_trades=int(n_open),
            n_halt_events=int(n_halt),
            halt_reason=halt_reason,
            pct_time_in_position=float(pct_in_pos) * 100.0,
            total_funding=float(funding),
            total_convergence_pnl=float(conv_pnl),
            eq_curve=eq,
            ts_curve=ts,
        )


# ── single backtest runs ─────────────────────────────────────────────────────

def _run_basis_arb(df: pd.DataFrame, funding_map: dict[int, float],
                   warm_bars: int, *,
                   spot_column: str = "deribit_index_price",
                   capital: float = DEFAULT_CAPITAL,
                   lookback_hours: int = 168,
                   entry_z: float = 2.0,
                   exit_z: float = 0.25,
                   max_position_btc: float = 0.10,
                   perp_margin_frac: float = 1.0,
                   ) -> RunResult:
    """One Basis-Arb backtest run on a windowed dataframe.

    spot_column controls the spot reference: `deribit_index_price` is the
    single-venue case (what the live bot would see); `binance_spot_close`
    is the cross-venue cross-reference."""
    bot = BasisArbBot(
        capital=capital,
        lookback_hours=lookback_hours,
        entry_z_threshold=entry_z,
        exit_z_threshold=exit_z,
        max_position_btc=max_position_btc,
        perp_margin_frac=perp_margin_frac,
    )
    # Seed the basis_q from the warmup bars using the same spot column.
    warm = df.iloc[:warm_bars]
    warm_spot = warm[spot_column].values
    warm_perp = warm["perp_close"].values
    valid = (warm_spot > 0) & (warm_perp > 0)
    bot.warmup(warm_spot[valid].tolist(), warm_perp[valid].tolist())

    test = df.iloc[warm_bars:]
    n = len(test)
    eq = np.empty(n)
    n_open = 0
    n_halt = 0
    halt_reason = None
    hours_in_pos = 0
    last_state = bot.state

    spot_arr = test[spot_column].values
    perp_arr = test["perp_close"].values
    ts_arr = test["timestamp_ms"].values

    for i in range(n):
        s = float(spot_arr[i]) if not math.isnan(spot_arr[i]) else 0.0
        p = float(perp_arr[i]) if not math.isnan(perp_arr[i]) else 0.0
        if s <= 0 or p <= 0:
            eq[i] = bot.equity_now()
            continue
        hour_floor = (int(ts_arr[i]) // 3_600_000) * 3_600_000
        funding_1h = funding_map.get(hour_floor, 0.0)
        bot.step(s, p, funding_1h)
        if bot.state == bot.SHORT_BASIS and last_state != bot.SHORT_BASIS:
            n_open += 1
        if bot.state == bot.HALTED and last_state != bot.HALTED:
            n_halt += 1
            halt_reason = bot.halted_reason
        if bot.state == bot.SHORT_BASIS:
            hours_in_pos += 1
        last_state = bot.state
        eq[i] = bot.equity(s, p)

    pct_in_pos = hours_in_pos / max(1, n)
    return RunResult.from_eq(
        eq, test["ts"], capital,
        trades=bot.trades, n_open=n_open, n_halt=n_halt, halt_reason=halt_reason,
        pct_in_pos=pct_in_pos, funding=bot.total_funding_collected,
        conv_pnl=bot.total_convergence_pnl,
    )


def _run_funding(df: pd.DataFrame, funding_map: dict[int, float],
                 warm_bars: int, *, capital: float = DEFAULT_CAPITAL,
                 positive_only: bool = False) -> RunResult:
    """FundingBot baseline — always-on carry. The natural comparison."""
    bot = FundingBot(capital=capital, positive_only=positive_only, leverage=1.0)
    test = df.iloc[warm_bars:]
    n = len(test)
    eq = np.empty(n)
    ts_arr = test["timestamp_ms"].values
    for i in range(n):
        hour_floor = (int(ts_arr[i]) // 3_600_000) * 3_600_000
        bot.step(funding_map.get(hour_floor, 0.0))
        eq[i] = bot.equity_now()
    return RunResult.from_eq(
        eq, test["ts"], capital,
        trades=0, n_open=0, n_halt=int(bot.liquidated), halt_reason=None,
        pct_in_pos=1.0, funding=bot.equity - capital, conv_pnl=0.0,
    )


def _run_buyhold(df: pd.DataFrame, warm_bars: int, *,
                 capital: float = DEFAULT_CAPITAL) -> RunResult:
    """BuyHold baseline — long BTC at the warmup-end price."""
    bot = BuyHoldBot(capital=capital, fee=0.0006)
    test = df.iloc[warm_bars:]
    n = len(test)
    eq = np.empty(n)
    prices = test["perp_close"].values
    for i in range(n):
        bot.step(float(prices[i]))
        eq[i] = bot.equity(float(prices[i]))
    return RunResult.from_eq(
        eq, test["ts"], capital,
        trades=bot.trades, n_open=1, n_halt=0, halt_reason=None,
        pct_in_pos=1.0, funding=0.0, conv_pnl=0.0,
    )


# ── sweep ────────────────────────────────────────────────────────────────────

def run_sweep(df: pd.DataFrame, funding_map: dict[int, float],
              lookbacks_d, entries, exits) -> pd.DataFrame:
    rows = []
    for regime_name, start, end, _why in REGIMES:
        sub, warm = window_df(df, start, end)
        funding_bench = _run_funding(sub, funding_map, warm, positive_only=False)
        funding_smart = _run_funding(sub, funding_map, warm, positive_only=True)
        bh = _run_buyhold(sub, warm)
        for lb_d in lookbacks_d:
            for ez in entries:
                for xz in exits:
                    if xz >= ez:
                        continue
                    t0 = time.time()
                    arb = _run_basis_arb(
                        sub, funding_map, warm,
                        lookback_hours=lb_d * 24,
                        entry_z=ez, exit_z=xz,
                    )
                    rows.append({
                        "regime": regime_name,
                        "lookback_days": lb_d,
                        "entry_z": ez,
                        "exit_z": xz,
                        "arb_final_eq": arb.final_equity,
                        "arb_return_pct": arb.return_pct,
                        "arb_apr_pct": arb.apr_pct,
                        "arb_dd_pct": arb.max_dd_pct,
                        "arb_sharpe": arb.sharpe_,
                        "arb_trades": arb.trades,
                        "arb_opens": arb.n_open_trades,
                        "arb_halts": arb.n_halt_events,
                        "arb_halt_reason": arb.halt_reason or "",
                        "arb_pct_in_pos": arb.pct_time_in_position,
                        "arb_funding": arb.total_funding,
                        "arb_convergence_pnl": arb.total_convergence_pnl,
                        "funding_return_pct": funding_bench.return_pct,
                        "funding_smart_return_pct": funding_smart.return_pct,
                        "bh_return_pct": bh.return_pct,
                        "arb_vs_funding_pp":
                            (arb.final_equity / funding_bench.final_equity - 1.0) * 100.0
                            if funding_bench.final_equity > 0 else 0.0,
                        "arb_vs_funding_smart_pp":
                            (arb.final_equity / funding_smart.final_equity - 1.0) * 100.0
                            if funding_smart.final_equity > 0 else 0.0,
                        "arb_vs_bh_pp":
                            (arb.final_equity / bh.final_equity - 1.0) * 100.0
                            if bh.final_equity > 0 else 0.0,
                        "runtime_s": round(time.time() - t0, 3),
                    })
        print(f"  swept regime={regime_name:11s} ({len(sub)} bars)")
    return pd.DataFrame(rows)


# ── per-regime winners ───────────────────────────────────────────────────────

def per_regime_winners(sweep_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for regime_name, _s, _e, _why in REGIMES:
        sub = sweep_df[sweep_df["regime"] == regime_name].copy()
        if len(sub) == 0:
            continue
        sub = sub.sort_values("arb_vs_funding_smart_pp", ascending=False)
        top = sub.iloc[0]
        rows.append({
            "regime": regime_name,
            "lookback_days": int(top["lookback_days"]),
            "entry_z": float(top["entry_z"]),
            "exit_z": float(top["exit_z"]),
            "arb_return_pct": float(top["arb_return_pct"]),
            "arb_dd_pct": float(top["arb_dd_pct"]),
            "arb_trades": int(top["arb_trades"]),
            "arb_pct_in_pos": float(top["arb_pct_in_pos"]),
            "arb_vs_funding_pp": float(top["arb_vs_funding_pp"]),
            "arb_vs_funding_smart_pp": float(top["arb_vs_funding_smart_pp"]),
            "arb_vs_bh_pp": float(top["arb_vs_bh_pp"]),
        })
    return pd.DataFrame(rows)


def pick_global_winner(sweep_df: pd.DataFrame) -> dict:
    """Specialist scorecard for basis arb (spec §8.4):
      TIER 1 — vol-spike combined ≥ +8 % AND calm loss < 1 %
               AND vol-spike beat funding_smart by ≥ +3 %/yr AND no halts
               outside FTX.
      TIER 2 — vol-spike combined ≥ +4 % AND calm loss < 1 %.
      FALLBACK — composite: mean(vol-spike returns) − 2×|calm loss|.
    Tie-break inside whichever tier: higher mean vol-spike pp-vs-funding-smart;
    then prefer smaller lookback (more responsive).

    "Vol-spike" set: covid_2020 + luna_2021 + ftx_2022 + etf_2024 (4 windows).
    """
    vol_spike_regimes = ["covid_2020", "luna_2021", "ftx_2022", "etf_2024"]
    keys = ["lookback_days", "entry_z", "exit_z"]

    pivots = {}
    for reg in vol_spike_regimes + ["calm_2023"]:
        sub = sweep_df[sweep_df["regime"] == reg].set_index(keys)
        pivots[reg] = sub

    # Build a per-config aggregate frame.
    sample = pivots[vol_spike_regimes[0]]
    rows = []
    for idx, _ in sample.iterrows():
        vs_funding_smart = []
        vs_funding = []
        returns = []
        halts_outside_ftx = 0
        for reg in vol_spike_regimes:
            r = pivots[reg].loc[idx]
            vs_funding_smart.append(r["arb_vs_funding_smart_pp"])
            vs_funding.append(r["arb_vs_funding_pp"])
            returns.append(r["arb_return_pct"])
            if reg != "ftx_2022" and r["arb_halts"] > 0:
                halts_outside_ftx += 1
        try:
            calm_r = pivots["calm_2023"].loc[idx]
        except KeyError:
            continue
        rows.append({
            "lookback_days": idx[0],
            "entry_z": idx[1],
            "exit_z": idx[2],
            "vol_spike_combined_pct": float(sum(returns)),
            "vol_spike_mean_vs_funding_smart_pp":
                float(sum(vs_funding_smart) / len(vs_funding_smart)),
            "vol_spike_mean_vs_funding_pp":
                float(sum(vs_funding) / len(vs_funding)),
            "calm_return_pct": float(calm_r["arb_return_pct"]),
            "calm_halts": int(calm_r["arb_halts"]),
            "halts_outside_ftx": halts_outside_ftx,
        })
    joined = pd.DataFrame(rows)

    tier1 = joined[
        (joined["vol_spike_combined_pct"] >= 8.0)
        & (joined["calm_return_pct"] >= -1.0)
        & (joined["vol_spike_mean_vs_funding_smart_pp"] >= 3.0)
        & (joined["halts_outside_ftx"] == 0)
    ].copy()
    tier2 = joined[
        (joined["vol_spike_combined_pct"] >= 4.0)
        & (joined["calm_return_pct"] >= -1.0)
    ].copy()

    def _rank(d):
        d = d.copy()
        d["score"] = d["vol_spike_mean_vs_funding_smart_pp"]
        return d.sort_values(["score", "lookback_days"], ascending=[False, True])

    if len(tier1) > 0:
        ranked = _rank(tier1)
        rationale = (f"TIER 1 PASS: {len(tier1)} configs cleared the full "
                     "specialist scorecard (vol-spike ≥+8%, calm ≥-1%, "
                     "beat funding_smart by ≥+3%/yr, no halts outside FTX).")
    elif len(tier2) > 0:
        ranked = _rank(tier2)
        rationale = (f"TIER 2 PASS: {len(tier2)} configs cleared the relaxed "
                     "bar (vol-spike ≥+4%, calm ≥-1%). Full specialist "
                     "scorecard not met.")
    else:
        joined["score"] = (joined["vol_spike_combined_pct"]
                           - 2.0 * np.maximum(-joined["calm_return_pct"], 0.0))
        ranked = joined.sort_values(["score", "lookback_days"], ascending=[False, True])
        rationale = ("FALLBACK: no config met any specialist tier. Picked "
                     "by composite score = vol_spike_combined "
                     "− 2×|calm loss|.")

    top = ranked.iloc[0]
    return {
        "lookback_days": int(top["lookback_days"]),
        "entry_z": float(top["entry_z"]),
        "exit_z": float(top["exit_z"]),
        "vol_spike_combined_pct": float(top["vol_spike_combined_pct"]),
        "vol_spike_mean_vs_funding_smart_pp": float(top["vol_spike_mean_vs_funding_smart_pp"]),
        "vol_spike_mean_vs_funding_pp": float(top["vol_spike_mean_vs_funding_pp"]),
        "calm_return_pct": float(top["calm_return_pct"]),
        "calm_halts": int(top["calm_halts"]),
        "halts_outside_ftx": int(top["halts_outside_ftx"]),
        "rationale": rationale,
        "n_tier1_configs": int(len(tier1)),
        "n_tier2_configs": int(len(tier2)),
        "top10": ranked.head(10).to_dict(orient="records"),
    }


# ── walk-forward ─────────────────────────────────────────────────────────────

def run_walkforward(df: pd.DataFrame, funding_map: dict[int, float], *,
                    lookback_days: int, entry_z: float, exit_z: float,
                    test_months: int = 6, stride_months: int = 2,
                    series_start: str = "2019-05-01") -> pd.DataFrame:
    """6-month folds, 2-month stride (~33 % of test window per the spec's
    walk-forward shape). Series start is 2019-05 so the first fold gets a
    populated funding-rate map (the endpoint's earliest snapshot is 2019-04-30).
    """
    folds = []
    series_start_ts = pd.Timestamp(series_start, tz="UTC")
    holdout_ts = pd.Timestamp(HOLDOUT_START, tz="UTC")
    cursor = series_start_ts + pd.Timedelta(hours=WARMUP_HOURS + 24)
    while cursor + pd.DateOffset(months=test_months) <= holdout_ts:
        folds.append((cursor, cursor + pd.DateOffset(months=test_months)))
        cursor += pd.DateOffset(months=stride_months)

    rows = []
    for ts, te in folds:
        sub, warm = window_df(df, ts.strftime("%Y-%m-%d"), te.strftime("%Y-%m-%d"))
        if len(sub) - warm < 30 * 24:
            continue
        arb = _run_basis_arb(sub, funding_map, warm,
                             lookback_hours=lookback_days * 24,
                             entry_z=entry_z, exit_z=exit_z)
        f_bench = _run_funding(sub, funding_map, warm, positive_only=False)
        f_smart = _run_funding(sub, funding_map, warm, positive_only=True)
        bh = _run_buyhold(sub, warm)
        rows.append({
            "fold_start": ts.strftime("%Y-%m-%d"),
            "fold_end":   te.strftime("%Y-%m-%d"),
            "arb_return": arb.return_pct,
            "arb_apr": arb.apr_pct,
            "arb_dd": arb.max_dd_pct,
            "arb_sharpe": arb.sharpe_,
            "arb_trades": arb.trades,
            "arb_opens": arb.n_open_trades,
            "arb_halts": arb.n_halt_events,
            "arb_pct_in_pos": arb.pct_time_in_position,
            "funding_return": f_bench.return_pct,
            "funding_smart_return": f_smart.return_pct,
            "bh_return": bh.return_pct,
            "arb_vs_funding_pp": (arb.final_equity / f_bench.final_equity - 1.0) * 100.0
                if f_bench.final_equity > 0 else 0.0,
            "arb_vs_funding_smart_pp": (arb.final_equity / f_smart.final_equity - 1.0) * 100.0
                if f_smart.final_equity > 0 else 0.0,
            "arb_vs_bh_pp": (arb.final_equity / bh.final_equity - 1.0) * 100.0
                if bh.final_equity > 0 else 0.0,
        })

    # Held-out fold.
    sub_h, warm_h = window_df(df, HOLDOUT_START, HOLDOUT_END)
    arb_h = _run_basis_arb(sub_h, funding_map, warm_h,
                           lookback_hours=lookback_days * 24,
                           entry_z=entry_z, exit_z=exit_z)
    f_h = _run_funding(sub_h, funding_map, warm_h, positive_only=False)
    f_s_h = _run_funding(sub_h, funding_map, warm_h, positive_only=True)
    bh_h = _run_buyhold(sub_h, warm_h)
    rows.append({
        "fold_start": "HOLDOUT " + HOLDOUT_START,
        "fold_end":   HOLDOUT_END,
        "arb_return": arb_h.return_pct,
        "arb_apr": arb_h.apr_pct,
        "arb_dd": arb_h.max_dd_pct,
        "arb_sharpe": arb_h.sharpe_,
        "arb_trades": arb_h.trades,
        "arb_opens": arb_h.n_open_trades,
        "arb_halts": arb_h.n_halt_events,
        "arb_pct_in_pos": arb_h.pct_time_in_position,
        "funding_return": f_h.return_pct,
        "funding_smart_return": f_s_h.return_pct,
        "bh_return": bh_h.return_pct,
        "arb_vs_funding_pp": (arb_h.final_equity / f_h.final_equity - 1.0) * 100.0
            if f_h.final_equity > 0 else 0.0,
        "arb_vs_funding_smart_pp": (arb_h.final_equity / f_s_h.final_equity - 1.0) * 100.0
            if f_s_h.final_equity > 0 else 0.0,
        "arb_vs_bh_pp": (arb_h.final_equity / bh_h.final_equity - 1.0) * 100.0
            if bh_h.final_equity > 0 else 0.0,
    })
    return pd.DataFrame(rows)


# ── comparison ──────────────────────────────────────────────────────────────

def run_comparison(df: pd.DataFrame, funding_map: dict[int, float], *,
                   lookback_days: int, entry_z: float, exit_z: float):
    rows = []
    monthly_curves: dict[str, list[pd.Series]] = {
        "basis_arb": [], "funding": [], "funding_smart": [], "buyhold": []
    }
    for regime_name, start, end, _why in REGIMES + [("holdout", HOLDOUT_START, HOLDOUT_END, "")]:
        sub, warm = window_df(df, start, end)
        arb = _run_basis_arb(sub, funding_map, warm,
                             lookback_hours=lookback_days * 24,
                             entry_z=entry_z, exit_z=exit_z)
        f_bench = _run_funding(sub, funding_map, warm, positive_only=False)
        f_smart = _run_funding(sub, funding_map, warm, positive_only=True)
        bh = _run_buyhold(sub, warm)
        monthly_curves["basis_arb"].append(monthly_returns(arb.ts_curve, arb.eq_curve))
        monthly_curves["funding"].append(monthly_returns(f_bench.ts_curve, f_bench.eq_curve))
        monthly_curves["funding_smart"].append(monthly_returns(f_smart.ts_curve, f_smart.eq_curve))
        monthly_curves["buyhold"].append(monthly_returns(bh.ts_curve, bh.eq_curve))
        for name, r in [("basis_arb", arb), ("funding", f_bench),
                        ("funding_smart", f_smart), ("buyhold", bh)]:
            rows.append({
                "regime": regime_name, "bot": name,
                "final_eq": r.final_equity,
                "return_pct": r.return_pct,
                "apr_pct": r.apr_pct,
                "max_dd_pct": r.max_dd_pct,
                "sharpe": r.sharpe_,
                "trades": r.trades,
                "pct_time_in_pos": r.pct_time_in_position,
                "halts": r.n_halt_events,
            })

    # Build correlation matrix on monthly returns concatenated across windows.
    monthly = pd.DataFrame({
        k: pd.concat(v).reset_index(drop=True) for k, v in monthly_curves.items()
    }).dropna()
    corr = monthly.corr() if len(monthly) > 1 else pd.DataFrame()

    return pd.DataFrame(rows), corr


# ── failure-mode test (scorecard-relevant per Steven's Q3 decision) ──────────

def run_failure_modes(df: pd.DataFrame, funding_map: dict[int, float], *,
                      lookback_days: int, entry_z: float, exit_z: float
                      ) -> pd.DataFrame:
    rows = []
    for regime_name, start, end, _why in REGIMES:
        sub, warm = window_df(df, start, end)
        for pmf in PERP_MARGIN_FRAC_GRID:
            arb = _run_basis_arb(sub, funding_map, warm,
                                 lookback_hours=lookback_days * 24,
                                 entry_z=entry_z, exit_z=exit_z,
                                 perp_margin_frac=pmf)
            rows.append({
                "regime": regime_name,
                "perp_margin_frac": pmf,
                "return_pct": arb.return_pct,
                "max_dd_pct": arb.max_dd_pct,
                "trades": arb.trades,
                "opens": arb.n_open_trades,
                "halts": arb.n_halt_events,
                "halt_reason": arb.halt_reason or "",
                "pct_in_pos": arb.pct_time_in_position,
                "would_have_been_liquidated": arb.halt_reason == "perp_margin_call",
            })
    return pd.DataFrame(rows)


# ── single-venue feasibility (Q4) ────────────────────────────────────────────

def run_single_venue_check(df: pd.DataFrame, funding_map: dict[int, float], *,
                           lookback_days: int, entry_z: float, exit_z: float
                           ) -> pd.DataFrame:
    rows = []
    for regime_name, start, end, _why in REGIMES + [("holdout", HOLDOUT_START, HOLDOUT_END, "")]:
        sub, warm = window_df(df, start, end)
        for venue, col in (("deribit_index", "deribit_index_price"),
                           ("binance_spot",  "binance_spot_close")):
            arb = _run_basis_arb(sub, funding_map, warm,
                                 spot_column=col,
                                 lookback_hours=lookback_days * 24,
                                 entry_z=entry_z, exit_z=exit_z)
            rows.append({
                "regime": regime_name,
                "spot_reference": venue,
                "return_pct": arb.return_pct,
                "max_dd_pct": arb.max_dd_pct,
                "trades": arb.trades,
                "opens": arb.n_open_trades,
                "halts": arb.n_halt_events,
                "pct_in_pos": arb.pct_time_in_position,
                "halt_reason": arb.halt_reason or "",
            })
    return pd.DataFrame(rows)


# ── orchestrator ─────────────────────────────────────────────────────────────

def main(quick: bool = False):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    mode = "QUICK" if quick else "FULL"
    print(f"\n=== BASIS ARB — GATE 3 BACKTEST ({mode}) ===\n")
    t0 = time.time()

    print("[loading] dataset + funding map...")
    df = load_dataset()
    funding_map = load_funding_map()
    print(f"  dataset: {len(df):,} hourly rows  "
          f"{df['ts'].min()} → {df['ts'].max()}")
    print(f"  funding: {len(funding_map):,} hourly samples")

    lookbacks = QUICK_LOOKBACK if quick else LOOKBACK_DAYS_SWEEP
    entries   = QUICK_ENTRY_Z  if quick else ENTRY_Z_SWEEP
    exits     = QUICK_EXIT_Z   if quick else EXIT_Z_SWEEP
    n_cfg = sum(1 for _lb in lookbacks for ez in entries for xz in exits if xz < ez)
    n_total = n_cfg * len(REGIMES)

    print(f"\n[1/6] Parameter sweep across {len(REGIMES)} regimes...")
    print(f"     {len(lookbacks)} lookback × {len(entries)} entry_z × {len(exits)} exit_z "
          f"⇒ {n_cfg} valid configs × {len(REGIMES)} regimes = {n_total} runs")
    sweep_df = run_sweep(df, funding_map, lookbacks, entries, exits)
    sweep_df.to_csv(OUTDIR / "sweep_results.csv", index=False)

    print("\n[2/6] Per-regime winners (ranked by vs-funding-smart pp)...")
    regime_winners = per_regime_winners(sweep_df)
    regime_winners.to_csv(OUTDIR / "regime_winners.csv", index=False)
    for _, row in regime_winners.iterrows():
        print(f"     {row['regime']:11s}  lb={int(row['lookback_days'])}d  "
              f"ez={row['entry_z']:.1f}  xz={row['exit_z']:.1f}  "
              f"| ret={row['arb_return_pct']:+6.2f}%  "
              f"vs-fnd={row['arb_vs_funding_pp']:+6.2f}pp  "
              f"vs-fnd-smart={row['arb_vs_funding_smart_pp']:+6.2f}pp  "
              f"vs-bh={row['arb_vs_bh_pp']:+6.2f}pp  "
              f"trades={int(row['arb_trades'])}")

    print("\n     GLOBAL WINNER pick (specialist scorecard)...")
    winner = pick_global_winner(sweep_df)
    print(f"     {winner['rationale']}")
    print(f"     lookback={winner['lookback_days']}d  entry_z={winner['entry_z']:.1f}  "
          f"exit_z={winner['exit_z']:.1f}")
    print(f"     vol-spike combined: {winner['vol_spike_combined_pct']:+.2f}%  |  "
          f"calm: {winner['calm_return_pct']:+.2f}%")
    print(f"     mean vs funding_smart: {winner['vol_spike_mean_vs_funding_smart_pp']:+.2f}pp  |  "
          f"halts outside FTX: {winner['halts_outside_ftx']}")
    with open(OUTDIR / "winner.json", "w") as f:
        json.dump({k: v for k, v in winner.items() if k != "top10"},
                  f, indent=2, default=float)

    print("\n[3/6] Walk-forward at winning config + holdout...")
    wf_df = run_walkforward(df, funding_map,
                            lookback_days=winner["lookback_days"],
                            entry_z=winner["entry_z"],
                            exit_z=winner["exit_z"])
    wf_df.to_csv(OUTDIR / "walkforward_results.csv", index=False)
    for _, row in wf_df.iterrows():
        print(f"     {str(row['fold_start']):>22s} → {row['fold_end']:<12s}  "
              f"arb={row['arb_return']:+7.2f}%  "
              f"fnd={row['funding_return']:+6.2f}%  "
              f"fnd_smart={row['funding_smart_return']:+6.2f}%  "
              f"bh={row['bh_return']:+7.1f}%  | "
              f"vs-fnd-smart={row['arb_vs_funding_smart_pp']:+5.2f}pp  "
              f"trades={int(row['arb_trades']):2d}  "
              f"halts={int(row['arb_halts'])}")

    print("\n[4/6] Head-to-head per regime + holdout...")
    cmp_df, corr_df = run_comparison(df, funding_map,
                                     lookback_days=winner["lookback_days"],
                                     entry_z=winner["entry_z"],
                                     exit_z=winner["exit_z"])
    cmp_df.to_csv(OUTDIR / "comparison_results.csv", index=False)
    if not corr_df.empty:
        corr_df.to_csv(OUTDIR / "monthly_return_correlation.csv")
        print("\n     Monthly-return correlation matrix (across all regime months):")
        print(corr_df.round(3).to_string())
    pivot_ret = cmp_df.pivot(index="regime", columns="bot", values="return_pct")
    pivot_dd  = cmp_df.pivot(index="regime", columns="bot", values="max_dd_pct")
    print("\n     Terminal return % by regime:")
    print(pivot_ret.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:+8.2f}"))
    print("\n     Max drawdown % by regime:")
    print(pivot_dd.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:7.2f}"))

    print("\n[5/6] FAILURE-MODE test (scorecard-relevant per Steven's Q3)...")
    fm_df = run_failure_modes(df, funding_map,
                              lookback_days=winner["lookback_days"],
                              entry_z=winner["entry_z"],
                              exit_z=winner["exit_z"])
    fm_df.to_csv(OUTDIR / "failure_mode_results.csv", index=False)
    pivot_fm_ret = fm_df.pivot(index="regime", columns="perp_margin_frac", values="return_pct")
    pivot_fm_halt = fm_df.pivot(index="regime", columns="perp_margin_frac", values="halts")
    pivot_fm_liq = fm_df.pivot(index="regime", columns="perp_margin_frac",
                               values="would_have_been_liquidated")
    print("\n     Return % by perp_margin_frac:")
    print(pivot_fm_ret.reindex([r[0] for r in REGIMES])
          .to_string(float_format=lambda x: f"{x:+8.2f}"))
    print("\n     Halt events by perp_margin_frac:")
    print(pivot_fm_halt.reindex([r[0] for r in REGIMES]).to_string())
    print("\n     Would-have-been-liquidated by perp_margin_frac:")
    print(pivot_fm_liq.reindex([r[0] for r in REGIMES]).to_string())

    print("\n[6/6] Single-venue feasibility — winner config × spot reference...")
    sv_df = run_single_venue_check(df, funding_map,
                                   lookback_days=winner["lookback_days"],
                                   entry_z=winner["entry_z"],
                                   exit_z=winner["exit_z"])
    sv_df.to_csv(OUTDIR / "single_venue_results.csv", index=False)
    pivot_sv = sv_df.pivot(index="regime", columns="spot_reference", values="return_pct")
    print("\n     Return % by spot reference (Deribit index vs Binance spot):")
    print(pivot_sv.reindex([r[0] for r in REGIMES] + ["holdout"])
          .to_string(float_format=lambda x: f"{x:+8.2f}"))

    print(f"\nDONE in {time.time() - t0:.1f}s. Artifacts under {OUTDIR}")
    print("Next: write docs/gate3-reports/03-basis-arb.md from these CSVs.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="smaller sweep for iteration")
    args = parser.parse_args()
    main(quick=args.quick)
