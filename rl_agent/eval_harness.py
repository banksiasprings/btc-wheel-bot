"""
eval_harness.py — universal backtest/evaluation on the REAL Deribit data.

Runs any baseline policy or SB3 model (PPO/SAC/MaskablePPO) deterministically
over the locked test split (last 30% of rl_agent/data/btc_daily.csv ≈ most
recent ~11 months) and prints a leaderboard. buy_hold is computed directly
from the price series since the env has no spot-buy action.

Usage:
    python3.11 eval_harness.py                 # full leaderboard
    python3.11 eval_harness.py --split train   # evaluate on the train split instead
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from env import BTCOptionsEnv
import baselines
import metrics as M

DATA_PATH = str(HERE / "data" / "btc_daily.csv")
START_DAY = 20            # warm-up so momentum/RV features are populated
STARTING_EQUITY = 100_000.0


def backtest_policy(policy_fn, action_mode="discrete", split="test", start_day=START_DAY,
                    data_path=DATA_PATH, seed=42, starting_equity=STARTING_EQUITY):
    """Deterministic single-path backtest from start_day to end of split."""
    env = BTCOptionsEnv(
        data_path=data_path, split=split, action_mode=action_mode,
        reward_mode="sharpe", curriculum_stage=0,
        starting_equity=starting_equity, seed=seed,
    )
    env.reset(seed=seed)
    env._day = min(start_day, max(0, env.n_days - 2))   # override random start

    obs = env._obs()
    curve = [env._equity]
    trade_pnls = []
    n_opens = 0
    steps_in_market = 0
    eq_before = env._equity

    while True:
        action = policy_fn(obs, env)
        obs, _reward, term, trunc, info = env.step(action)

        # self._equity only changes when a position settles → that delta is a realised trade
        if abs(env._equity - eq_before) > 1e-9:
            trade_pnls.append(env._equity - eq_before)
            eq_before = env._equity

        t = info.get("trade")
        if isinstance(t, str) and t.startswith("SELL"):
            n_opens += 1
        if env._position is not None:
            steps_in_market += 1

        unreal = env._position.unrealised_pnl if env._position is not None else 0.0
        curve.append(env._equity + unreal)

        if term or trunc:
            break

    return np.asarray(curve, dtype=np.float64), trade_pnls, n_opens, steps_in_market


def get_split_prices(split="test", data_path=DATA_PATH):
    env = BTCOptionsEnv(data_path=data_path, split=split, curriculum_stage=0,
                        action_mode="discrete", seed=42)
    return np.asarray(env.prices, dtype=np.float64)


# --- SB3 model → policy adapters ---

def sac_policy(path):
    from stable_baselines3 import SAC
    model = SAC.load(path)

    def fn(obs, env):
        a, _ = model.predict(np.asarray(obs), deterministic=True)
        return a

    return fn


def maskable_policy(model_path, vecnorm_path=None):
    from sb3_contrib import MaskablePPO
    model = MaskablePPO.load(model_path)
    mean = var = None
    if vecnorm_path and Path(vecnorm_path).exists():
        try:
            with open(vecnorm_path, "rb") as f:
                nd = pickle.load(f)
            mean, var = nd.obs_rms.mean, nd.obs_rms.var
        except Exception as e:
            print(f"  [warn] could not load VecNormalize stats: {e}")

    def fn(obs, env):
        o = np.asarray(obs, dtype=np.float32)
        if mean is not None:
            o = np.clip((o - mean) / np.sqrt(var + 1e-8), -10.0, 10.0).astype(np.float32)
        mask = env.action_masks()
        a, _ = model.predict(o, deterministic=True, action_masks=mask)
        return int(a)

    return fn


def ppo_policy(path):
    from stable_baselines3 import PPO
    model = PPO.load(path)

    def fn(obs, env):
        a, _ = model.predict(np.asarray(obs), deterministic=True)
        return int(a)

    return fn


# --- reporting ---

COLS = [
    ("Strategy", "{:<14}"),
    ("Return%", "{:>8}"),
    ("Annual%", "{:>8}"),
    ("Sharpe", "{:>7}"),
    ("Sortino", "{:>8}"),
    ("MaxDD%", "{:>7}"),
    ("Win%", "{:>6}"),
    ("Trades", "{:>7}"),
    ("InMkt%", "{:>7}"),
]


def _fmt(v):
    if v == float("inf"):
        return "inf"
    return f"{v:.2f}"


def print_table(rows):
    header = "".join(fmt.format(name) for name, fmt in COLS)
    print(header)
    print("-" * len(header))
    for name, s in rows:
        if "error" in s:
            print(f"{name:<14}  ERROR: {s['error']}")
            continue
        vals = [
            name,
            _fmt(s["total_return"] * 100),
            _fmt(s["annualised_return"] * 100),
            _fmt(s["sharpe"]),
            _fmt(s["sortino"]),
            _fmt(s["max_drawdown"] * 100),
            _fmt(s["win_rate"] * 100),
            str(s["n_trades"]),
            _fmt(s["time_in_market"] * 100),
        ]
        print("".join(fmt.format(v) for (_, fmt), v in zip(COLS, vals)))


def main():
    parser = argparse.ArgumentParser(description="Real-data eval leaderboard")
    parser.add_argument("--split", default="test", choices=["train", "test"])
    args = parser.parse_args()

    prices = get_split_prices(args.split)
    n_days = len(prices)
    print(f"\n=== Real-data leaderboard ({args.split} split: {n_days} days, "
          f"start_day={START_DAY}, costs ON) ===")
    print(f"BTC over window: ${prices[START_DAY]:,.0f} -> ${prices[-1]:,.0f} "
          f"({(prices[-1]/prices[START_DAY]-1)*100:+.1f}%)\n")

    rows = []

    bh = baselines.buy_hold_curve(prices, START_DAY, STARTING_EQUITY)
    rows.append(("buy_hold", M.summarise(bh, [], 0, len(bh))))

    for name in ("do_nothing", "simple_wheel", "always_wheel"):
        curve, pnls, opens, sim = backtest_policy(baselines.POLICIES[name],
                                                   action_mode="discrete", split=args.split)
        rows.append((name, M.summarise(curve, pnls, opens, sim)))

    models = [
        ("SAC-20M", lambda: sac_policy(str(HERE / "checkpoints/sac/sac_final.zip")), "continuous"),
        ("V3-MaskPPO", lambda: maskable_policy(
            str(HERE / "checkpoints/v3/v3_final.zip"),
            str(HERE / "checkpoints/v3/v3_vecnorm.pkl")), "discrete"),
    ]
    for name, builder, amode in models:
        try:
            fn = builder()
            curve, pnls, opens, sim = backtest_policy(fn, action_mode=amode, split=args.split)
            rows.append((name, M.summarise(curve, pnls, opens, sim)))
        except Exception as e:
            rows.append((name, {"error": repr(e)}))

    print_table(rows)
    print("\nGATE 0: is simple_wheel profitable net of costs, and do any models beat it?")


if __name__ == "__main__":
    main()
