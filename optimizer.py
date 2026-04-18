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
from datetime import datetime
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


# ── Worker function (runs in a subprocess) ────────────────────────────────────


def _run_backtest_worker(args: tuple[int, ParamSet, pd.DataFrame, list]) -> dict:
    """
    Worker function executed in a separate process.
    Each worker runs one full backtest with the given ParamSet.

    Returns a dict with the param set, results metrics, and fitness score.
    """
    bot_id, params, ohlcv_df, iv_history = args

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
        cfg.backtest.approx_otm_offset = params.approx_otm_offset
        cfg.backtest.premium_fraction_of_spot = params.premium_fraction_of_spot

        # Run simulation (no network calls — data pre-fetched)
        bt = Backtester()
        bt._ohlcv_cache = ohlcv_df     # inject pre-fetched data
        bt._iv_cache = iv_history

        results = bt.run_with_data(ohlcv_df, iv_history, iv_window=int(params.iv_rank_window_days))
        score = fitness_score(results)

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
            "error": None,
        }
    except Exception as exc:
        return {
            "bot_id": bot_id,
            "params": asdict(params),
            "fitness": 0.0,
            "error": str(exc),
            **{k: 0.0 for k in ["sharpe_ratio", "total_return_pct", "max_drawdown_pct",
                                  "win_rate_pct", "num_cycles", "ending_equity"]},
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
        self._ohlcv_df = bt._fetch_btc_ohlcv(cfg.backtest.lookback_months)
        self._iv_history = bt._fetch_iv_history()

        logger.info("Market data loaded and cached.")
        return self._ohlcv_df, self._iv_history

    def _run_parallel(self, genomes: list[ParamSet]) -> list[dict]:
        """Run a list of genomes in parallel using multiprocessing."""
        ohlcv_df, iv_history = self._load_data()
        args = [(i, g, ohlcv_df, iv_history) for i, g in enumerate(genomes)]

        logger.info(f"Running {len(genomes)} backtests on {self._workers} workers...")
        start = time.time()

        with mp.Pool(processes=self._workers) as pool:
            results = pool.map(_run_backtest_worker, args)

        elapsed = time.time() - start
        logger.info(f"Batch complete: {len(results)} results in {elapsed:.1f}s "
                    f"({elapsed/len(results):.1f}s avg)")
        return results

    # ── Sweep mode ────────────────────────────────────────────────────────────

    def run_sweep(self, target_param: str | None = None) -> None:
        """
        Vary each parameter individually across its full range.
        All other parameters remain at baseline (config.yaml defaults).

        If target_param is specified, only sweep that one parameter.
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
        )

        params_to_sweep = [target_param] if target_param else list(PARAM_RANGES.keys())
        all_sweep_results: dict[str, list[dict]] = {}

        for param_name in params_to_sweep:
            lo, hi, step = PARAM_RANGES[param_name]
            values = list(np.arange(lo, hi + step * 0.5, step))
            if param_name in ("max_dte", "min_dte", "iv_rank_window_days"):
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

            results = self._run_parallel(genomes)
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

    # ── Evolve mode ───────────────────────────────────────────────────────────

    def run_evolution(
        self,
        population_size: int = 20,
        generations: int = 8,
        elite_keep: int = 4,
        mutation_rate: float = 0.3,
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
        """
        logger.info(
            f"Starting genetic evolution: "
            f"pop={population_size}, gens={generations}, elite={elite_keep}"
        )

        # Generation 0: random population
        population = [_random_genome() for _ in range(population_size)]

        all_generations: list[list[dict]] = []
        best_ever: dict | None = None

        for gen in range(1, generations + 1):
            logger.info(f"Generation {gen}/{generations}")
            results = self._run_parallel(population)

            # Attach genome to each result for tracking
            for res, genome in zip(results, population):
                res["generation"] = gen

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
            return ParamSet(**best_ever["params"])

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
    parser.add_argument("--mode", choices=["sweep", "evolve"], default="sweep")
    parser.add_argument("--param", type=str, default=None,
                        help="For sweep mode: which parameter to sweep (default: all)")
    parser.add_argument("--pop",  type=int, default=20,  help="Population size (evolve mode)")
    parser.add_argument("--gen",  type=int, default=8,   help="Number of generations (evolve mode)")
    parser.add_argument("--workers", type=int, default=None, help="Parallel worker processes")
    args = parser.parse_args()

    # Setup minimal logging
    import sys
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

    opt = Optimizer(workers=args.workers)

    if args.mode == "sweep":
        if args.param and args.param not in PARAM_RANGES:
            print(f"Unknown parameter '{args.param}'. Valid options: {list(PARAM_RANGES.keys())}")
            return
        opt.run_sweep(target_param=args.param)

    elif args.mode == "evolve":
        best = opt.run_evolution(population_size=args.pop, generations=args.gen)
        # Save best genome to YAML for easy copy-paste into config.yaml
        import yaml
        out_path = Path("data/optimizer/best_genome.yaml")
        with open(out_path, "w") as f:
            yaml.dump(asdict(best), f, default_flow_style=False)
        print(f"\n  Best genome saved to {out_path} — copy values into config.yaml")


if __name__ == "__main__":
    main()
