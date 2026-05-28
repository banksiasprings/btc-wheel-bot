"""
baselines.py — non-RL reference policies the RL agent must beat.

A policy is `policy(obs, env) -> int` returning a discrete action. The eval
harness runs these through the same env/test-split as the RL models.

buy_hold is not an env policy (the env has no spot-buy action), so it is
provided as a direct equity-curve constructor from the price series.
"""

from __future__ import annotations

import numpy as np

from env import BTCOptionsEnv

IVR_THRESHOLD = 0.5  # sell premium only when IV rank is above this


def do_nothing(obs, env) -> int:
    """Hold cash forever — the floor any strategy must clear."""
    return BTCOptionsEnv.ACTION_HOLD


def simple_wheel(obs, env) -> int:
    """Sell a 20-delta weekly put whenever flat and IV rank > 0.5; hold to expiry.

    This is the 'dumb wheel' — no timing beyond an IV-rank gate, no early close.
    The env auto-settles the position at expiry, after which we sell again.
    """
    if env._position is None and obs[1] > IVR_THRESHOLD:
        return BTCOptionsEnv.ACTION_SELL_PUT_020
    return BTCOptionsEnv.ACTION_HOLD


def always_wheel(obs, env) -> int:
    """Sell a 20-delta weekly put whenever flat, regardless of IV rank.

    Isolates the value of the IV-rank gate when compared against simple_wheel.
    """
    if env._position is None:
        return BTCOptionsEnv.ACTION_SELL_PUT_020
    return BTCOptionsEnv.ACTION_HOLD


def buy_hold_curve(prices: np.ndarray, start_day: int, starting_equity: float) -> np.ndarray:
    """Equity curve for holding BTC over the same window the policies trade."""
    seg = np.asarray(prices[start_day:], dtype=np.float64)
    if len(seg) == 0 or seg[0] <= 0:
        return np.array([starting_equity], dtype=np.float64)
    return starting_equity * seg / seg[0]


POLICIES = {
    "do_nothing": do_nothing,
    "simple_wheel": simple_wheel,
    "always_wheel": always_wheel,
}
