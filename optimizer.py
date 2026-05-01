"""
optimizer.py — Multi-bot parameter evolution engine.

Two modes:
  1. Sweep mode  — vary ONE parameter at a time across its full range,
                   hold all others at baseline. Shows each variable's
                   individual sensitivity. Use this first to build intuition.

  2. Evolve mode — genetic algorithm over ALL parameters simultaneously.
                   Spawn N bots, run in parallel, score by fitness function,
                   mutate the survivors, repeat for K generations.
                   Use this after sweep mode to find optimal combinations.

Run:
    python optimizer.py --mode sweep              # one param at a time
    python optimizer.py --mode evolve             # genetic evolution
    python optimizer.py --mode sweep --param iv_rank_threshold
    python optimizer.py --mode evolve --generations 10 --population 20

The fitness function rewards Sharpe ratio and win rate while penalising
drawdown and assignment frequency. Tune the weights below to match your
risk preference.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import multiprocessing as mp
import os
import random
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from loguru import logger

# ── Parameter space ────────────────────────────────────────────────────────────


@dataclass
class ParamSet:
    """One complete set of strategy parameters (one 'genome')."""
    iv_rank_threshold: float = 0.50
    target_delta_min: float = 0.15
    target_delta_max: float = 0.30
    approx_otm_offset: float = 0.08
    max_dte: float = 35
    min_dte: float = 5
    max_equity_per_leg: float = 0.05
    premium_fraction_of_spot: float = 0.015
    iv_rank_window_days: int = 365    # lookback for IV rank calculation
    min_free_equity_fraction: float = 0.25  # minimum buffer kept free at all times
    starting_equity: float = 10000.0        # backtest starting account size (USD)


# Define the valid range for each parameter during evolution / sweep
PARAM_RANGES: dict[str, tuple[float, float, float]] = {
    # name: (min, max, step)
    "iv_rank_threshold":        (0.20, 0.80, 0.05),
    "target_delta_min":         (0.10, 0.25, 0.025),
    "target_delta_max":         (0.20, 0.45, 0.025),
    "approx_otm_offset":        (0.03, 0.18, 0.01),
    "max_dte":                  (7,    45,   7),
    "min_dte":                  (2,    14,   1),
    "max_equity_per_leg":       (0.02, 0.12, 0.01),
    "premium_fraction_of_spot": (0.008, 0.030, 0.002),
    "iv_rank_window_days":      (90,   365,  30),
    "min_free_equity_fraction": (0.00, 0.40, 0.05),   # 0% to 40% buffer
    "starting_equity":          (1000, 100000, 5000),   # $1k to $100k account size
}

# Fitness function weights (tune to your risk preference)
FITNESS_WEIGHTS = {
    "sharpe":           0.35,    # Sharpe ratio (higher = better)
    "win_rate":         0.25,    # Win rate (higher = better)
    "total_return":     0.20,    # Total return % (higher = better)
    "max_drawdown":     0.15,    # Max drawdown (LOWER = better — inverted)
    "num_cycles":       0.05,    # More cycles = more data (higher = better)
}


# ── Fitness scoring ────────────────────────────────────────────────────────────


EVOLVE_GOALS = ("balanced", "max_yield", "safest", "sharpe", "capital_roi", "daily_trader")


def _fitness_for_goal(result: dict, goal: str) -> float:
    """Re-score a backtest result dict using a goal-specific fitness function."""
    r   = result.get("total_return_pct", 0) / 100
    sharpe = result.get("sharpe_ratio", 0)
    win = result.get("win_rate_pct", 0) / 100
    dd  = abs(result.get("max_drawdown_pct", 0)) / 100 + 0.001

    if goal == "max_yield":
        return r * 10 + win * 2
    elif goal == "safest":
        return (win * 5) - (dd * 10) + (r * 1)
    elif goal == "sharpe":
        return sharpe * 3 + win * 1
    elif goal == "capital_roi":
        # Optimise for the user's stated thesis: small-capital, high-margin-ROI,
        # low-risk hedged premium harvesting. Compared to the previous scorer
        # (return-on-margin only) this also rewards LOW minimum viable capital
        # and LOW margin utilisation — without those, the optimizer happily
        # picks configs that need $200k+ to trade and lock 90% of equity in
        # collateral. Both kill the "millions of small bots" thesis.
        margin_roi          = result.get("annualised_margin_roi", 0.0)
        prem_on_margin      = result.get("premium_on_margin", 0.0)
        min_viable_capital  = result.get("min_viable_capital", 0.0)
        avg_margin_util     = result.get("avg_margin_utilization", 0.0)
        num_trades          = result.get("num_cycles", 0)

        # Penalise idle strategies — fewer than 6 trades over the backtest
        # period means the activity numbers can't be trusted.
        activity_penalty = 1.0 if num_trades >= 6 else num_trades / 6.0

        # Reward low minimum capital — saturates at $20k (small-bot target),
        # zero reward beyond $200k (institutional-only).
        if min_viable_capital <= 0:
            capital_score = 0.0   # no data; treat as worst
        elif min_viable_capital <= 20_000:
            capital_score = 1.0
        elif min_viable_capital >= 200_000:
            capital_score = 0.0
        else:
            capital_score = 1.0 - (min_viable_capital - 20_000) / 180_000.0

        # Reward low margin utilisation — using <30% leaves room for vol
        # spikes and gamma rebalances. >70% is dangerous.
        if avg_margin_util <= 0.30:
            util_score = 1.0
        elif avg_margin_util >= 0.70:
            util_score = 0.0
        else:
            util_score = 1.0 - (avg_margin_util - 0.30) / 0.40

        # Reward yield-on-margin saturating at 30% (premium income / margin
        # deployed). 30% over a 12-month backtest = exceptional; 5% = mediocre.
        prem_score = float(np.clip(prem_on_margin / 0.30, 0.0, 1.0))

        score = (
            0.30 * min(margin_roi, 2.0) / 2.0 +    # 30% — annualised margin ROI capped at 200%
            0.20 * prem_score +                      # 20% — premium income / margin used
            0.15 * capital_score +                   # 15% — low capital floor
            0.15 * util_score +                      # 15% — keep margin utilisation below 50%
            0.10 * min(sharpe, 3.0) / 3.0 +         # 10% — risk-adjusted return
            0.05 * max(0.0, 1.0 - dd / 0.30) +      # 5% — drawdown discipline
            0.05 * win                                # 5% — win rate
        ) * activity_penalty
        return round(float(np.clip(score, 0.0, 1.0)), 4)
    elif goal == "daily_trader":
        # Optimise for frequent trading — useful for testing the full pipeline
        # with real trade flow rather than waiting weeks for signals.
        # Heavily rewards trade count; requires the strategy to at least
        # break even (penalises losses but doesn't chase big returns).
        num_trades = result.get("num_cycles", 0)
        activity   = min(num_trades / 60.0, 1.0)          # saturates at ~60 trades per backtest period
        profit_ok  = 1.0 if r >= 0 else max(0.0, 1.0 + r * 2)  # penalise losses gently
        safety     = max(0.0, 1.0 - dd / 0.50)            # only hurts if drawdown > 50%
        return activity * 3.0 + profit_ok * 1.0 + safety * 0.5
    else:  # "balanced"
        return (sharpe * 2) + (r * 3) + (win * 2) - (dd * 3)


def fitness_score(results: "BacktestResults") -> float:  # noqa: F821
    """
    Calculate a single fitness score from backtest results.

    Returns a value in approximately [0, 10]. Higher = better bot.

    The function normalises each metric to [0, 1] using expected ranges,
    then takes a weighted sum.
    """
    if results.num_cycles == 0:
        return 0.0

    # Normalise each metric to [0, 1]
    sharpe_norm      = float(np.clip(results.sharpe_ratio / 5.0, 0, 1))
    win_rate_norm    = results.win_rate_pct / 100.0
    return_norm      = float(np.clip(results.total_return_pct / 100.0, 0, 1))
    drawdown_norm    = 1.0 - float(np.clip(results.max_drawdown_pct / 30.0, 0, 1))  # inverted
    cycles_norm      = float(np.clip(results.num_cycles / 50.0, 0, 1))

    score = (
        FITNESS_WEIGHTS["sharpe"]       * sharpe_norm +
        FITNESS_WEIGHTS["win_rate"]     * win_rate_norm +
        FITNESS_WEIGHTS["total_return"] * return_norm +
        FITNESS_WEIGHTS["max_drawdown"] * drawdown_norm +
        FITNESS_WEIGHTS["num_cycles"]   * cycles_norm
    ) * 10.0  # scale to ~[0, 10]

    return round(score, 4)


# ── Experience calibration ────────────────────────────────────────────────────


def load_experience_calibration(experience_path: Path) -> dict:
    """
    Read experience.jsonl and compute actual performance per (param_name, value) bucket.
    Returns {} if file missing or < 5 records.

    Return format: {("iv_rank_threshold", 0.60): {"win_rate": 0.72, "avg_pnl_pct": 0.043, "n": 8}, ...}
    """
    if not experience_path.exists():
        return {}

    records = []
    try:
        with open(experience_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception:
        return {}

    if len(records) < 5:
        return {}

    # Group by (param_name, rounded_value) for each param in PARAM_RANGES
    from collections import defaultdict
    buckets: dict[tuple, list] = defaultdict(list)

    for rec in records:
        params = rec.get("params", {})
        outcome = rec.get("outcome", {})
        win = outcome.get("win", False)
        pnl_pct = outcome.get("pnl_pct", 0.0)

        for param_name in PARAM_RANGES:
            if param_name in params:
                val = params[param_name]
                # Round to step size to bucket similar values together
                step = PARAM_RANGES[param_name][2]
                rounded = round(round(val / step) * step, 6)
                buckets[(param_name, rounded)].append({"win": win, "pnl_pct": pnl_pct})

    calibration = {}
    for (param_name, val), trades in buckets.items():
        if len(trades) >= 2:
            calibration[(param_name, val)] = {
                "win_rate": sum(1 for t in trades if t["win"]) / len(trades),
                "avg_pnl_pct": sum(t["pnl_pct"] for t in trades) / len(trades),
                "n": len(trades),
            }

    return calibration


def apply_experience_blend(
    historical_fitness: float,
    genome_params: dict,
    calibration: dict,
    total_records: int,
) -> float:
    """
    Blend historical backtest fitness with actual experience performance.

    Blend ratio shifts as experience grows:
      < 10 trades:  80% historical, 20% experience
      10-19 trades: 60% historical, 40% experience
      20-29 trades: 50/50
      30+ trades:   30% historical, 70% experience (experience dominates)
    """
    # Find matching experience buckets for this genome's params
    experience_scores = []

    for param_name, (p_min, p_max, step) in PARAM_RANGES.items():
        val = genome_params.get(param_name)
        if val is None:
            continue
        rounded = round(round(val / step) * step, 6)
        key = (param_name, rounded)
        if key in calibration and calibration[key]["n"] >= 2:
            entry = calibration[key]
            # Normalise: win_rate already 0-1, pnl_pct normalise to ~[0,1]
            win_norm = entry["win_rate"]
            pnl_norm = float(np.clip(entry["avg_pnl_pct"] / 0.05, 0, 1))
            exp_score = (0.6 * win_norm + 0.4 * pnl_norm) * 10.0
            experience_scores.append(exp_score)

    if not experience_scores:
        return historical_fitness

    avg_exp_score = sum(experience_scores) / len(experience_scores)

    # Determine blend ratio
    if total_records < 10:
        hist_w, exp_w = 0.80, 0.20
    elif total_records < 20:
        hist_w, exp_w = 0.60, 0.40
    elif total_records < 30:
        hist_w, exp_w = 0.50, 0.50
    else:
        hist_w, exp_w = 0.30, 0.70

    return round(hist_w * historical_fitness + exp_w * avg_exp_score, 4)


def summarise_experience(experience_path: Path) -> dict:
    """Compute summary stats from experience.jsonl for dashboard display."""
    if not experience_path.exists():
        return {"total_trades": 0, "calibration_level": "none"}

    records = []
    try:
        with open(experience_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception:
        return {"total_trades": 0, "calibration_level": "none"}

    n = len(records)
    if n == 0:
        return {"total_trades": 0, "calibration_level": "none"}

    wins = [r for r in records if r.get("outcome", {}).get("win", False)]
    pnls = [r.get("outcome", {}).get("pnl_usd", 0) for r in records]
    pnl_pcts = [r.get("outcome", {}).get("pnl_pct", 0) for r in records]

    last_ts = max(r.get("timestamp", 0) for r in records)

    # Best iv_rank_threshold by win rate (min 3 trades)
    from collections import defaultdict
    iv_buckets: dict = defaultdict(list)
    for r in records:
        iv_val = r.get("params", {}).get("iv_rank_threshold")
        if iv_val is not None:
            iv_buckets[round(iv_val, 2)].append(r.get("outcome", {}).get("win", False))
    best_iv = None
    best_iv_wr = 0.0
    for iv_val, wins_list in iv_buckets.items():
        if len(wins_list) >= 3:
            wr = sum(wins_list) / len(wins_list)
            if wr > best_iv_wr:
                best_iv_wr = wr
                best_iv = iv_val

    level = "none" if n < 5 else "low" if n < 15 else "medium" if n < 30 else "high"

    return {
        "total_trades": n,
        "win_rate": round(len(wins) / n, 3) if n > 0 else 0,
        "avg_pnl_usd": round(sum(pnls) / n, 2) if n > 0 else 0,
        "avg_pnl_pct": round(sum(pnl_pcts) / n, 4) if n > 0 else 0,
        "best_iv_threshold": best_iv,
        "best_iv_win_rate": round(best_iv_wr, 3),
        "calibration_level": level,
        "last_updated": last_ts,
    }


# ── Worker function (runs in a subprocess) ────────────────────────────────────


def _run_backtest_worker(args: tuple[int, ParamSet, pd.DataFrame, list, dict, int]) -> dict:
    """
    Worker function executed in a separate process.
    Each worker runs one full backtest with the given ParamSet.

    args: (bot_id, params, ohlcv_df, iv_history, calibration, total_exp_records)
    calibration and total_exp_records are optional (default to empty/0).

    Returns a dict with the param set, results metrics, and fitness score.
    """
    bot_id, params, ohlcv_df, iv_history = args[0], args[1], args[2], args[3]

    # Patch config values for this run (each subprocess gets its own copy)
    # We do this by temporarily monkey-patching the cfg singleton
    try:
        # Import here to get a fresh module state in this process
        from config import cfg
        from backtester import Backtester, BacktestResults

        # Apply this genome's parameters to config
        cfg.strategy.iv_rank_threshold = params.iv_rank_threshold
        cfg.strategy.target_delta_min = params.target_delta_min
        cfg.strategy.target_delta_max = params.target_delta_max
        cfg.strategy.min_dte = int(params.min_dte)
        cfg.strategy.max_dte = int(params.max_dte)
        cfg.sizing.max_equity_per_leg = params.max_equity_per_leg
        cfg.sizing.min_free_equity_fraction = params.min_free_equity_fraction
        cfg.backtest.approx_otm_offset = params.approx_otm_offset
        cfg.backtest.premium_fraction_of_spot = params.premium_fraction_of_spot
        cfg.backtest.starting_equity = params.starting_equity

        # Run simulation (no network calls — data pre-fetched)
        bt = Backtester()
        bt._ohlcv_cache = ohlcv_df     # inject pre-fetched data
        bt._iv_cache = iv_history

        results = bt.run_with_data(ohlcv_df, iv_history, iv_window=int(params.iv_rank_window_days))
        score = fitness_score(results)

        # Blend with real experience data if calibration is available
        calibration = args[4] if len(args) > 4 else {}
        total_exp = args[5] if len(args) > 5 else 0
        if calibration:
            score = apply_experience_blend(score, asdict(params), calibration, total_exp)

        return {
            "bot_id": bot_id,
            "params": asdict(params),
            "fitness": score,
            "sharpe_ratio": results.sharpe_ratio,
            "total_return_pct": results.total_return_pct,
            "max_drawdown_pct": results.max_drawdown_pct,
            "win_rate_pct": results.win_rate_pct,
            "num_cycles": results.num_cycles,
            "ending_equity": results.ending_equity,
            "annualised_margin_roi": results.annualised_margin_roi,
            "premium_on_margin": results.premium_on_margin,
            "min_viable_capital": results.min_viable_capital,
            "avg_margin_utilization": results.avg_margin_utilization,
            "trades_per_year": results.trades_per_year,
            "avg_pnl_per_trade_usd": results.avg_pnl_per_trade_usd,
            "error": None,
        }
    except Exception as exc:
        return {
            "bot_id": bot_id,
            "params": asdict(params),
            "fitness": 0.0,
            "error": str(exc),
            **{k: 0.0 for k in ["sharpe_ratio", "total_return_pct", "max_drawdown_pct",
                                  "win_rate_pct", "num_cycles", "ending_equity",
                                  "annualised_margin_roi", "premium_on_margin",
                                  "min_viable_capital", "avg_margin_utilization",
                                  "trades_per_year", "avg_pnl_per_trade_usd"]},
        }


# ── Genetic operators ─────────────────────────────────────────────────────────


def _random_genome() -> ParamSet:
    """Generate a random parameter set within defined ranges."""
    p = ParamSet()
    for attr, (lo, hi, _) in PARAM_RANGES.items():
        val = random.uniform(lo, hi)
        if attr in ("max_dte", "min_dte", "iv_rank_window_days"):
            val = int(round(val))
        setattr(p, attr, round(val, 4))
    # Ensure delta_min < delta_max
    if p.target_delta_min >= p.target_delta_max:
        p.target_delta_max = p.target_delta_min + 0.05
    # Ensure min_dte < max_dte
    if p.min_dte >= p.max_dte:
        p.max_dte = p.min_dte + 7
    return p


def _mutate(genome: ParamSet, mutation_rate: float = 0.3) -> ParamSet:
    """
    Mutate a genome by randomly perturbing each parameter with probability
    mutation_rate. Perturbation is ±20% of the parameter's range.
    """
    child = copy.deepcopy(genome)
    for attr, (lo, hi, step) in PARAM_RANGES.items():
        if random.random() < mutation_rate:
            current = getattr(child, attr)
            delta = (hi - lo) * 0.20 * random.choice([-1, 1]) * random.random()
            new_val = float(np.clip(current + delta, lo, hi))
            if attr in ("max_dte", "min_dte", "iv_rank_window_days"):
                new_val = int(round(new_val))
            setattr(child, attr, round(new_val, 4))
    # Repair constraint violations
    if child.target_delta_min >= child.target_delta_max:
        child.target_delta_max = child.target_delta_min + 0.05
    if child.min_dte >= child.max_dte:
        child.max_dte = child.min_dte + 7
    return child


def _crossover(parent_a: ParamSet, parent_b: ParamSet) -> ParamSet:
    """
    Single-point crossover: randomly pick each parameter from either parent.
    """
    child = ParamSet()
    for attr in PARAM_RANGES:
        val = getattr(parent_a, attr) if random.random() < 0.5 else getattr(parent_b, attr)
        setattr(child, attr, val)
    if child.target_delta_min >= child.target_delta_max:
        child.target_delta_max = child.target_delta_min + 0.05
    if child.min_dte >= child.max_dte:
        child.max_dte = child.min_dte + 7
    return child


# ── Optimizer class ───────────────────────────────────────────────────────────


class Optimizer:
    """Runs multi-bot parameter sweeps and genetic evolution."""

    def __init__(self, workers: int | None = None) -> None:
        self._workers = workers or max(1, mp.cpu_count() - 1)
        self._ohlcv_df: pd.DataFrame | None = None
        self._iv_history: list | None = None
        self._pool: mp.Pool | None = None
        self._results_dir = Path("data/optimizer")
        self._results_dir.mkdir(parents=True, exist_ok=True)

    def _load_data(self) -> tuple[pd.DataFrame, list]:
        """Download and cache market data (shared across all workers)."""
        if self._ohlcv_df is not None:
            return self._ohlcv_df, self._iv_history

        logger.info("Downloading market data for optimizer (shared across all runs)...")
        from backtester import Backtester
        from config import cfg

        bt = Backtester()
        self._ohlcv_df = bt._fetch_prices()
        raw_iv = bt._rest._get("get_historical_volatility", {"currency": "BTC"})
        self._iv_history = raw_iv if raw_iv else []

        logger.info("Market data loaded and cached.")
        return self._ohlcv_df, self._iv_history

    def _run_parallel(self, genomes: list[ParamSet], calibration: dict | None = None) -> list[dict]:
        """Run a list of genomes in parallel using multiprocessing.

        Uses a persistent pool (created once, reused across all sweep/evolve batches)
        to avoid the heavy macOS spawn overhead of creating a new Pool per batch.
        """
        ohlcv_df, iv_history = self._load_data()

        # Load experience calibration (unless caller explicitly passed empty dict to skip)
        if calibration is None:
            exp_path = self._results_dir.parent / "experience.jsonl"
            calibration = load_experience_calibration(exp_path)
            total_exp = 0
            if exp_path.exists():
                with open(exp_path) as f:
                    total_exp = sum(1 for line in f if line.strip())
        else:
            total_exp = 0

        args = [(i, g, ohlcv_df, iv_history, calibration, total_exp) for i, g in enumerate(genomes)]

        logger.info(f"Running {len(genomes)} backtests on {self._workers} workers...")
        start = time.time()

        # Use a persistent pool to avoid spawn overhead on macOS (pool is created
        # once on first call and reused; closed explicitly in close_pool()).
        if self._pool is None:
            self._pool = mp.Pool(processes=self._workers)
        results = self._pool.map(_run_backtest_worker, args)

        elapsed = time.time() - start
        logger.info(f"Batch complete: {len(results)} results in {elapsed:.1f}s "
                    f"({elapsed/len(results):.1f}s avg)")
        return results

    def close_pool(self) -> None:
        """Shut down the persistent worker pool gracefully."""
        if self._pool is not None:
            self._pool.terminate()
            self._pool.join()
            self._pool = None

    # ── Sweep mode ────────────────────────────────────────────────────────────

    def run_sweep(self, target_param: str | None = None, use_experience: bool = True) -> None:
        """
        Vary each parameter individually across its full range.
        All other parameters remain at baseline (config.yaml defaults).

        If target_param is specified, only sweep that one parameter.
        Set use_experience=False to ignore experience.jsonl calibration.
        """
        from config import cfg

        baseline = ParamSet(
            iv_rank_threshold=cfg.strategy.iv_rank_threshold,
            target_delta_min=cfg.strategy.target_delta_min,
            target_delta_max=cfg.strategy.target_delta_max,
            approx_otm_offset=cfg.backtest.approx_otm_offset,
            max_dte=cfg.strategy.max_dte,
            min_dte=cfg.strategy.min_dte,
            max_equity_per_leg=cfg.sizing.max_equity_per_leg,
            premium_fraction_of_spot=cfg.backtest.premium_fraction_of_spot,
            min_free_equity_fraction=cfg.sizing.min_free_equity_fraction,
            starting_equity=cfg.backtest.starting_equity,
        )

        params_to_sweep = [target_param] if target_param else list(PARAM_RANGES.keys())
        all_sweep_results: dict[str, list[dict]] = {}

        for param_name in params_to_sweep:
            lo, hi, step = PARAM_RANGES[param_name]
            values = list(np.arange(lo, hi + step * 0.5, step))
            if param_name in ("max_dte", "min_dte", "iv_rank_window_days", "starting_equity"):
                values = [int(v) for v in values]

            logger.info(f"Sweeping '{param_name}' across {len(values)} values: {values[0]}→{values[-1]}")

            genomes: list[ParamSet] = []
            for val in values:
                g = copy.deepcopy(baseline)
                setattr(g, param_name, round(float(val), 4))
                # Repair
                if g.target_delta_min >= g.target_delta_max:
                    g.target_delta_max = g.target_delta_min + 0.05
                if g.min_dte >= g.max_dte:
                    g.max_dte = int(g.min_dte) + 7
                genomes.append(g)

            results = self._run_parallel(genomes, calibration={} if not use_experience else None)
            all_sweep_results[param_name] = results

            # Print per-param summary
            print(f"\n  SWEEP: {param_name}")
            print("  " + "─" * 65)
            header = f"  {'Value':>10}  {'Fitness':>7}  {'Return':>8}  {'Sharpe':>7}  {'WinRate':>8}  {'MaxDD':>7}"
            print(header)
            for res, val in zip(results, values):
                if res["error"]:
                    print(f"  {val:>10}  ERROR: {res['error']}")
                else:
                    print(
                        f"  {val:>10.4f}  {res['fitness']:>7.3f}  "
                        f"{res['total_return_pct']:>+7.1f}%  "
                        f"{res['sharpe_ratio']:>7.2f}  "
                        f"{res['win_rate_pct']:>7.1f}%  "
                        f"{res['max_drawdown_pct']:>6.1f}%"
                    )

            # Find the best value for this parameter
            best = max((r for r in results if not r["error"]), key=lambda r: r["fitness"], default=None)
            if best:
                best_val = best["params"][param_name]
                print(f"\n  ★ Best {param_name}: {best_val} (fitness={best['fitness']:.3f})")

        self._save_sweep_results(all_sweep_results)
        self._plot_sweep(all_sweep_results)
        self.close_pool()

    # ── Evolve mode ───────────────────────────────────────────────────────────

    def run_evolution(
        self,
        population_size: int = 20,
        generations: int = 8,
        elite_keep: int = 4,
        mutation_rate: float = 0.3,
        seed_from_sweep: bool = False,
        seed_genome: "ParamSet | None" = None,
        use_experience: bool = True,
        fitness_goal: str = "balanced",
    ) -> ParamSet:
        """
        Genetic algorithm over all parameters simultaneously.

        Each generation:
          1. Run all N genomes in parallel
          2. Score by fitness function
          3. Keep top elite_keep survivors
          4. Fill rest of population with crossover + mutation of survivors
          5. Repeat for K generations
          6. Return the best genome found

        If seed_from_sweep=True, reads sweep_results.json and seeds 30% of
        generation 0 with mutated copies of the best-per-parameter genome
        found by the sweep, giving evolution a head start.
        """
        logger.info(
            f"Starting genetic evolution: "
            f"pop={population_size}, gens={generations}, elite={elite_keep}"
        )

        # Generation 0: random population (optionally seeded from sweep)
        population = [_random_genome() for _ in range(population_size)]

        if seed_genome is not None:
            # Seed from a known genome: keep it as elite[0], fill 60% of pop with
            # tight mutations, keep 40% random for diversity.
            seed_count = max(1, int(population_size * 0.60))
            seeded = [_mutate(seed_genome, mutation_rate=0.15) for _ in range(seed_count - 1)]
            population = [seed_genome] + seeded + population[seed_count:]
            logger.info(
                f"Seeded generation 0 from config: 1 exact + {seed_count - 1} tight "
                f"mutations + {population_size - seed_count} random for diversity"
            )

        if seed_from_sweep:
            sweep_path = self._results_dir / "sweep_results.json"
            if sweep_path.exists():
                with open(sweep_path) as f:
                    sweep_data = json.load(f)

                # Find the best value for each parameter from the sweep
                seed_params: dict[str, Any] = {}
                for param_name, param_results in sweep_data.items():
                    valid = [r for r in param_results if not r.get("error")]
                    if valid:
                        best = max(valid, key=lambda r: r["fitness"])
                        seed_params[param_name] = best["params"][param_name]

                if seed_params:
                    # Build the seed genome from best-per-param values
                    seed_genome = ParamSet()
                    for attr, val in seed_params.items():
                        if hasattr(seed_genome, attr):
                            setattr(seed_genome, attr, val)
                    # Repair constraint violations
                    if seed_genome.target_delta_min >= seed_genome.target_delta_max:
                        seed_genome.target_delta_max = seed_genome.target_delta_min + 0.05
                    if seed_genome.min_dte >= seed_genome.max_dte:
                        seed_genome.max_dte = int(seed_genome.min_dte) + 7

                    # Replace 30% of population with lightly-mutated copies of seed
                    seed_count = max(1, int(population_size * 0.30))
                    seeded = [_mutate(seed_genome, mutation_rate=0.15) for _ in range(seed_count)]
                    population = seeded + population[seed_count:]
                    logger.info(
                        f"Seeded {seed_count}/{population_size} genomes from sweep "
                        f"best-per-parameter values"
                    )
            else:
                logger.warning(
                    "seed_from_sweep=True but sweep_results.json not found — "
                    "starting from random population"
                )

        all_generations: list[list[dict]] = []
        best_ever: dict | None = None
        _evo_start = time.time()

        for gen in range(1, generations + 1):
            logger.info(f"Generation {gen}/{generations}")
            results = self._run_parallel(population, calibration={} if not use_experience else None)

            # Attach genome to each result for tracking; re-score with goal fitness
            for res, genome in zip(results, population):
                res["generation"] = gen
                if not res.get("error"):
                    res["fitness"] = _fitness_for_goal(res, fitness_goal)

            # Sort by fitness
            valid = [r for r in results if not r["error"]]
            valid.sort(key=lambda r: r["fitness"], reverse=True)
            all_generations.append(valid)

            if valid:
                gen_best = valid[0]
                logger.info(
                    f"Gen {gen} best: fitness={gen_best['fitness']:.3f} | "
                    f"return={gen_best['total_return_pct']:+.1f}% | "
                    f"sharpe={gen_best['sharpe_ratio']:.2f} | "
                    f"maxDD={gen_best['max_drawdown_pct']:.1f}%"
                )
                if best_ever is None or gen_best["fitness"] > best_ever["fitness"]:
                    best_ever = gen_best

            # Write per-generation progress for dashboard polling
            try:
                _progress = {
                    "running": True,
                    "generation": gen,
                    "total_generations": generations,
                    "elapsed_sec": round(time.time() - _evo_start, 1),
                    "best_fitness": best_ever["fitness"] if best_ever else None,
                    "best_return_pct": best_ever["total_return_pct"] if best_ever else None,
                    "best_sharpe": best_ever["sharpe_ratio"] if best_ever else None,
                    "gen_best_fitness": valid[0]["fitness"] if valid else None,
                    "fitness_goal": fitness_goal,
                }
                _prog_path = self._results_dir / "evolution_progress.json"
                _prog_path.write_text(json.dumps(_progress))
            except Exception:
                pass

            # Print generation leaderboard
            print(f"\n  ═══ GENERATION {gen} LEADERBOARD ═══")
            print(f"  {'Rank':>4}  {'Fitness':>7}  {'Return':>8}  {'Sharpe':>7}  {'WinRate':>8}  {'MaxDD':>7}")
            for rank, res in enumerate(valid[:10], 1):
                print(
                    f"  {rank:>4}  {res['fitness']:>7.3f}  "
                    f"{res['total_return_pct']:>+7.1f}%  "
                    f"{res['sharpe_ratio']:>7.2f}  "
                    f"{res['win_rate_pct']:>7.1f}%  "
                    f"{res['max_drawdown_pct']:>6.1f}%"
                )

            if gen == generations:
                break

            # ── Next generation ────────────────────────────────────────────────
            # Keep elite survivors
            elite_params = [
                ParamSet(**valid[i]["params"])
                for i in range(min(elite_keep, len(valid)))
            ]

            new_population: list[ParamSet] = list(elite_params)

            # Fill with crossover + mutation
            while len(new_population) < population_size:
                if len(elite_params) >= 2:
                    p_a, p_b = random.sample(elite_params, 2)
                    child = _crossover(p_a, p_b)
                else:
                    child = copy.deepcopy(elite_params[0])
                child = _mutate(child, mutation_rate=mutation_rate)
                new_population.append(child)

            population = new_population

        # Final summary
        self._save_evolution_results(all_generations)
        self._plot_evolution(all_generations)
        try:
            _prog_path = self._results_dir / "evolution_progress.json"
            _prog_path.write_text(json.dumps({"running": False, "completed": True}))
        except Exception:
            pass

        if best_ever:
            print("\n  ★ BEST GENOME FOUND:")
            best_params = best_ever["params"]
            for k, v in best_params.items():
                print(f"    {k}: {v}")
            print(f"\n  Fitness: {best_ever['fitness']:.3f}")
            print(f"  Return:  {best_ever['total_return_pct']:+.1f}%")
            print(f"  Sharpe:  {best_ever['sharpe_ratio']:.2f}")
            print(f"  MaxDD:   {best_ever['max_drawdown_pct']:.1f}%")
            print(f"  WinRate: {best_ever['win_rate_pct']:.1f}%")
            self.close_pool()
            return ParamSet(**best_ever["params"])

        self.close_pool()
        return ParamSet()  # fallback to defaults

    # ── Output ────────────────────────────────────────────────────────────────

    def _save_sweep_results(self, results: dict[str, list[dict]]) -> None:
        path = self._results_dir / "sweep_results.json"
        with open(path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"Sweep results saved: {path}")

    def _save_evolution_results(self, generations: list[list[dict]]) -> None:
        path = self._results_dir / "evolution_results.json"
        with open(path, "w") as f:
            json.dump(generations, f, indent=2, default=str)
        logger.info(f"Evolution results saved: {path}")

        # Also save leaderboard CSV
        all_bots = [bot for gen in generations for bot in gen]
        all_bots.sort(key=lambda r: r["fitness"], reverse=True)
        csv_path = self._results_dir / "evolution_leaderboard.csv"
        if all_bots:
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=all_bots[0].keys())
                writer.writeheader()
                writer.writerows(all_bots)
        logger.info(f"Leaderboard CSV saved: {csv_path}")

    def _plot_sweep(self, results: dict[str, list[dict]]) -> None:
        """Plot sensitivity charts: fitness vs each parameter value."""
        n = len(results)
        if n == 0:
            return
        cols = min(3, n)
        rows = (n + cols - 1) // cols
        fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 4 * rows))
        fig.patch.set_facecolor("#0d1117")
        if n == 1:
            axes = [[axes]]
        elif rows == 1:
            axes = [axes]

        flat_axes = [ax for row in axes for ax in (row if hasattr(row, '__iter__') else [row])]

        for ax, (param_name, param_results) in zip(flat_axes, results.items()):
            valid = [r for r in param_results if not r["error"]]
            if not valid:
                ax.set_visible(False)
                continue
            xs = [r["params"][param_name] for r in valid]
            ys = [r["fitness"] for r in valid]
            ax.set_facecolor("#161b22")
            ax.plot(xs, ys, color="#58a6ff", linewidth=2, marker="o", markersize=4)
            best_idx = int(np.argmax(ys))
            ax.axvline(xs[best_idx], color="#3fb950", linestyle="--", alpha=0.7,
                       label=f"best={xs[best_idx]:.3f}")
            ax.set_title(param_name, color="white", fontsize=10)
            ax.set_ylabel("Fitness", color="white", fontsize=8)
            ax.tick_params(colors="white", labelsize=7)
            ax.spines[:].set_color("#30363d")
            ax.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="white", fontsize=7)

        for ax in flat_axes[n:]:
            ax.set_visible(False)

        fig.suptitle("Parameter Sensitivity Sweep", color="white", fontsize=14, y=1.02)
        plt.tight_layout()
        path = self._results_dir / "sweep_sensitivity.png"
        plt.savefig(path, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close()
        logger.info(f"Sweep chart saved: {path}")
        print(f"\n  Sensitivity chart saved → {path}")

    def _plot_evolution(self, generations: list[list[dict]]) -> None:
        """Plot fitness progression across generations."""
        if not generations:
            return
        gen_nums = list(range(1, len(generations) + 1))
        best_scores = [max(r["fitness"] for r in gen) if gen else 0 for gen in generations]
        avg_scores = [np.mean([r["fitness"] for r in gen]) if gen else 0 for gen in generations]

        fig, ax = plt.subplots(figsize=(10, 5))
        fig.patch.set_facecolor("#0d1117")
        ax.set_facecolor("#161b22")
        ax.plot(gen_nums, best_scores, color="#3fb950", linewidth=2, marker="o", label="Best fitness")
        ax.plot(gen_nums, avg_scores,  color="#58a6ff", linewidth=1.5, marker="s",
                linestyle="--", label="Mean fitness", alpha=0.7)
        ax.fill_between(gen_nums, avg_scores, best_scores, alpha=0.1, color="#3fb950")
        ax.set_xlabel("Generation", color="white")
        ax.set_ylabel("Fitness Score", color="white")
        ax.set_title("Genetic Evolution — Fitness Progression", color="white")
        ax.tick_params(colors="white")
        ax.spines[:].set_color("#30363d")
        ax.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="white")
        ax.set_xticks(gen_nums)

        path = self._results_dir / "evolution_progress.png"
        plt.tight_layout()
        plt.savefig(path, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close()
        logger.info(f"Evolution chart saved: {path}")
        print(f"  Evolution chart saved → {path}")


# ── Monte Carlo simulation ────────────────────────────────────────────────────


def run_monte_carlo(results_dir: Path, n_runs: int = 100, sim_months: int = 6) -> None:
    """
    Monte Carlo simulation: stress-test the best genome across many random
    market windows to understand the distribution of possible outcomes.

    Method
    ------
    1. Load best genome (from evolve mode).
    2. Fetch all available price history (~24 months including IV warmup).
    3. Run N backtests, each starting at a different random date within the
       available history.  Each window covers `sim_months` of trading.
    4. Collect the distribution of fitness, return, Sharpe, drawdown, win rate.
    5. Report p5/p25/p50/p75/p95 percentiles and save to
       data/optimizer/monte_carlo_results.json.
    """
    import yaml

    logger.info(f"=== Monte Carlo Simulation ({n_runs} runs × {sim_months}m windows) ===")

    best_genome_path = results_dir / "best_genome.yaml"
    if not best_genome_path.exists():
        logger.error("No best_genome.yaml found. Run 'evolve' mode first.")
        print("\n  ERROR: Run the Evolve optimizer first — Monte Carlo needs a best genome to test.")
        return

    with open(best_genome_path) as f:
        genome_dict = yaml.safe_load(f)

    params = ParamSet(**{k: v for k, v in genome_dict.items() if k in ParamSet.__dataclass_fields__})

    from backtester import Backtester
    from config import cfg

    # Patch config
    cfg.strategy.iv_rank_threshold       = params.iv_rank_threshold
    cfg.strategy.target_delta_min        = params.target_delta_min
    cfg.strategy.target_delta_max        = params.target_delta_max
    cfg.strategy.min_dte                 = int(params.min_dte)
    cfg.strategy.max_dte                 = int(params.max_dte)
    cfg.sizing.max_equity_per_leg        = params.max_equity_per_leg
    cfg.sizing.min_free_equity_fraction  = params.min_free_equity_fraction
    cfg.backtest.approx_otm_offset       = params.approx_otm_offset
    cfg.backtest.premium_fraction_of_spot = params.premium_fraction_of_spot
    cfg.backtest.starting_equity         = params.starting_equity

    # Fetch data once
    logger.info("Fetching market data...")
    bt0 = Backtester()
    ohlcv_full = bt0._fetch_prices()
    raw_iv = bt0._rest._get("get_historical_volatility", {"currency": "BTC"})
    iv_history = raw_iv if raw_iv else []
    iv_window = int(params.iv_rank_window_days)

    # The full dataset has 12m simulation + 12m IV warmup prefix.
    # Identify the usable simulation rows (last 12 months).
    sim_days = sim_months * 30
    warmup_rows = 380  # rows reserved for IV rank warmup

    # We can start any window that has at least sim_days rows after it
    max_start_idx = len(ohlcv_full) - sim_days - 1
    min_start_idx = warmup_rows  # need warmup before any simulation

    if max_start_idx <= min_start_idx:
        logger.error("Not enough data for Monte Carlo windows.")
        print("\n  ERROR: Not enough price history for Monte Carlo. Need at least 18 months of data.")
        return

    rng = random.Random(42)
    start_indices = [rng.randint(min_start_idx, max_start_idx) for _ in range(n_runs)]

    results_list = []
    for i, start_idx in enumerate(start_indices):
        end_idx = start_idx + sim_days
        # Include warmup prefix for IV rank calculation
        warmup_start = max(0, start_idx - warmup_rows)
        window_df = ohlcv_full.iloc[warmup_start: end_idx].reset_index(drop=True)

        try:
            bt = Backtester()
            r = bt.run_with_data(window_df, iv_history, iv_window=iv_window)
            results_list.append({
                "run":          i + 1,
                "start_date":   str(ohlcv_full.iloc[start_idx]["date"].date()),
                "end_date":     str(ohlcv_full.iloc[end_idx - 1]["date"].date()),
                "fitness":      fitness_score(r),
                "sharpe":       round(r.sharpe_ratio, 3),
                "return_pct":   round(r.total_return_pct, 2),
                "win_rate":     round(r.win_rate_pct, 1),
                "max_drawdown": round(r.max_drawdown_pct, 2),
                "num_cycles":   r.num_cycles,
            })
        except Exception as e:
            logger.debug(f"Run {i+1} failed: {e}")
            continue

        if (i + 1) % 10 == 0:
            logger.info(f"  {i+1}/{n_runs} runs complete...")

    if not results_list:
        logger.error("All Monte Carlo runs failed.")
        return

    def pct(arr, p):
        return round(float(np.percentile(arr, p)), 3)

    for metric in ["fitness", "sharpe", "return_pct", "win_rate", "max_drawdown"]:
        vals = [r[metric] for r in results_list]
        arr = np.array(vals)
        logger.info(
            f"  {metric:15s}: p5={pct(arr,5):6.2f}  p25={pct(arr,25):6.2f}  "
            f"p50={pct(arr,50):6.2f}  p75={pct(arr,75):6.2f}  p95={pct(arr,95):6.2f}"
        )

    def dist(metric):
        vals = np.array([r[metric] for r in results_list])
        return {
            "p5":  pct(vals, 5),  "p25": pct(vals, 25), "p50": pct(vals, 50),
            "p75": pct(vals, 75), "p95": pct(vals, 95),
            "mean": round(float(vals.mean()), 3),
            "std":  round(float(vals.std()),  3),
        }

    positive_returns = sum(1 for r in results_list if r["return_pct"] > 0)
    prob_profit = round(positive_returns / len(results_list) * 100, 1)

    output = {
        "timestamp":     datetime.utcnow().isoformat(),
        "n_runs":        len(results_list),
        "sim_months":    sim_months,
        "genome":        genome_dict,
        "prob_profit_pct": prob_profit,
        "distributions": {
            "fitness":      dist("fitness"),
            "sharpe":       dist("sharpe"),
            "return_pct":   dist("return_pct"),
            "win_rate":     dist("win_rate"),
            "max_drawdown": dist("max_drawdown"),
            "num_cycles":   dist("num_cycles"),
        },
        "runs": results_list,
    }

    out_path = results_dir / "monte_carlo_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(f"\n  Monte Carlo complete: {len(results_list)} runs.")
    logger.info(f"  Probability of profit: {prob_profit}%")
    logger.info(f"  Median return: {dist('return_pct')['p50']}%")
    logger.info(f"  Results saved → {out_path}")
    print(f"\n  Monte Carlo complete. Probability of profit: {prob_profit}%. "
          f"Median return: {dist('return_pct')['p50']}%")


# ── Walk-forward validation ───────────────────────────────────────────────────


def run_walk_forward(results_dir: Path, workers: int | None = None) -> None:
    """
    Walk-forward validation: test whether the optimised parameters generalise
    to unseen data.

    Method
    ------
    1. Fetch full price history (same data the sweep/evolve used).
    2. Split the simulation period 75 / 25: in-sample (IS) vs out-of-sample (OOS).
       - IS  = first 75% of simulation days → used for optimisation (already done).
       - OOS = last 25% of simulation days  → never seen during optimisation.
    3. Load the best genome from `data/optimizer/best_genome.yaml` (produced by
       evolve mode) — these are the IS-optimised parameters.
    4. Run a fresh backtest of those parameters on the OOS slice.
    5. Also run the same parameters on the full period so the comparison is fair.
    6. Compute robustness score = OOS fitness / IS fitness.
       - > 0.8  → strong (strategy holds up on unseen data)
       - 0.5–0.8 → acceptable
       - < 0.5  → over-fitted (good on paper, risky in practice)
    7. Save results to `data/optimizer/walk_forward_results.json`.
    """
    import yaml
    from backtester import Backtester, BacktestResults
    from config import cfg, Config

    logger.info("=== Walk-Forward Validation ===")

    # ── Load best genome ───────────────────────────────────────────────────────
    best_genome_path = results_dir / "best_genome.yaml"
    if not best_genome_path.exists():
        logger.error(
            "No best_genome.yaml found. Run 'evolve' mode first to produce one."
        )
        print("\n  ERROR: Run the Evolve optimizer first — walk-forward needs a best genome to test.")
        return

    with open(best_genome_path) as f:
        genome_dict = yaml.safe_load(f)

    logger.info(f"Loaded best genome from {best_genome_path}")
    params = ParamSet(**{k: v for k, v in genome_dict.items() if k in ParamSet.__dataclass_fields__})

    # ── Patch config with genome params ───────────────────────────────────────
    cfg.strategy.iv_rank_threshold    = params.iv_rank_threshold
    cfg.strategy.target_delta_min     = params.target_delta_min
    cfg.strategy.target_delta_max     = params.target_delta_max
    cfg.strategy.min_dte              = int(params.min_dte)
    cfg.strategy.max_dte              = int(params.max_dte)
    cfg.sizing.max_equity_per_leg     = params.max_equity_per_leg
    cfg.sizing.min_free_equity_fraction = params.min_free_equity_fraction
    cfg.backtest.approx_otm_offset    = params.approx_otm_offset
    cfg.backtest.premium_fraction_of_spot = params.premium_fraction_of_spot
    cfg.backtest.starting_equity      = params.starting_equity

    # ── Fetch data ─────────────────────────────────────────────────────────────
    logger.info("Fetching market data...")
    bt = Backtester()
    ohlcv_full = bt._fetch_prices()
    raw_iv = bt._rest._get("get_historical_volatility", {"currency": "BTC"})
    iv_history = raw_iv if raw_iv else []
    iv_window = int(params.iv_rank_window_days)

    # Determine simulation period (strip the 12m IV warm-up prefix the fetcher adds)
    sim_months = cfg.backtest.lookback_months  # typically 12
    cutoff_days = sim_months * 30
    sim_start_idx = max(0, len(ohlcv_full) - cutoff_days - 380)  # 380 = IV warm-up buffer
    sim_df = ohlcv_full.iloc[sim_start_idx:].reset_index(drop=True)

    # Split: IS = first 75%, OOS = last 25%
    split_idx = int(len(sim_df) * 0.75)
    # Keep the IV warm-up prefix for each slice so IV rank can be computed
    warmup = min(380, sim_start_idx)
    warmup_df = ohlcv_full.iloc[max(0, sim_start_idx - warmup):sim_start_idx]

    is_df  = pd.concat([warmup_df, sim_df.iloc[:split_idx]], ignore_index=True)
    oos_df = pd.concat([warmup_df, sim_df.iloc[split_idx:]], ignore_index=True)

    is_start  = sim_df.iloc[0]["date"].date()
    is_end    = sim_df.iloc[split_idx - 1]["date"].date()
    oos_start = sim_df.iloc[split_idx]["date"].date()
    oos_end   = sim_df.iloc[-1]["date"].date()

    logger.info(f"IS  period: {is_start} → {is_end} ({split_idx} days)")
    logger.info(f"OOS period: {oos_start} → {oos_end} ({len(sim_df) - split_idx} days)")

    # ── Run IS backtest ────────────────────────────────────────────────────────
    logger.info("Running IS backtest...")
    bt_is = Backtester()
    is_results = bt_is.run_with_data(is_df, iv_history, iv_window=iv_window)
    is_fitness = fitness_score(is_results)
    logger.info(f"IS  fitness={is_fitness:.4f}  sharpe={is_results.sharpe_ratio:.2f}  "
                f"return={is_results.total_return_pct:.1f}%  win={is_results.win_rate_pct:.1f}%")

    # ── Run OOS backtest ───────────────────────────────────────────────────────
    logger.info("Running OOS backtest...")
    bt_oos = Backtester()
    oos_results = bt_oos.run_with_data(oos_df, iv_history, iv_window=iv_window)
    oos_fitness = fitness_score(oos_results)
    logger.info(f"OOS fitness={oos_fitness:.4f}  sharpe={oos_results.sharpe_ratio:.2f}  "
                f"return={oos_results.total_return_pct:.1f}%  win={oos_results.win_rate_pct:.1f}%")

    # ── Robustness score ───────────────────────────────────────────────────────
    robustness = round(oos_fitness / is_fitness, 4) if is_fitness > 0 else 0.0
    if robustness >= 0.8:
        verdict = "STRONG — strategy holds up on unseen data"
    elif robustness >= 0.5:
        verdict = "ACCEPTABLE — some degradation on unseen data"
    else:
        verdict = "OVER-FITTED — parameters may not generalise to live trading"

    logger.info(f"\n  Robustness score: {robustness:.2f} ({robustness*100:.0f}%)")
    logger.info(f"  Verdict: {verdict}")

    # ── Save results ───────────────────────────────────────────────────────────
    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "genome": genome_dict,
        "split": {
            "is_start":   str(is_start),
            "is_end":     str(is_end),
            "is_days":    split_idx,
            "oos_start":  str(oos_start),
            "oos_end":    str(oos_end),
            "oos_days":   len(sim_df) - split_idx,
        },
        "in_sample": {
            "fitness":      is_fitness,
            "sharpe":       round(is_results.sharpe_ratio, 3),
            "return_pct":   round(is_results.total_return_pct, 2),
            "win_rate":     round(is_results.win_rate_pct, 1),
            "max_drawdown": round(is_results.max_drawdown_pct, 2),
            "num_cycles":   is_results.num_cycles,
        },
        "out_of_sample": {
            "fitness":      oos_fitness,
            "sharpe":       round(oos_results.sharpe_ratio, 3),
            "return_pct":   round(oos_results.total_return_pct, 2),
            "win_rate":     round(oos_results.win_rate_pct, 1),
            "max_drawdown": round(oos_results.max_drawdown_pct, 2),
            "num_cycles":   oos_results.num_cycles,
        },
        "robustness_score": robustness,
        "verdict": verdict,
    }

    out_path = results_dir / "walk_forward_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(f"\n  Results saved → {out_path}")
    print(f"\n  Walk-forward complete. Robustness: {robustness:.2f} — {verdict}")


# ── Reconciliation ────────────────────────────────────────────────────────────


def _parse_instrument_expiry(instrument_name: str) -> "datetime | None":
    """Parse expiry date from a Deribit instrument name like BTC-25APR25-90000-P."""
    try:
        from datetime import timezone as _tz
        parts = instrument_name.split("-")
        return datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=_tz.utc)
    except Exception:
        return None


def run_reconcile(results_dir: Path) -> None:
    """
    Reconcile backtester predictions against actual paper/live trading results.

    Method
    ------
    1. Load actual closed trades from data/trades.csv.
    2. Fetch historical BTC price and implied-vol data for each trade's entry date.
    3. For each trade, run Black-Scholes to predict what the premium *should* have
       been at entry, and compare it to the actual premium collected.
    4. Also run a mini-backtest over the actual trading date range (using the current
       best genome) and compare aggregate stats to what actually happened.
    5. Compute metrics: premium RMSE, premium bias, win-rate accuracy, overall accuracy.
    6. Save results to data/optimizer/reconcile_results.json.

    Outputs used by the mobile API /optimizer/summary endpoint:
        metrics.accuracy          — overall model accuracy score (0-1)
        metrics.premium_rmse_usd  — RMS premium-prediction error per contract (USD)
        metrics.premium_bias_usd  — mean premium over/under-estimation (USD, + = overestimates)
    """
    import csv as _csv
    from datetime import timezone as _tz
    from backtester import Backtester, bs_put_price, bs_call_price
    from config import cfg

    logger.info("=== Reconciliation: Backtest vs Actual Trades ===")

    trades_path = results_dir.parent / "trades.csv"
    if not trades_path.exists():
        print(
            "\n  No trades.csv found. Run the bot in paper or live mode first "
            "to collect at least 3 completed trades, then retry."
        )
        return

    # ── Load completed trades ──────────────────────────────────────────────────
    all_trades = []
    with open(trades_path, newline="") as f:
        for row in _csv.DictReader(f):
            # Only settle/force-close counts as a completed cycle
            if row.get("reason") in ("expiry_settlement", "mobile_force_close"):
                all_trades.append(row)

    if len(all_trades) < 3:
        print(
            f"\n  Only {len(all_trades)} completed trade(s) found in trades.csv. "
            "Need at least 3 for a meaningful reconciliation."
        )
        return

    logger.info(f"  {len(all_trades)} completed trades loaded from {trades_path}")

    # ── Fetch historical market data ───────────────────────────────────────────
    logger.info("Fetching historical market data...")
    bt = Backtester()
    ohlcv_full = bt._fetch_prices()

    raw_iv = bt._rest._get("get_historical_volatility", {"currency": "BTC"})
    if raw_iv and len(raw_iv) >= 60:
        _iv_raw = pd.DataFrame(raw_iv, columns=["ts_ms", "iv"])
        _iv_raw["date"] = pd.to_datetime(_iv_raw["ts_ms"], unit="ms", utc=True).dt.normalize()
        iv_df = _iv_raw.sort_values("date").drop_duplicates("date")[["date", "iv"]]
        if len(iv_df) < 30:
            iv_df = bt._synthesise_iv(ohlcv_full)
    else:
        iv_df = bt._synthesise_iv(ohlcv_full)

    # Build O(1) date lookups: date → spot / IV
    ohlcv_by_date: dict = {
        row["date"].date(): float(row["close"])
        for _, row in ohlcv_full.iterrows()
    }
    iv_by_date: dict = {
        row["date"].date(): float(row["iv"])
        for _, row in iv_df.iterrows()
    }

    def _nearest_value(lookup: dict, target_date) -> float | None:
        """Return the closest available value within ±5 days."""
        for delta in range(6):
            for sign in (0, 1, -1):
                d = target_date + timedelta(days=delta * sign)
                if d in lookup:
                    return lookup[d]
        return None

    # ── Trade-level BS reconciliation ──────────────────────────────────────────
    r_free = cfg.backtest.risk_free_rate
    comparisons: list[dict] = []
    win_pred_correct = 0

    for row in all_trades:
        try:
            instrument = row.get("instrument", "")
            dte_at_entry = int(row.get("dte_at_entry", 7))
            opt_type = row.get("option_type", "put")
            strike = float(row.get("strike", 0))
            contracts = float(row.get("contracts", 0.1))
            entry_price_btc = float(row.get("entry_price", 0))
            spot_at_close = float(row.get("btc_price", 0))
            actual_pnl_usd = float(row.get("pnl_usd", 0))

            if not strike or not entry_price_btc or not spot_at_close:
                continue

            # Derive entry date: expiry_date − dte_at_entry
            expiry_dt = _parse_instrument_expiry(instrument)
            if expiry_dt is None:
                continue
            entry_date = (expiry_dt - timedelta(days=dte_at_entry)).date()

            spot_at_entry = _nearest_value(ohlcv_by_date, entry_date)
            iv_at_entry   = _nearest_value(iv_by_date, entry_date)
            if not spot_at_entry or not iv_at_entry:
                continue

            # BS-predicted premium (USD per contract per BTC underlying)
            T = max(dte_at_entry / 365.0, 1e-8)
            sigma = iv_at_entry / 100.0
            if opt_type == "put":
                bs_usd_per_unit = bs_put_price(spot_at_entry, strike, T, r_free, sigma)
            else:
                bs_usd_per_unit = bs_call_price(spot_at_entry, strike, T, r_free, sigma)
            predicted_premium_usd = bs_usd_per_unit * contracts

            # Actual premium collected: entry_price_btc × spot_at_entry × contracts
            # (entry_price_btc is in BTC per 1 BTC underlying notional)
            actual_premium_usd = entry_price_btc * spot_at_entry * contracts

            premium_error_usd = predicted_premium_usd - actual_premium_usd

            # Win/loss prediction: option expires worthless (OTM) if spot moved away from strike
            if opt_type == "put":
                model_win = spot_at_close >= strike   # put OTM if spot above strike
            else:
                model_win = spot_at_close <= strike   # call OTM if spot below strike
            actual_win = actual_pnl_usd > 0
            if model_win == actual_win:
                win_pred_correct += 1

            comparisons.append({
                "instrument":            instrument,
                "entry_date":            str(entry_date),
                "close_date":            str(expiry_dt.date()),
                "option_type":           opt_type,
                "strike":                round(strike, 0),
                "spot_at_entry":         round(spot_at_entry, 0),
                "spot_at_close":         round(spot_at_close, 0),
                "iv_at_entry_pct":       round(iv_at_entry, 2),
                "predicted_premium_usd": round(predicted_premium_usd, 2),
                "actual_premium_usd":    round(actual_premium_usd, 2),
                "premium_error_usd":     round(premium_error_usd, 2),
                "premium_error_pct":     round(
                    premium_error_usd / actual_premium_usd * 100 if actual_premium_usd else 0, 1
                ),
                "model_win":             model_win,
                "actual_win":            actual_win,
                "actual_pnl_usd":        round(actual_pnl_usd, 2),
            })
        except Exception as exc:
            logger.debug(f"Reconcile: skipped {row.get('instrument', '?')}: {exc}")
            continue

    if not comparisons:
        print("\n  Could not match any trades to historical data (date range may be out of scope).")
        return

    n = len(comparisons)
    errors = np.array([c["premium_error_usd"] for c in comparisons])
    premium_rmse      = float(np.sqrt(np.mean(errors ** 2)))
    premium_bias      = float(np.mean(errors))
    win_accuracy      = win_pred_correct / n

    # Premium accuracy: penalise large relative errors
    rel_errors = np.array([abs(c["premium_error_pct"]) for c in comparisons])
    premium_accuracy  = float(np.clip(1.0 - np.mean(rel_errors) / 100.0, 0.0, 1.0))

    # Overall accuracy: 50% win prediction + 50% premium accuracy
    overall_accuracy  = round(0.5 * win_accuracy + 0.5 * premium_accuracy, 4)

    logger.info(f"\n  Trade-level results ({n} trades matched):")
    logger.info(f"    Win-rate prediction accuracy : {win_accuracy:.1%}")
    logger.info(f"    Premium RMSE                 : ${premium_rmse:,.2f}")
    logger.info(f"    Premium bias                 : ${premium_bias:+,.2f}  "
                f"({'overestimates' if premium_bias > 0 else 'underestimates'} premiums)")
    logger.info(f"    Premium accuracy             : {premium_accuracy:.1%}")
    logger.info(f"    Overall accuracy             : {overall_accuracy:.1%}")

    # ── Aggregate backtest comparison ──────────────────────────────────────────
    # Run the current best genome backtest over the same date span as actual trades
    backtest_comparison: dict = {}
    best_genome_path = results_dir / "best_genome.yaml"
    if best_genome_path.exists() and len(comparisons) >= 1:
        try:
            import yaml
            genome_dict = yaml.safe_load(best_genome_path.open())
            params = ParamSet(**{k: v for k, v in genome_dict.items()
                                 if k in ParamSet.__dataclass_fields__})

            cfg.strategy.iv_rank_threshold      = params.iv_rank_threshold
            cfg.strategy.target_delta_min       = params.target_delta_min
            cfg.strategy.target_delta_max       = params.target_delta_max
            cfg.strategy.min_dte                = int(params.min_dte)
            cfg.strategy.max_dte                = int(params.max_dte)
            cfg.sizing.max_equity_per_leg       = params.max_equity_per_leg
            cfg.sizing.min_free_equity_fraction = params.min_free_equity_fraction
            cfg.backtest.approx_otm_offset      = params.approx_otm_offset
            cfg.backtest.premium_fraction_of_spot = params.premium_fraction_of_spot
            cfg.backtest.starting_equity        = params.starting_equity

            # Trim OHLCV to the actual trading window (±30 days warmup)
            first_entry = min(
                (_parse_instrument_expiry(c["instrument"]) - timedelta(days=int(row["dte_at_entry"])))
                for c, row in zip(comparisons, all_trades) if _parse_instrument_expiry(c["instrument"])
            )
            warmup_start = first_entry - timedelta(days=400)
            mask = ohlcv_full["date"] >= pd.Timestamp(warmup_start)
            bt_slice = ohlcv_full[mask].reset_index(drop=True)

            raw_iv_list = raw_iv if (raw_iv and len(raw_iv) >= 60) else []
            iv_window = int(params.iv_rank_window_days)
            bt_compare = Backtester()
            bt_r = bt_compare.run_with_data(bt_slice, raw_iv_list, iv_window=iv_window)

            # Actual aggregate stats from trades
            actual_wins    = sum(1 for c in comparisons if c["actual_win"])
            actual_wr      = actual_wins / n * 100
            actual_pnl_sum = sum(c["actual_pnl_usd"] for c in comparisons)

            backtest_comparison = {
                "backtest_win_rate_pct":    bt_r.win_rate_pct,
                "actual_win_rate_pct":      round(actual_wr, 1),
                "win_rate_delta_pp":        round(bt_r.win_rate_pct - actual_wr, 1),
                "backtest_return_pct":      bt_r.total_return_pct,
                "backtest_sharpe":          bt_r.sharpe_ratio,
                "actual_total_pnl_usd":     round(actual_pnl_sum, 2),
                "actual_trade_count":       n,
                "backtest_trade_count":     bt_r.num_cycles,
            }

            logger.info(
                f"\n  Aggregate comparison:"
                f"\n    Backtest win rate : {bt_r.win_rate_pct:.1f}%  |  "
                f"Actual : {actual_wr:.1f}%  "
                f"(Δ {backtest_comparison['win_rate_delta_pp']:+.1f}pp)"
                f"\n    Actual total P&L  : ${actual_pnl_sum:+,.2f} over {n} trades"
            )
        except Exception as exc:
            logger.warning(f"Aggregate comparison failed: {exc}")

    # ── Save results ───────────────────────────────────────────────────────────
    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "trade_count": n,
        "metrics": {
            "accuracy":          overall_accuracy,
            "win_accuracy":      round(win_accuracy, 4),
            "premium_accuracy":  round(premium_accuracy, 4),
            "premium_rmse_usd":  round(premium_rmse, 2),
            "premium_bias_usd":  round(premium_bias, 2),
        },
        "backtest_vs_actual": backtest_comparison,
        "trades": comparisons,
    }

    out_path = results_dir / "reconcile_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    logger.info(f"\n  Results saved → {out_path}")
    print(
        f"\n  Reconciliation complete: {n} trades analysed."
        f"\n  Overall accuracy: {overall_accuracy:.1%} | "
        f"Premium RMSE: ${premium_rmse:,.2f} | "
        f"Bias: ${premium_bias:+,.2f}"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTC Wheel Bot — Parameter Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python optimizer.py --mode sweep                 # sweep all params
  python optimizer.py --mode sweep --param iv_rank_threshold
  python optimizer.py --mode evolve --pop 20 --gen 8
        """,
    )
    parser.add_argument("--mode", choices=["sweep", "evolve", "walk_forward", "monte_carlo", "reconcile"], default="sweep")
    parser.add_argument("--param", type=str, default=None,
                        help="For sweep mode: which parameter to sweep (default: all)")
    parser.add_argument("--population", "--pop", dest="population", type=int, default=20,
                        help="Population size (evolve mode)")
    parser.add_argument("--generations", "--gen", dest="generations", type=int, default=8,
                        help="Number of generations (evolve mode)")
    parser.add_argument("--elite", type=int, default=4,
                        help="Number of elite survivors to keep (evolve mode)")
    parser.add_argument("--mutation", type=float, default=0.3,
                        help="Mutation rate 0.0–1.0 (evolve mode)")
    parser.add_argument("--fitness-goal", dest="fitness_goal",
                        choices=list(EVOLVE_GOALS), default="balanced",
                        help="Fitness objective for evolution (default: balanced)")
    parser.add_argument("--seed-from-sweep", action="store_true", default=False,
                        help="Seed 30%% of gen-0 population from sweep best-per-param values")
    parser.add_argument("--no-experience", action="store_true", default=False,
                        help="Ignore experience.jsonl calibration (use pure backtest fitness)")
    parser.add_argument("--workers", type=int, default=None, help="Parallel worker processes")
    parser.add_argument("--seed-config", dest="seed_config", type=str, default=None,
                        help="Named config to seed evolution from (e.g. 'balanced_20260423_2346')")
    args = parser.parse_args()

    # Setup minimal logging
    import sys
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

    opt = Optimizer(workers=args.workers)

    use_exp = not args.no_experience

    if args.mode == "sweep":
        if args.param and args.param not in PARAM_RANGES:
            print(f"Unknown parameter '{args.param}'. Valid options: {list(PARAM_RANGES.keys())}")
            return
        opt.run_sweep(target_param=args.param, use_experience=use_exp)

    elif args.mode == "evolve":
        goal = args.fitness_goal

        # Load seed genome from named config if provided
        _seed_genome: "ParamSet | None" = None
        _seed_config_name: str | None = args.seed_config
        if _seed_config_name:
            try:
                from config_store import load_config_by_name as _load_cfg
                _raw = _load_cfg(_seed_config_name)
                _strat   = _raw.get("strategy", {})
                _sizing  = _raw.get("sizing", {})
                _bt      = _raw.get("backtest", {})
                _seed_genome = ParamSet(
                    iv_rank_threshold        = float(_strat.get("iv_rank_threshold",        0.50)),
                    target_delta_min         = float(_strat.get("target_delta_min",          0.15)),
                    target_delta_max         = float(_strat.get("target_delta_max",          0.30)),
                    max_dte                  = float(_strat.get("max_dte",                   35)),
                    min_dte                  = float(_strat.get("min_dte",                   5)),
                    approx_otm_offset        = float(_bt.get("approx_otm_offset",            0.08)),
                    max_equity_per_leg       = float(_sizing.get("max_equity_per_leg",       0.05)),
                    premium_fraction_of_spot = float(_bt.get("premium_fraction_of_spot",     0.015)),
                    min_free_equity_fraction = float(_sizing.get("min_free_equity_fraction", 0.25)),
                    starting_equity          = float(_bt.get("starting_equity",              10000.0)),
                )
                logger.info(f"Seeding evolution from config: '{_seed_config_name}'")
            except Exception as _seed_exc:
                logger.warning(f"Could not load seed config '{_seed_config_name}': {_seed_exc} — starting from random")
                _seed_genome = None

        best = opt.run_evolution(
            population_size=args.population,
            generations=args.generations,
            elite_keep=args.elite,
            mutation_rate=args.mutation,
            seed_from_sweep=args.seed_from_sweep,
            seed_genome=_seed_genome,
            use_experience=use_exp,
            fitness_goal=goal,
        )
        import yaml
        import csv as _csv_hist
        from datetime import datetime as _dt_hist, timezone as _tz_hist
        genome_dict = asdict(best)
        genome_dict["fitness_goal"] = goal
        # Save to goal-specific file and keep best_genome.yaml for backwards compat
        out_dir = Path("data/optimizer")
        out_dir.mkdir(parents=True, exist_ok=True)
        goal_path = out_dir / f"best_genome_{goal}.yaml"
        generic_path = out_dir / "best_genome.yaml"
        for path in (goal_path, generic_path):
            with open(path, "w") as f:
                yaml.dump(genome_dict, f, default_flow_style=False)
        print(f"\n  Best genome saved to {goal_path} (and {generic_path})")

        # ── Append to per-goal version history ───────────────────────────────
        # Pull metrics from the leaderboard CSV that was just saved
        best_metrics: dict = {}
        lb_path = out_dir / "evolution_leaderboard.csv"
        try:
            if lb_path.exists():
                with open(lb_path, newline="") as _lf:
                    _reader = _csv_hist.DictReader(_lf)
                    _top = next(_reader, None)
                    if _top:
                        best_metrics = {
                            "fitness":              round(float(_top.get("fitness", 0)), 4),
                            "return_pct":           round(float(_top.get("total_return_pct", 0)), 2),
                            "sharpe":               round(float(_top.get("sharpe_ratio", 0)), 3),
                            "win_rate":             round(float(_top.get("win_rate_pct", 0)), 1),
                            "drawdown":             round(float(_top.get("max_drawdown_pct", 0)), 2),
                            "num_cycles":           int(float(_top.get("num_cycles", 0))),
                            "trades_per_year":      round(float(_top.get("trades_per_year", 0)), 1),
                            "avg_pnl_per_trade_usd": round(float(_top.get("avg_pnl_per_trade_usd", 0)), 2),
                            # Capital-efficiency metrics — surfaced in the
                            # Pipeline UI to help users pick small-capital
                            # high-margin-ROI configs (the user's stated thesis).
                            "annualised_margin_roi": round(float(_top.get("annualised_margin_roi", 0)), 4),
                            "premium_on_margin":     round(float(_top.get("premium_on_margin", 0)), 4),
                            "min_viable_capital":    round(float(_top.get("min_viable_capital", 0)), 2),
                            "avg_margin_utilization": round(float(_top.get("avg_margin_utilization", 0)), 4),
                        }
        except Exception:
            pass

        history_path = out_dir / f"evolve_history_{goal}.json"
        _history: list = []
        try:
            if history_path.exists():
                with open(history_path) as _hf:
                    _history = json.load(_hf)
        except Exception:
            pass

        _entry = {
            "version":   len(_history) + 1,
            "timestamp": _dt_hist.now(_tz_hist.utc).isoformat(),
            "goal":      goal,
            **best_metrics,
        }
        _history.append(_entry)
        with open(history_path, "w") as _hf:
            json.dump(_history, _hf, indent=2)
        print(f"  History saved → {history_path} (v{_entry['version']})")

        # ── Save to named config store ────────────────────────────────────────
        try:
            from config_store import save_config as _cs_save, genome_to_params as _g2p
            _ts_slug = _dt_hist.now(_tz_hist.utc).strftime("%Y%m%d_%H%M")
            _cfg_name = f"{goal}_{_ts_slug}"
            _cfg_params = _g2p(genome_dict)
            _cs_save(
                name=_cfg_name,
                params=_cfg_params,
                source="evolved",
                metadata={
                    "fitness":         best_metrics.get("fitness"),
                    "goal":            goal,
                    "generations":     args.generations,
                    "total_return":    best_metrics.get("return_pct"),
                    "sharpe":          best_metrics.get("sharpe"),
                    "win_rate":        best_metrics.get("win_rate"),
                    "drawdown":        best_metrics.get("drawdown"),
                    "version":         _entry["version"],
                    "seeded_from":     _seed_config_name,  # None if fresh evolution
                },
            )
            print(f"  Config store: saved as '{_cfg_name}'")
        except Exception as _cs_exc:
            print(f"  [config_store] Could not save named config: {_cs_exc}")

    elif args.mode == "walk_forward":
        run_walk_forward(
            results_dir=Path("data/optimizer"),
            workers=args.workers,
        )

    elif args.mode == "monte_carlo":
        run_monte_carlo(
            results_dir=Path("data/optimizer"),
        )

    elif args.mode == "reconcile":
        run_reconcile(results_dir=Path("data/optimizer"))


if __name__ == "__main__":
    main()
