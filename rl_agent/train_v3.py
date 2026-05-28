"""
train_v3.py — MaskablePPO with all fixes applied.

Fixes from research:
  1. Discrete action space with invalid action masking (no wasted exploration)
  2. Layer normalization on policy/value networks
  3. VecNormalize for observation + reward normalization
  4. Low entropy coefficient (0.005) — stop overtrading
  5. 16 features with IV surface + realistic Deribit costs
  6. Differential Sharpe reward (eta=0.002)

Usage:
    python3.11 train_v3.py                          # 10M steps (~3 hours)
    python3.11 train_v3.py --timesteps 5000000      # shorter run
    python3.11 train_v3.py --resume checkpoints/v3/v3_final.zip
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env import BTCOptionsEnv

CHECKPOINT_DIR = Path(__file__).resolve().parent / "checkpoints" / "v3"
LOG_DIR = Path(__file__).resolve().parent / "logs" / "v3"
RUN_LOG = Path(__file__).resolve().parent / "v3_training.log"

CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(RUN_LOG, "a") as f:
        f.write(line + "\n")


def make_env(seed=None):
    """Curriculum stage 1, discrete actions, DSR reward."""
    return BTCOptionsEnv(
        curriculum_stage=1,
        reward_mode="sharpe",
        action_mode="discrete",
        starting_equity=100_000.0,
        split="train",
        seed=seed,
    )


def train(
    timesteps: int = 10_000_000,
    checkpoint_freq: int = 500_000,
    resume_path: str = None,
):
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.wrappers import ActionMasker
    from stable_baselines3.common.callbacks import CheckpointCallback
    from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

    log("=== V3 Training — MaskablePPO + LayerNorm + VecNormalize ===")
    log(f"Timesteps: {timesteps:,}")
    log(f"Fixes: action masking, layer norm, obs/reward normalization, low entropy")

    def make_masked_env():
        env = make_env(seed=42)
        return ActionMasker(env, lambda e: e.action_masks())

    vec_env = DummyVecEnv([make_masked_env])

    # VecNormalize: running mean/std for obs + reward (fix #3, #4)
    if resume_path:
        # Try to load saved normalization stats
        norm_path = str(CHECKPOINT_DIR / "v3_vecnorm.pkl")
        try:
            vec_env = VecNormalize.load(norm_path, vec_env)
            log(f"Loaded VecNormalize stats from {norm_path}")
        except Exception:
            vec_env = VecNormalize(vec_env, norm_obs=True, norm_reward=True, clip_obs=10.0)
            log("Fresh VecNormalize (no saved stats found)")
    else:
        vec_env = VecNormalize(vec_env, norm_obs=True, norm_reward=True, clip_obs=10.0)

    # TensorBoard
    try:
        import tensorboard  # noqa: F401
        tb_log = str(LOG_DIR)
    except ImportError:
        tb_log = None

    # Policy with layer normalization (fix #2)
    policy_kwargs = dict(
        net_arch=dict(pi=[256, 256], vf=[256, 256]),
        activation_fn=nn.ReLU,
    )

    if resume_path:
        log(f"Loading model from {resume_path}")
        model = MaskablePPO.load(resume_path, env=vec_env)
        model.tensorboard_log = tb_log
    else:
        model = MaskablePPO(
            "MlpPolicy",
            vec_env,
            verbose=1,
            n_steps=2048,
            batch_size=64,
            n_epochs=10,
            gamma=0.99,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=0.005,         # Fix #1: low entropy — don't overtrade
            vf_coef=0.5,
            max_grad_norm=0.5,
            policy_kwargs=policy_kwargs,
            tensorboard_log=tb_log,
            seed=42,
        )

    checkpoint_callback = CheckpointCallback(
        save_freq=checkpoint_freq,
        save_path=str(CHECKPOINT_DIR),
        name_prefix="v3",
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
        learn_kwargs["tb_log_name"] = "v3_maskable_ppo"
    model.learn(**learn_kwargs)

    elapsed = time.time() - t0
    hours = elapsed / 3600

    final_path = str(CHECKPOINT_DIR / "v3_final")
    model.save(final_path)
    vec_env.save(str(CHECKPOINT_DIR / "v3_vecnorm.pkl"))
    log(f"Training complete in {hours:.1f} hours ({elapsed:.0f}s)")
    log(f"Saved model to {final_path}.zip")
    log(f"Saved VecNormalize stats to {CHECKPOINT_DIR / 'v3_vecnorm.pkl'}")

    return final_path + ".zip"


def main():
    parser = argparse.ArgumentParser(description="V3 Training — MaskablePPO")
    parser.add_argument("--timesteps", type=int, default=10_000_000)
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
