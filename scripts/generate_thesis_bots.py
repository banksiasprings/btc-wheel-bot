"""
scripts/generate_thesis_bots.py — write a fleet of thesis-driven paper bots.

Each bot encodes one hypothesis about what works in the BTC-options wheel
strategy. Running them in parallel (via bot_farm.py) generates real per-leg
trade data so the user can find the actual boundary-of-profitability
empirically rather than guessing.

Usage:
    python3.11 scripts/generate_thesis_bots.py
"""
from __future__ import annotations

import yaml
from datetime import datetime, timezone
from pathlib import Path

CONFIGS_DIR = Path(__file__).parent.parent / "configs"

# Common scaffolding that every config needs — only the fields that diverge
# per-thesis are overridden per bot.
COMMON: dict = {
    "deribit": {
        "currency": "BTC",
        "request_timeout": 10,
        "rest_url_mainnet": "https://www.deribit.com/api/v2",
        "rest_url_testnet": "https://test.deribit.com/api/v2",
        "testnet": True,
        "ws_url_mainnet": "wss://www.deribit.com/ws/api/v2",
        "ws_url_testnet": "wss://test.deribit.com/ws/api/v2",
    },
    "execution": {
        "order_confirm_timeout": 30,
        "poll_interval": 60,
        "slippage_tolerance": 0.005,
    },
    "logging": {
        "level": "INFO",
        "log_dir": "logs",
        "retention": "30 days",
        "rotation": "1 day",
        "trade_log_csv": "data/trades.csv",
    },
    "overseer": {
        "check_interval_minutes": 60,
        "drawdown_warning_threshold": 0.05,
        "enabled": True,
        "iv_spike_warning_threshold": 0.85,
    },
    "backtest": {
        "approx_otm_offset": 0.0338,
        "lookback_months": 12,
        "premium_fraction_of_spot": 0.0289,
        "results_csv": "data/backtest_trades.csv",
        "results_image": "backtest_results.png",
        "risk_free_rate": 0.04,
        "starting_equity": 100_000.0,    # uniform $100k for cross-bot comparison
        "transaction_cost": 0.5,
    },
}

DEFAULT_RISK = {
    "kill_switch_file": "KILL_SWITCH",
    "max_adverse_delta": 0.4,
    "max_daily_drawdown": 0.1,
    "max_loss_per_leg": 0.02,
    "roll_enabled": False,
    "roll_min_dte": 3,
}

DEFAULT_HEDGE = {
    "enabled": True,
    "rebalance_threshold": 0.05,
}


def build(
    name: str,
    hypothesis: str,
    *,
    iv_rank_threshold: float = 0.30,
    target_delta_min: float = 0.18,
    target_delta_max: float = 0.40,
    min_dte: int = 3,
    max_dte: int = 37,
    max_open_legs: int = 10,
    max_equity_per_leg: float = 0.10,
    min_free_equity_fraction: float = 0.05,
    collateral_buffer: float = 1.5,
    hedge_enabled: bool = True,
    iv_dynamic_delta: bool = True,
) -> dict:
    """Construct a config dict from a thesis spec."""
    cfg = {
        "_meta": {
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "thesis",
            "status": "paper",
            "notes": hypothesis,
            "goal": None,
            "fitness": None,
        },
        "strategy": {
            "expiry_execution_hour": 8,
            "expiry_preference": ["weekly", "monthly"],
            "initial_cycle": "put",
            "iv_dynamic_delta": iv_dynamic_delta,
            "iv_rank_threshold": iv_rank_threshold,
            "liquidity_top_n": 100,
            "max_dte": max_dte,
            "min_dte": min_dte,
            "target_delta_max": target_delta_max,
            "target_delta_min": target_delta_min,
        },
        "sizing": {
            "collateral_buffer": collateral_buffer,
            "contract_size_btc": 0.1,
            "ladder_enabled": False,
            "ladder_legs": 2,
            "max_equity_per_leg": max_equity_per_leg,
            "max_open_legs": max_open_legs,
            "min_free_equity_fraction": min_free_equity_fraction,
            "regime_ma_days": 50,
            "use_regime_filter": False,
        },
        "risk": dict(DEFAULT_RISK),
        "hedge": {
            "enabled": hedge_enabled,
            "rebalance_threshold": 0.05,
        },
    }
    # Splice in the common scaffolding
    for k, v in COMMON.items():
        cfg[k] = v
    return cfg


def main() -> None:
    THESIS_BOTS = [
        # 1. Deep OTM safety — far-out-of-the-money puts/calls
        build(
            "deep_otm_safety",
            "Sells deep-OTM options (delta 0.10-0.20). Hypothesis: very high "
            "win rate (>90%), low premium per trade, minimal assignment risk. "
            "Should be the steadiest equity curve.",
            target_delta_min=0.10,
            target_delta_max=0.20,
            iv_rank_threshold=0.30,
        ),
        # 2. ATM premium hunter — collect maximum premium, accept assignments
        build(
            "atm_premium_hunter",
            "Sells closer to ATM (delta 0.30-0.50). Hypothesis: max premium "
            "per trade, more assignments, lower win rate but bigger absolute "
            "P&L. Tests how the wheel handles assignment cycles.",
            target_delta_min=0.30,
            target_delta_max=0.50,
            iv_rank_threshold=0.30,
        ),
        # 3. Short-DTE theta scalper
        build(
            "short_dte_theta",
            "DTE 1-7 only. Weekly options exclusively. Hypothesis: fast "
            "theta decay = more cycles/year, but more whipsaw on short-dated "
            "moves. Tests how often we can recycle capital.",
            min_dte=1,
            max_dte=7,
            iv_rank_threshold=0.20,
        ),
        # 4. Long-DTE monthly trader
        build(
            "long_dte_monthly",
            "DTE 21-45 only — monthlies. Hypothesis: smoother time-decay, "
            "fewer assignments per cycle, but capital tied up longer. "
            "Better for ranging markets.",
            min_dte=21,
            max_dte=45,
            iv_rank_threshold=0.30,
        ),
        # 5. No-hedge naked
        build(
            "no_hedge_naked",
            "Hedge disabled — no perp offset, fully directional. Hypothesis: "
            "bigger swings, much higher variance, but lower funding cost "
            "and simpler operationally. Tests how much the hedge actually "
            "earns or loses.",
            hedge_enabled=False,
            iv_rank_threshold=0.30,
        ),
        # 6. Max stack — stress test
        build(
            "max_stack",
            "max_open_legs=20, max_equity_per_leg=0.04 → up to 20 simultaneous "
            "positions at ~4% equity each. Hypothesis: very high margin "
            "utilisation will trigger collateral / free-margin checks; logs "
            "will show exactly where the bot stops opening new legs.",
            max_open_legs=20,
            max_equity_per_leg=0.04,
            min_free_equity_fraction=0.02,
            iv_rank_threshold=0.30,
        ),
    ]

    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    for cfg in THESIS_BOTS:
        name = cfg["_meta"]["name"]
        path = CONFIGS_DIR / f"{name}.yaml"
        with open(path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
        print(f"  ✓ {path.name} — {cfg['_meta']['notes'][:80]}…")

    print(f"\nWrote {len(THESIS_BOTS)} thesis bots to {CONFIGS_DIR}/")
    print("Each is at status='paper' so bot_farm.py will pick them up automatically.")


if __name__ == "__main__":
    main()
