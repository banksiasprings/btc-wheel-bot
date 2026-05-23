"""Phase 3 PPO training — curriculum stage 0 (100k steps).

Trains a small MLP PPO policy on BTCOptionsEnv with curriculum_stage=0
(SELL_PUT / CLOSE_ALL / DO_NOTHING only) for 100k timesteps and saves a
checkpoint.

This sits on top of the reward-shaping rework: every component of the env
reward is now tanh-wrapped and the final reward is clipped to [-10, 10], so
the catastrophic outliers seen in the 50k smoke test are no longer possible.

Usage:
    python training/train_ppo.py
"""

from __future__ import annotations

import os
import sys
import time
from collections import Counter
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

from environment.btc_options_env import (
    BTCOptionsEnv,
    EnvConfig,
    NUM_ACTION_TYPES,
)


TOTAL_TIMESTEPS = 100_000
PRINT_EVERY = 5_000
CURRICULUM_STAGE = 0

LOG_DIR = ROOT / "training" / "logs"
CHECKPOINT_DIR = ROOT / "training" / "checkpoints"
CHECKPOINT_PATH = CHECKPOINT_DIR / "ppo_curriculum_stage0.zip"

ACTION_NAMES = {
    0: "DO_NOTHING",
    1: "SELL_PUT",
    2: "SELL_CALL",
    3: "BUY_PUT",
    4: "BUY_CALL",
    5: "BUY_SPOT",
    6: "SELL_SPOT",
    7: "ROLL_POSITION",
    8: "CLOSE_POSITION",
}


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
                rew_min = float(np.min(rews))
                rew_max = float(np.max(rews))
                len_mean = float(np.mean(lens))
                n = len(buf)
            else:
                rew_mean = rew_min = rew_max = float("nan")
                len_mean = float("nan")
                n = 0
            elapsed = time.time() - self._t0
            sps = self.num_timesteps / max(elapsed, 1e-6)
            print(
                f"[{self.num_timesteps:>7d}/{TOTAL_TIMESTEPS}] "
                f"ep_rew_mean={rew_mean:+8.3f} (min={rew_min:+7.2f}, max={rew_max:+7.2f})  "
                f"ep_len_mean={len_mean:6.1f}  episodes={n}  "
                f"sps={sps:6.0f}  elapsed={elapsed:6.1f}s",
                flush=True,
            )
            self._next_print += self.print_every
        return True


class ActionDistributionLogger(BaseCallback):
    """Log the per-action-type rollout distribution to TensorBoard.

    SB3 hands us `self.locals['actions']` on every collect step. For the
    MultiDiscrete action space we only care about the first column (action
    type) — the strike/dte/size columns are ignored on most action types.
    The distribution and curriculum stage are recorded once per PPO update.
    """

    def __init__(self, curriculum_stage: int):
        super().__init__()
        self._counter: Counter = Counter()
        self._total = 0
        self._stage = curriculum_stage

    def _on_step(self) -> bool:
        actions = self.locals.get("actions")
        if actions is None:
            return True
        arr = np.asarray(actions)
        # MultiDiscrete actions arrive as shape (n_envs, 4); we want column 0.
        if arr.ndim == 2:
            arr = arr[:, 0]
        for a in arr.flatten():
            self._counter[int(a)] += 1
            self._total += 1
        return True

    def _on_rollout_end(self) -> None:
        self.logger.record("config/curriculum_stage", self._stage)
        total = max(self._total, 1)
        for act_id in range(NUM_ACTION_TYPES):
            name = ACTION_NAMES.get(act_id, f"act_{act_id}")
            self.logger.record(
                f"actions/{name}", self._counter.get(act_id, 0) / total
            )
        # Reset for next rollout so the histogram tracks recent behavior.
        self._counter.clear()
        self._total = 0


def make_env():
    env = BTCOptionsEnv(config=EnvConfig(seed=42, curriculum_stage=CURRICULUM_STAGE))
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
        f"Starting PPO curriculum stage {CURRICULUM_STAGE} run — "
        f"{TOTAL_TIMESTEPS} steps, net_arch=[64, 64], "
        f"obs_dim={vec_env.observation_space.shape}, "
        f"action_space={vec_env.action_space}",
        flush=True,
    )

    model.learn(
        total_timesteps=TOTAL_TIMESTEPS,
        callback=[
            RewardPrinter(print_every=PRINT_EVERY),
            ActionDistributionLogger(curriculum_stage=CURRICULUM_STAGE),
        ],
        tb_log_name=f"ppo_curriculum_stage{CURRICULUM_STAGE}",
        progress_bar=False,
    )

    model.save(str(CHECKPOINT_PATH))
    size_mb = CHECKPOINT_PATH.stat().st_size / (1024 * 1024)
    print(f"Saved checkpoint: {CHECKPOINT_PATH} ({size_mb:.2f} MB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
