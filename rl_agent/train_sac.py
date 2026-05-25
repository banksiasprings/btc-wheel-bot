"""
train_sac.py — SAC training with IV surface features + tuned DSR reward.

SAC advantages over PPO:
  - Off-policy: replay buffer reuses transitions (3-5x more sample efficient)
  - Entropy regularisation: principled exploration
  - Continuous action space: natural for future delta/sizing expansion

Usage:
    python3.11 train_sac.py                          # 20M steps
    python3.11 train_sac.py --timesteps 5000000      # shorter run
    python3.11 train_sac.py --resume checkpoints/sac/sac_final.zip
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env import BTCOptionsEnv

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints" / "sac"
LOG_DIR = Path(__file__).resolve().parent / "logs" / "sac"
RUN_LOG = Path(__file__).resolve().parent / "sac_training.log"

CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(RUN_LOG, "a") as f:
        f.write(line + "\n")


def make_env(seed=None):
    """Curriculum stage 1 env with continuous actions + IV features + tuned DSR."""
    return BTCOptionsEnv(
        curriculum_stage=1,
        reward_mode="sharpe",
        action_mode="continuous",
        starting_equity=100_000.0,
        split="train",
        seed=seed,
    )


def train(
    timesteps: int = 20_000_000,
    checkpoint_freq: int = 500_000,
    resume_path: str = None,
):
    from stable_baselines3 import SAC
    from stable_baselines3.common.callbacks import CheckpointCallback
    from stable_baselines3.common.vec_env import DummyVecEnv

    log("=== SAC Training — IV Surface Features + Tuned DSR ===")
    log(f"Timesteps: {timesteps:,}")
    log(f"Checkpoint freq: {checkpoint_freq:,}")
    log(f"State space: 16 features (12 original + VRP, skew, term structure, 30d RV)")
    log(f"Action: continuous [-1, 1] mapped to 5 discrete actions")
    log(f"Reward: Differential Sharpe Ratio (eta=0.002) + theta bonus - DD penalty")
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
        model = SAC.load(resume_path, env=env)
        model.tensorboard_log = tb_log
    else:
        model = SAC(
            "MlpPolicy",
            env,
            verbose=1,
            learning_rate=3e-4,
            buffer_size=500_000,       # replay buffer (off-policy advantage)
            learning_starts=10_000,     # collect transitions before training
            batch_size=256,
            tau=0.005,                  # soft update coefficient
            gamma=0.99,
            ent_coef="auto",            # auto-tune entropy (key SAC feature)
            target_entropy="auto",
            train_freq=1,               # update every step
            gradient_steps=1,
            tensorboard_log=tb_log,
            seed=42,
        )

    checkpoint_callback = CheckpointCallback(
        save_freq=checkpoint_freq,
        save_path=str(CHECKPOINT_DIR),
        name_prefix="sac",
        verbose=1,
    )

    log(f"Starting SAC training at {datetime.now().isoformat()}")
    t0 = time.time()

    learn_kwargs = dict(
        total_timesteps=timesteps,
        callback=checkpoint_callback,
        progress_bar=False,
    )
    if tb_log is not None:
        learn_kwargs["tb_log_name"] = "sac_curriculum_s1"
    model.learn(**learn_kwargs)

    elapsed = time.time() - t0
    hours = elapsed / 3600

    final_path = str(CHECKPOINT_DIR / "sac_final")
    model.save(final_path)
    log(f"Training complete in {hours:.1f} hours ({elapsed:.0f}s)")
    log(f"Saved final model to {final_path}.zip")

    return final_path + ".zip"


def main():
    parser = argparse.ArgumentParser(description="SAC Training")
    parser.add_argument("--timesteps", type=int, default=20_000_000)
    parser.add_argument("--checkpoint-freq", type=int, default=500_000)
    parser.add_argument("--resume", type=str, default=None)
    args = parser.parse_args()
    train(
        timesteps=args.timesteps,
        checkpoint_freq=args.checkpoint_freq,
        resume_path=args.resume,
    )


if __name__ == "__main__":
    main()
