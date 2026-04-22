"""
config.py — Centralised configuration loader for BTC Wheel Bot.

Loads settings from config.yaml, then overlays values from environment
variables (via .env or system env).  Exposes a single typed Config object
that every other module imports.

Usage:
    from config import cfg
    print(cfg.strategy.iv_rank_threshold)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv

# Load .env before reading environment variables
load_dotenv()

# ── Sub-configs ────────────────────────────────────────────────────────────────


@dataclass
class DeribitConfig:
    testnet: bool
    ws_url_testnet: str
    ws_url_mainnet: str
    rest_url_testnet: str
    rest_url_mainnet: str
    request_timeout: int
    currency: str
    api_key: str = ""
    api_secret: str = ""

    @property
    def ws_url(self) -> str:
        return self.ws_url_testnet if self.testnet else self.ws_url_mainnet

    @property
    def rest_url(self) -> str:
        return self.rest_url_testnet if self.testnet else self.rest_url_mainnet


@dataclass
class StrategyConfig:
    iv_rank_threshold: float
    target_delta_min: float
    target_delta_max: float
    expiry_preference: list[str]
    min_dte: int
    max_dte: int
    initial_cycle: Literal["put", "call"]
    expiry_execution_hour: int
    liquidity_top_n: int
    # Dynamic delta: shift the target delta midpoint with IV rank.
    # When True: low IV rank → conservative (target_delta_min side),
    #            high IV rank → aggressive (target_delta_max side).
    iv_dynamic_delta: bool = False


@dataclass
class SizingConfig:
    max_equity_per_leg: float
    max_open_legs: int
    collateral_buffer: float
    contract_size_btc: float
    min_free_equity_fraction: float = 0.25
    # Regime filter: skip put-selling when BTC is below its N-day MA
    use_regime_filter: bool = False
    regime_ma_days: int = 50


@dataclass
class RiskConfig:
    max_adverse_delta: float
    max_loss_per_leg: float
    max_daily_drawdown: float
    kill_switch_file: str


@dataclass
class ExecutionConfig:
    poll_interval: int
    order_confirm_timeout: int
    slippage_tolerance: float


@dataclass
class BacktestConfig:
    starting_equity: float
    lookback_months: int
    transaction_cost: float
    approx_otm_offset: float
    premium_fraction_of_spot: float
    risk_free_rate: float
    results_image: str
    results_csv: str


@dataclass
class HedgeConfig:
    enabled: bool = True
    rebalance_threshold: float = 0.05   # min BTC drift before a hedge trade fires


@dataclass
class OverseerConfig:
    enabled: bool
    check_interval_minutes: int
    drawdown_warning_threshold: float
    iv_spike_warning_threshold: float


@dataclass
class LoggingConfig:
    level: str
    rotation: str
    retention: str
    log_dir: str
    trade_log_csv: str


@dataclass
class Config:
    deribit: DeribitConfig
    strategy: StrategyConfig
    sizing: SizingConfig
    risk: RiskConfig
    execution: ExecutionConfig
    backtest: BacktestConfig
    overseer: OverseerConfig
    logging: LoggingConfig
    hedge: HedgeConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.hedge is None:
            self.hedge = HedgeConfig()


# ── Loader ─────────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    """Load and parse a YAML file."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def load_config(yaml_path: str | Path | None = None) -> Config:
    """
    Load configuration from YAML file and environment variable overrides.

    Environment variable precedence (highest wins):
        DERIBIT_API_KEY, DERIBIT_API_SECRET — auth credentials
        DERIBIT_TESTNET — "true" / "false" override
        LOG_LEVEL — logging level override
    """
    if yaml_path is None:
        yaml_path = Path(__file__).parent / "config.yaml"

    raw = _load_yaml(Path(yaml_path))

    d = raw["deribit"]
    deribit_cfg = DeribitConfig(
        testnet=os.getenv("DERIBIT_TESTNET", str(d["testnet"])).lower() == "true",
        ws_url_testnet=d["ws_url_testnet"],
        ws_url_mainnet=d["ws_url_mainnet"],
        rest_url_testnet=d["rest_url_testnet"],
        rest_url_mainnet=d["rest_url_mainnet"],
        request_timeout=int(d["request_timeout"]),
        currency=d["currency"],
        # Secrets always come from environment — never YAML
        api_key=os.getenv("DERIBIT_API_KEY", ""),
        api_secret=os.getenv("DERIBIT_API_SECRET", ""),
    )

    s = raw["strategy"]
    strategy_cfg = StrategyConfig(
        iv_rank_threshold=float(s["iv_rank_threshold"]),
        target_delta_min=float(s["target_delta_min"]),
        target_delta_max=float(s["target_delta_max"]),
        expiry_preference=list(s["expiry_preference"]),
        min_dte=int(s["min_dte"]),
        max_dte=int(s["max_dte"]),
        initial_cycle=s["initial_cycle"],
        expiry_execution_hour=int(s["expiry_execution_hour"]),
        liquidity_top_n=int(s["liquidity_top_n"]),
        iv_dynamic_delta=bool(s.get("iv_dynamic_delta", False)),
    )

    sz = raw["sizing"]
    sizing_cfg = SizingConfig(
        max_equity_per_leg=float(sz["max_equity_per_leg"]),
        max_open_legs=int(sz["max_open_legs"]),
        collateral_buffer=float(sz["collateral_buffer"]),
        contract_size_btc=float(sz["contract_size_btc"]),
        min_free_equity_fraction=float(sz.get("min_free_equity_fraction", 0.25)),
        use_regime_filter=bool(sz.get("use_regime_filter", False)),
        regime_ma_days=int(sz.get("regime_ma_days", 50)),
    )

    r = raw["risk"]
    risk_cfg = RiskConfig(
        max_adverse_delta=float(r["max_adverse_delta"]),
        max_loss_per_leg=float(r["max_loss_per_leg"]),
        max_daily_drawdown=float(r["max_daily_drawdown"]),
        kill_switch_file=r["kill_switch_file"],
    )

    e = raw["execution"]
    exec_cfg = ExecutionConfig(
        poll_interval=int(e["poll_interval"]),
        order_confirm_timeout=int(e["order_confirm_timeout"]),
        slippage_tolerance=float(e["slippage_tolerance"]),
    )

    bt = raw["backtest"]
    backtest_cfg = BacktestConfig(
        starting_equity=float(bt["starting_equity"]),
        lookback_months=int(bt["lookback_months"]),
        transaction_cost=float(bt["transaction_cost"]),
        approx_otm_offset=float(bt["approx_otm_offset"]),
        premium_fraction_of_spot=float(bt["premium_fraction_of_spot"]),
        risk_free_rate=float(bt["risk_free_rate"]),
        results_image=bt["results_image"],
        results_csv=bt["results_csv"],
    )

    ov = raw.get("overseer", {})
    overseer_cfg = OverseerConfig(
        enabled=bool(ov.get("enabled", True)),
        check_interval_minutes=int(ov.get("check_interval_minutes", 60)),
        drawdown_warning_threshold=float(ov.get("drawdown_warning_threshold", 0.05)),
        iv_spike_warning_threshold=float(ov.get("iv_spike_warning_threshold", 0.85)),
    )

    lg = raw["logging"]
    logging_cfg = LoggingConfig(
        level=os.getenv("LOG_LEVEL", lg["level"]),
        rotation=lg["rotation"],
        retention=lg["retention"],
        log_dir=lg["log_dir"],
        trade_log_csv=lg["trade_log_csv"],
    )

    hg = raw.get("hedge", {})
    hedge_cfg = HedgeConfig(
        enabled=bool(hg.get("enabled", True)),
        rebalance_threshold=float(hg.get("rebalance_threshold", 0.05)),
    )

    return Config(
        deribit=deribit_cfg,
        strategy=strategy_cfg,
        sizing=sizing_cfg,
        risk=risk_cfg,
        execution=exec_cfg,
        backtest=backtest_cfg,
        overseer=overseer_cfg,
        logging=logging_cfg,
        hedge=hedge_cfg,
    )


# Module-level singleton — import this everywhere
cfg: Config = load_config()
