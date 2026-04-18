"""
main.py -- CLI entry point for BTC Wheel Bot.

Usage:
    python main.py --mode=backtest          # run historical simulation
    python main.py --mode=paper             # paper-trade (no real orders)
    python main.py --mode=live              # live trading on Deribit

Flags:
    --config=<path>   override default config.yaml location
    --loglevel=DEBUG  override log level
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from config import load_config


def _setup_logging(cfg) -> None:
    """Configure loguru with file + stderr handlers."""
    log_dir = Path(cfg.logging.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()  # remove default handler
    logger.add(
        sys.stderr,
        level=cfg.logging.level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
        colorize=True,
    )
    logger.add(
        log_dir / "btc-wheel-bot.log",
        level="DEBUG",
        rotation=cfg.logging.rotation,
        retention=cfg.logging.retention,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
    )


def cmd_backtest(cfg) -> None:
    """Run the historical backtest and print results."""
    from backtester import Backtester

    bt = Backtester(config=cfg)
    logger.info("Starting backtest mode...")

    results = bt.run()
    bt.print_summary(results)
    bt.save_csv(results)
    bt.save_plot(results)

    logger.info(
        f"Backtest complete: "
        f"return={results.total_return_pct:+.1f}% "
        f"sharpe={results.sharpe_ratio:.2f} "
        f"maxdd={results.max_drawdown_pct:.1f}%"
    )


def cmd_paper(cfg) -> None:
    """Run in paper-trading mode (no real orders, uses live market data)."""
    import asyncio
    from bot import WheelBot

    logger.info("Starting paper-trading mode...")
    bot = WheelBot(cfg, paper=True)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Paper mode interrupted by user")


def cmd_live(cfg) -> None:
    """Run in live-trading mode (real orders on Deribit)."""
    import asyncio
    from bot import WheelBot

    if not cfg.deribit.api_key:
        logger.error("DERIBIT_API_KEY not set — cannot run live mode")
        sys.exit(1)

    logger.warning("*** LIVE TRADING MODE ACTIVE ***")
    logger.warning("Real orders will be placed on Deribit!")
    confirm = input("Type 'YES' to confirm: ").strip()
    if confirm != "YES":
        logger.info("Live mode cancelled")
        sys.exit(0)

    bot = WheelBot(cfg, paper=False)
    try:
        import asyncio
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Live mode interrupted by user")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTC Wheel Bot -- options premium-collection strategy on Deribit"
    )
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "live"],
        default="backtest",
        help="Execution mode (default: backtest)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--loglevel",
        default=None,
        help="Override log level (DEBUG, INFO, WARNING, ERROR)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.loglevel:
        cfg.logging.level = args.loglevel.upper()

    _setup_logging(cfg)
    logger.info(f"BTC Wheel Bot starting in [{args.mode.upper()}] mode")
    logger.info(f"Config: {args.config or 'config.yaml'}")

    dispatch = {"backtest": cmd_backtest, "paper": cmd_paper, "live": cmd_live}
    dispatch[args.mode](cfg)


if __name__ == "__main__":
    main()
