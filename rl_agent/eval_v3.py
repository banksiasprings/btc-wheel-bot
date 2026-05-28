"""
eval_v3.py — Evaluate V3 MaskablePPO model.

Handles VecNormalize stats and action masking.

Usage:
    python3.11 eval_v3.py
    python3.11 eval_v3.py --model checkpoints/v3/v3_500000_steps.zip
    python3.11 eval_v3.py --episodes 20
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env import BTCOptionsEnv

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints" / "v3"


def evaluate(model_path: str = None, episodes: int = 10):
    from sb3_contrib import MaskablePPO

    if model_path is None:
        model_path = str(CHECKPOINT_DIR / "v3_final.zip")

    print(f"Loading model: {model_path}")
    model = MaskablePPO.load(model_path)

    # Load VecNormalize stats if available
    norm_path = str(CHECKPOINT_DIR / "v3_vecnorm.pkl")
    try:
        from stable_baselines3.common.vec_env import VecNormalize
        # We'll manually normalize obs using saved stats
        import pickle
        with open(norm_path, "rb") as f:
            norm_data = pickle.load(f)
        obs_mean = norm_data.obs_rms.mean
        obs_var = norm_data.obs_rms.var
        has_norm = True
        print(f"Loaded VecNormalize stats")
    except Exception:
        has_norm = False
        print("No VecNormalize stats — using raw observations")

    action_names = {0: 'HOLD', 1: 'SELL_PUT_020', 2: 'SELL_PUT_025', 3: 'SELL_CALL_020', 4: 'CLOSE'}
    results = []
    action_counts = Counter()

    for ep in range(episodes):
        env = BTCOptionsEnv(
            curriculum_stage=1, reward_mode='sharpe',
            action_mode='discrete', seed=ep * 100 + 7
        )
        obs, _ = env.reset()
        total_reward = 0
        trades = 0
        steps = 0

        while True:
            # Normalize obs if we have stats
            if has_norm:
                obs_norm = np.clip(
                    (obs - obs_mean) / np.sqrt(obs_var + 1e-8), -10.0, 10.0
                ).astype(np.float32)
            else:
                obs_norm = obs

            mask = env.action_masks()
            action, _ = model.predict(obs_norm, deterministic=True, action_masks=mask)
            action = int(action)
            action_counts[action] += 1

            obs, reward, term, trunc, info = env.step(action)
            total_reward += reward
            if action in (1, 2, 3, 4):
                trades += 1
            steps += 1
            if term or trunc:
                break

        equity = info.get('equity', env._equity)
        ret = (equity - 100_000) / 100_000 * 100
        results.append({
            'ep': ep, 'steps': steps, 'trades': trades,
            'return_pct': ret, 'reward': total_reward, 'equity': equity
        })

    print(f"\n=== V3 MaskablePPO — {episodes} Episode Evaluation ===")
    print(f"{'Ep':>3} {'Steps':>6} {'Trades':>6} {'Return%':>8} {'Equity':>12} {'Reward':>8}")
    for r in results:
        print(f"{r['ep']:3d} {r['steps']:6d} {r['trades']:6d} {r['return_pct']:8.2f}% {r['equity']:12,.2f} {r['reward']:8.2f}")

    returns = [r['return_pct'] for r in results]
    print(f"\nAvg return: {np.mean(returns):.2f}%")
    print(f"Min return: {np.min(returns):.2f}%")
    print(f"Max return: {np.max(returns):.2f}%")
    print(f"Std return: {np.std(returns):.2f}%")
    print(f"Avg trades/episode: {np.mean([r['trades'] for r in results]):.1f}")
    print(f"Profitable episodes: {sum(1 for r in returns if r > 0)}/{episodes}")

    print(f"\nAction distribution:")
    total_actions = sum(action_counts.values())
    for a in range(5):
        pct = action_counts[a] / total_actions * 100 if total_actions > 0 else 0
        print(f"  {action_names[a]:15s}: {action_counts[a]:6d} ({pct:5.1f}%)")

    print(f"\n=== Comparison ===")
    print(f"PPO 5M:   avg return -6.05%  | profitable 0/10  | trades/ep 350")
    print(f"PPO 50M:  avg return -3.70%  | profitable 2/10  | trades/ep 352")
    print(f"SAC 5M:   avg return -4.48%  | profitable 0/10  | trades/ep 438")
    print(f"V3 10M:   avg return {np.mean(returns):.2f}%  | profitable {sum(1 for r in returns if r > 0)}/{episodes}  | trades/ep {np.mean([r['trades'] for r in results]):.0f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--episodes", type=int, default=10)
    args = parser.parse_args()
    evaluate(model_path=args.model, episodes=args.episodes)
