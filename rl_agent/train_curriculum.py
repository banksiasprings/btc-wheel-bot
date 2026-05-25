"""
train_curriculum.py — Curriculum training with Heston data + differential Sharpe reward.

Stage 1: Heston with narrow params (gentle markets, no jumps).
         Trains risk-adjusted behaviour on realistic vol dynamics.

Usage:
    python3 train_curriculum.py                          # 5M steps, stage 1
    python3 train_curriculum.py --timesteps 1000000      # shorter run
    python3 train_curriculum.py --resume checkpoints/model_3000000_steps.zip  # resume
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env import BTCOptionsEnv

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints" / "curriculum-s1"
LOG_DIR = Path(__file__).resolve().parent / "logs" / "curriculum-s1"
RUN_LOG = Path(__file__).resolve().parent / "curriculum_s1.log"

CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(RUN_LOG, "a") as f:
        f.write(line + "\n")


def make_env(seed=None):
    """Create a curriculum stage 1 env with Heston data + differential Sharpe reward."""
    return BTCOptionsEnv(
        curriculum_stage=1,
        reward_mode="sharpe",
        starting_equity=100_000.0,
        split="train",
        seed=seed,
    )


def train(
    timesteps: int = 5_000_000,
    checkpoint_freq: int = 250_000,
    resume_path: str = None,
):
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import CheckpointCallback
    from stable_baselines3.common.vec_env import DummyVecEnv

    log(f"=== Curriculum Stage 1 Training ===")
    log(f"Timesteps: {timesteps:,}")
    log(f"Checkpoint freq: {checkpoint_freq:,}")
    log(f"Data: Heston stochastic vol (domain-randomised each episode)")
    log(f"Reward: Differential Sharpe Ratio + theta bonus - drawdown penalty")
    log(f"Resume: {resume_path or 'fresh start'}")

    env = DummyVecEnv([lambda: make_env(seed=42)])

    # TensorBoard
    try:
        import tensorboard  # noqa: F401
        tb_log = str(LOG_DIR)
    except ImportError:
        log("tensorboard not installed — stdout only")
        tb_log = None

    if resume_path:
        log(f"Loading model from {resume_path}")
        model = PPO.load(resume_path, env=env)
        model.tensorboard_log = tb_log
    else:
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
        name_prefix="curriculum_s1",
        verbose=1,
    )

    log(f"Starting training at {datetime.now().isoformat()}")
    t0 = time.time()

    learn_kwargs = dict(
        total_timesteps=timesteps,
        callback=checkpoint_callback,
        progress_bar=False,
    )
    if tb_log is not None:
        learn_kwargs["tb_log_name"] = "curriculum_s1"
    model.learn(**learn_kwargs)

    elapsed = time.time() - t0
    hours = elapsed / 3600

    final_path = str(CHECKPOINT_DIR / "curriculum_s1_final")
    model.save(final_path)
    log(f"Training complete in {hours:.1f} hours ({elapsed:.0f}s)")
    log(f"Saved final model to {final_path}.zip")

    return final_path + ".zip"


def main():
    parser = argparse.ArgumentParser(description="Curriculum Stage 1 Training")
    parser.add_argument(
        "--timesteps", type=int, default=5_000_000,
        help="Total timesteps (default 5M)",
    )
    parser.add_argument(
        "--checkpoint-freq", type=int, default=250_000,
        help="Checkpoint every N steps (default 250k)",
    )
    parser.add_argument(
        "--resume", type=str, default=None,
        help="Path to model .zip to resume training from",
    )
    args = parser.parse_args()
    train(
        timesteps=args.timesteps,
        checkpoint_freq=args.checkpoint_freq,
        resume_path=args.resume,
    )


if __name__ == "__main__":
    main()
