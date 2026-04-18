"""
main.py — Entry point for BTC Wheel Bot.

Usage:
    python main.py --mode backtest    # Run historical simulation (Phase 1)
    python main.py --mode paper       # Paper trade on Deribit testnet (Phase 2)
    python main.py --mode live        # Live trade on mainnet (Phase 3)
    python main.py --mode dashboard   # Show current position summary
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from config import cfg


def setup_logging() -> None:
    """Configure loguru with file rotation and console output."""
    log_dir = Path(cfg.logging.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()  # Remove default handler

    # Console: coloured, concise
    logger.add(
        sys.stderr,
        level=cfg.logging.level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        colorize=True,
    )

    # File: full detail with rotation
    logger.add(
        log_dir / "bot.log",
        level="DEBUG",
        rotation=cfg.logging.rotation,
        retention=cfg.logging.retention,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
        encoding="utf-8",
    )


def run_backtest() -> None:
    """Phase 1: run historical simulation and print results."""
    from backtester import Backtester

    logger.info("Starting backtest simulation...")
    bt = Backtester()

    results = bt.run()
    bt.print_summary(results)
    bt.save_csv(results)
    bt.save_plot(results)

    logger.info(
        f"Backtest complete: {results.num_cycles} cycles | "
        f"return={results.total_return_pct:+.1f}% | "
        f"sharpe={results.sharpe_ratio:.2f} | "
        f"maxDD={results.max_drawdown_pct:.1f}%"
    )


def run_paper() -> None:
    """Phase 2: paper trading on Deribit testnet."""
    import asyncio
    from bot import WheelBot

    if not cfg.deribit.api_key:
        logger.error("DERIBIT_API_KEY not set. Paper mode requires testnet credentials.")
        sys.exit(1)

    logger.info("Starting paper trading on Deribit testnet...")
    bot = WheelBot()
    asyncio.run(bot.start())


def run_live() -> None:
    """Phase 3: live trading on Deribit mainnet."""
    import asyncio
    from bot import WheelBot

    if not cfg.deribit.api_key or not cfg.deribit.api_secret:
        logger.error("DERIBIT_API_KEY and DERIBIT_API_SECRET must be set for live mode.")
        sys.exit(1)

    if cfg.deribit.testnet:
        logger.error(
            "Config has testnet=true but mode=live. "
            "Set DERIBIT_TESTNET=false in your .env to enable mainnet."
        )
        sys.exit(1)

    confirm = input(
        "\n⚠️  LIVE MODE — real money on Deribit MAINNET.\n"
        "Type 'YES I UNDERSTAND' to proceed: "
    )
    if confirm.strip() != "YES I UNDERSTAND":
        print("Aborted.")
        sys.exit(0)

    logger.warning("Starting LIVE trading on Deribit MAINNET")
    bot = WheelBot()
    asyncio.run(bot.start())


def run_dashboard() -> None:
    """Show current position dashboard."""
    from dashboard import print_dashboard
    # Phase 1: placeholder — will pull live data in Phase 2
    print_dashboard(positions=[], equity=10000.0, starting_equity=10000.0, num_cycles=0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTC Options Wheel Bot — premium harvesting on Deribit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --mode backtest    # Run Phase 1 backtest
  python main.py --mode paper       # Phase 2: paper trade on testnet
  python main.py --mode live        # Phase 3: live trade (REAL MONEY)
  python main.py --mode dashboard   # Show position summary
        """,
    )
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "live", "dashboard"],
        default="backtest",
        help="Execution mode (default: backtest)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override log level from config",
    )

    args = parser.parse_args()

    # Apply CLI log level override
    if args.log_level:
        import os
        os.environ["LOG_LEVEL"] = args.log_level

    setup_logging()
    logger.info(f"BTC Wheel Bot starting | mode={args.mode}")

    dispatch = {
        "backtest":  run_backtest,
        "paper":     run_paper,
        "live":      run_live,
        "dashboard": run_dashboard,
    }
    dispatch[args.mode]()


if __name__ == "__main__":
    main()
