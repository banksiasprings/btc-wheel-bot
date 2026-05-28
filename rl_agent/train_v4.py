"""
train_v4.py — SAC with Max ROI + Survival reward.

Evolution pipeline Phase 3: Align reward with Steven's actual goal.
  - Max annualised ROI (uncapped upside, no Sharpe dampening)
  - Survival instinct (stepped penalty: warning → pain → catastrophic)
  - Kelly Criterion mentality: bet big, stay alive

Uses SAC (winner from Phase 1) with continuous actions.
16 features including IV surface.
Realistic Deribit transaction costs.

Usage:
    python3.11 train_v4.py                          # 20M steps
    python3.11 train_v4.py --timesteps 5000000      # shorter test
    python3.11 train_v4.py --resume checkpoints/v4/v4_final.zip
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env import BTCOptionsEnv

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints" / "v4"
LOG_DIR = Path(__file__).resolve().parent / "logs" / "v4"
RUN_LOG = Path(__file__).resolve().parent / "v4_training.log"

CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(RUN_LOG, "a") as f:
        f.write(line + "\n")


def make_env(seed=None):
    """Curriculum stage 1, continuous actions, MAX ROI + SURVIVAL reward."""
    return BTCOptionsEnv(
        curriculum_stage=1,
        reward_mode="max_roi",         # THE KEY CHANGE
        action_mode="continuous",
        starting_equity=100_000.0,
        split="train",
        seed=seed,
    )


def train(
    timesteps: int = 20_000_000,
    checkpoint_freq: int = 1_000_000,
    resume_path: str = None,
):
    from stable_baselines3 import SAC
    from stable_baselines3.common.callbacks import CheckpointCallback
    from stable_baselines3.common.vec_env import DummyVecEnv

    log("=== V4 Training — SAC + Max ROI + Survival Reward ===")
    log(f"Timesteps: {timesteps:,}")
    log(f"Reward: Max ROI (uncapped) + stepped survival penalty")
    log(f"  ROI signal: 10 * pnl/equity (linear, no tanh)")
    log(f"  Theta bonus: 0.02 * pnl/capital (when positioned + earning)")
    log(f"  Trade cost: -0.03 per trade")
    log(f"  Survival: 0-10% DD=free, 10-20%=warning, 20-30%=pain, 30%+=catastrophic")
    log(f"Algorithm: SAC (ent_coef=auto, target_entropy=-2.0)")
    log(f"Resume: {resume_path or 'fresh start'}")

    env = DummyVecEnv([lambda: make_env(seed=42)])

    # TensorBoard
    try:
        import tensorboard  # noqa: F401
        tb_log = str(LOG_DIR)
    except ImportError:
        tb_log = None

    if resume_path:
        log(f"Loading model from {resume_path}")
        model = SAC.load(resume_path, env=env)
        model.tensorboard_log = tb_log
    else:
        model = SAC(
            "MlpPolicy",
            env,
            verbose=1,
            learning_rate=3e-4,
            buffer_size=500_000,
            learning_starts=10_000,
            batch_size=256,
            tau=0.005,
            gamma=0.99,
            ent_coef="auto",
            target_entropy=-2.0,        # Lower than default -1.0: less exploration, less overtrading
            train_freq=1,
            gradient_steps=1,
            tensorboard_log=tb_log,
            seed=42,
        )

    checkpoint_callback = CheckpointCallback(
        save_freq=checkpoint_freq,
        save_path=str(CHECKPOINT_DIR),
        name_prefix="v4",
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
        learn_kwargs["tb_log_name"] = "v4_max_roi"
    model.learn(**learn_kwargs)

    elapsed = time.time() - t0
    hours = elapsed / 3600

    final_path = str(CHECKPOINT_DIR / "v4_final")
    model.save(final_path)
    log(f"Training complete in {hours:.1f} hours ({elapsed:.0f}s)")
    log(f"Saved final model to {final_path}.zip")

    return final_path + ".zip"


def main():
    parser = argparse.ArgumentParser(description="V4 Training — Max ROI + Survival")
    parser.add_argument("--timesteps", type=int, default=20_000_000)
    parser.add_argument("--checkpoint-freq", type=int, default=1_000_000)
    parser.add_argument("--resume", type=str, default=None)
    args = parser.parse_args()
    train(
        timesteps=args.timesteps,
        checkpoint_freq=args.checkpoint_freq,
        resume_path=args.resume,
    )


if __name__ == "__main__":
    main()
