"""Phase 3 PPO smoke test.

Trains a small MLP PPO policy on BTCOptionsEnv for 50k timesteps and saves a
checkpoint. This is a smoke test of the training pipeline — verify that the
gym env, SB3 PPO, and TB logging plumbing all work end-to-end on CPU.

Usage:
    python training/train_ppo.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import torch

# Make the project importable when invoked from anywhere.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv

from environment.btc_options_env import BTCOptionsEnv, EnvConfig


TOTAL_TIMESTEPS = 50_000
PRINT_EVERY = 5_000

LOG_DIR = ROOT / "training" / "logs"
CHECKPOINT_DIR = ROOT / "training" / "checkpoints"
CHECKPOINT_PATH = CHECKPOINT_DIR / "ppo_smoke_test.zip"


class RewardPrinter(BaseCallback):
    """Print rolling ep_rew_mean / ep_len_mean every `print_every` steps."""

    def __init__(self, print_every: int = 5_000):
        super().__init__()
        self.print_every = print_every
        self._next_print = print_every
        self._t0 = time.time()

    def _on_step(self) -> bool:
        if self.num_timesteps >= self._next_print:
            buf = self.model.ep_info_buffer
            if buf and len(buf) > 0:
                rews = [ep["r"] for ep in buf]
                lens = [ep["l"] for ep in buf]
                rew_mean = float(np.mean(rews))
                len_mean = float(np.mean(lens))
                n = len(buf)
            else:
                rew_mean = float("nan")
                len_mean = float("nan")
                n = 0
            elapsed = time.time() - self._t0
            sps = self.num_timesteps / max(elapsed, 1e-6)
            print(
                f"[{self.num_timesteps:>7d}/{TOTAL_TIMESTEPS}] "
                f"ep_rew_mean={rew_mean:8.3f}  ep_len_mean={len_mean:7.1f}  "
                f"episodes={n}  sps={sps:6.0f}  elapsed={elapsed:6.1f}s",
                flush=True,
            )
            self._next_print += self.print_every
        return True


def make_env():
    env = BTCOptionsEnv(config=EnvConfig(seed=42))
    # Monitor wrapper is required for SB3 to populate ep_info_buffer
    # (ep_rew_mean / ep_len_mean printed in the training log).
    return Monitor(env, filename=str(LOG_DIR / "monitor"))


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))

    vec_env = DummyVecEnv([make_env])

    model = PPO(
        policy="MlpPolicy",
        env=vec_env,
        learning_rate=3e-4,
        n_steps=2048,
        batch_size=256,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        vf_coef=0.5,
        max_grad_norm=0.5,
        policy_kwargs=dict(net_arch=[64, 64]),
        tensorboard_log=str(LOG_DIR),
        verbose=1,
        device="cpu",
        seed=42,
    )

    print(
        f"Starting PPO smoke test — {TOTAL_TIMESTEPS} steps, "
        f"net_arch=[64, 64], obs_dim={vec_env.observation_space.shape}, "
        f"action_space={vec_env.action_space}",
        flush=True,
    )

    model.learn(
        total_timesteps=TOTAL_TIMESTEPS,
        callback=RewardPrinter(print_every=PRINT_EVERY),
        tb_log_name="ppo_smoke_test",
        progress_bar=False,
    )

    model.save(str(CHECKPOINT_PATH))
    size_mb = CHECKPOINT_PATH.stat().st_size / (1024 * 1024)
    print(f"Saved checkpoint: {CHECKPOINT_PATH} ({size_mb:.2f} MB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
