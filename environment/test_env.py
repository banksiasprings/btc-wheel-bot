"""
Phase 2 sanity test for BTCOptionsEnv.

Runs N random-action steps across multiple episodes, asserts no crashes /
non-finite values, and prints summary stats. Treat this as the gate before
plugging the env into PPO training.
"""

from __future__ import annotations

import math
import sys
import time

import numpy as np

from environment.btc_options_env import BTCOptionsEnv, EnvConfig


def main(total_steps: int = 1000, seed: int = 0):
    config = EnvConfig(seed=seed)
    env = BTCOptionsEnv(config=config)

    print(f"obs space: {env.observation_space}")
    print(f"action space: {env.action_space}")
    print(f"feature columns: {len(env._feature_columns)}")

    rewards = []
    obs_min = math.inf
    obs_max = -math.inf
    nan_count = 0
    inf_count = 0

    steps_run = 0
    ep_lengths = []
    ep_returns = []
    ep_terminals = 0
    ep_truncated = 0
    cur_ep_len = 0
    cur_ep_ret = 0.0
    final_equities = []

    rng = np.random.default_rng(seed)
    obs, info = env.reset(seed=seed)
    t0 = time.perf_counter()

    while steps_run < total_steps:
        a = rng.integers(low=0, high=np.asarray(env.action_space.nvec), size=4)
        obs, r, terminated, truncated, info = env.step(a)

        # Validity checks
        assert obs.shape == (env.observation_space.shape[0],), f"bad obs shape {obs.shape}"
        if np.isnan(obs).any():
            nan_count += int(np.isnan(obs).sum())
        if np.isinf(obs).any():
            inf_count += int(np.isinf(obs).sum())
        assert math.isfinite(r), f"non-finite reward: {r}"

        obs_min = min(obs_min, float(obs.min()))
        obs_max = max(obs_max, float(obs.max()))
        rewards.append(float(r))

        steps_run += 1
        cur_ep_len += 1
        cur_ep_ret += float(r)

        if terminated or truncated:
            ep_lengths.append(cur_ep_len)
            ep_returns.append(cur_ep_ret)
            final_equities.append(info.get("equity", float("nan")))
            if terminated:
                ep_terminals += 1
            if truncated:
                ep_truncated += 1
            cur_ep_len = 0
            cur_ep_ret = 0.0
            obs, info = env.reset()

    elapsed = time.perf_counter() - t0
    rewards = np.asarray(rewards)

    print()
    print("=" * 60)
    print(f"Ran {steps_run} steps in {elapsed:.2f}s ({steps_run/elapsed:.0f} steps/s)")
    print(f"Episodes completed: {len(ep_lengths)}  (term: {ep_terminals}, trunc: {ep_truncated})")
    if ep_lengths:
        print(f"  avg ep length: {np.mean(ep_lengths):.1f}, max: {np.max(ep_lengths)}")
        print(f"  avg ep return: {np.mean(ep_returns):.3f}")
        print(f"  final equity (mean): ${np.mean(final_equities):,.0f}")
    print(f"Reward — mean: {rewards.mean():+.4f}, std: {rewards.std():.4f}")
    print(f"         min:  {rewards.min():+.4f}, max: {rewards.max():+.4f}")
    print(f"Obs — min: {obs_min:.3f}, max: {obs_max:.3f}")
    print(f"NaN/Inf counts in obs: nan={nan_count} inf={inf_count}")
    print("=" * 60)

    # Hard pass/fail gate.
    failures = []
    if nan_count or inf_count:
        failures.append(f"non-finite obs: nan={nan_count} inf={inf_count}")
    if abs(obs_max) > 10.5 or abs(obs_min) > 10.5:
        failures.append(f"obs out of clipped range: [{obs_min:.3f}, {obs_max:.3f}]")
    if not math.isfinite(rewards.mean()) or not math.isfinite(rewards.std()):
        failures.append("rewards not finite")
    if len(ep_lengths) == 0:
        failures.append("no episode completed in 1000 steps — episode_length too long?")
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("OK — sanity test passed.")


if __name__ == "__main__":
    main()
