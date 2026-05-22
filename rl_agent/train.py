"""
train.py — PPO training script for BTCOptionsEnv.

Usage:
    python train.py                        # full 2M step run
    python train.py --timesteps 10000      # quick smoke test

Outputs:
    rl_agent/checkpoints/model_<step>.zip  — periodic checkpoints
    rl_agent/checkpoints/final_model.zip   — final saved model
    rl_agent/logs/                         — TensorBoard logs
"""

import argparse
import os
import sys
from pathlib import Path

# Allow running from project root or rl_agent/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from env import BTCOptionsEnv, load_or_generate_data

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints"
LOG_DIR = Path(__file__).resolve().parent / "logs"

CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def make_env(prices=None, iv_rank=None, split="train"):
    """Factory that returns an env instance (reuse pre-loaded data)."""
    return BTCOptionsEnv(prices=prices, iv_rank=iv_rank, split=split)


def train(timesteps: int = 2_000_000, checkpoint_freq: int = 100_000, data_path: str = None):
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import CheckpointCallback
    from stable_baselines3.common.vec_env import DummyVecEnv

    print(f"[train] Loading BTC data ...")
    prices, iv_rank = load_or_generate_data(data_path=data_path)
    source = data_path if data_path else "synthetic GBM"
    print(f"[train] Source: {source}")
    print(f"[train] Total days available: {len(prices)} | Training on first 70%")

    env = DummyVecEnv([lambda: make_env(prices=prices, iv_rank=iv_rank, split="train")])

    # Check if tensorboard is available; disable if not
    try:
        import tensorboard  # noqa: F401
        tb_log = str(LOG_DIR)
    except ImportError:
        print("[train] tensorboard not installed — logging to stdout only")
        tb_log = None

    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        vf_coef=0.5,
        max_grad_norm=0.5,
        tensorboard_log=tb_log,
        seed=42,
    )

    checkpoint_callback = CheckpointCallback(
        save_freq=checkpoint_freq,
        save_path=str(CHECKPOINT_DIR),
        name_prefix="model",
        verbose=1,
    )

    print(f"[train] Starting PPO training for {timesteps:,} timesteps ...")
    learn_kwargs = dict(
        total_timesteps=timesteps,
        callback=checkpoint_callback,
        progress_bar=False,
    )
    if tb_log is not None:
        learn_kwargs["tb_log_name"] = "ppo_btc_options"
    model.learn(**learn_kwargs)

    final_path = str(CHECKPOINT_DIR / "final_model")
    model.save(final_path)
    print(f"[train] Saved final model to {final_path}.zip")
    return final_path + ".zip"


def main():
    parser = argparse.ArgumentParser(description="Train PPO on BTCOptionsEnv")
    parser.add_argument(
        "--timesteps",
        type=int,
        default=2_000_000,
        help="Total environment steps (default 2_000_000; use 10000 for smoke test)",
    )
    parser.add_argument(
        "--checkpoint-freq",
        type=int,
        default=100_000,
        help="Save checkpoint every N steps",
    )
    parser.add_argument(
        "--data",
        type=str,
        default=None,
        help="Path to CSV file with real BTC price data (date, close, iv_rank columns). "
             "If omitted, uses synthetic GBM data.",
    )
    args = parser.parse_args()

    train(timesteps=args.timesteps, checkpoint_freq=args.checkpoint_freq, data_path=args.data)


if __name__ == "__main__":
    main()
