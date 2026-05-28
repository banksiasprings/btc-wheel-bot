"""
main.py — CLI entry point for BTC Wheel Bot.

Usage:
    python main.py --mode=backtest          # run historical simulation
    python main.py --mode=paper             # paper-trade (no real orders)
    python main.py --mode=testnet           # live orders on Deribit TESTNET
    python main.py --mode=live              # live orders on Deribit MAINNET

    python main.py --preflight              # run pre-flight checks (no trading)
    python main.py --preflight --testnet    # check against testnet specifically

Flags:
    --config=<path>     override default config.yaml location
    --loglevel=DEBUG    override log level
    --preflight         run pre-flight connectivity/auth checks and exit
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from loguru import logger


# ── Logging setup ─────────────────────────────────────────────────────────────

def _setup_logging(cfg) -> None:
    """Configure loguru with file + stderr handlers."""
    log_dir = Path(cfg.logging.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
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


# ── Mode handlers ─────────────────────────────────────────────────────────────

def cmd_backtest(cfg) -> None:
    """Run the historical backtest and print results."""
    from backtester import Backtester

    bt = Backtester(config=cfg)
    logger.info("Starting backtest mode…")
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
    """Run in paper-trading mode (no real orders, live market data)."""
    import asyncio
    from bot import WheelBot

    logger.info("Starting paper-trading mode…")
    bot = WheelBot(cfg, paper=True)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Paper mode interrupted")


def cmd_testnet(cfg) -> None:
    """
    Run in TESTNET mode — real Deribit API, fake money.

    This is the recommended path before going to mainnet.
    Requires DERIBIT_API_KEY + DERIBIT_API_SECRET pointing at test.deribit.com.
    Set DERIBIT_TESTNET=true in .env (or it's auto-set here).
    """
    import asyncio
    from bot import WheelBot
    from preflight import run_preflight

    # Force testnet regardless of config.yaml setting
    os.environ["DERIBIT_TESTNET"] = "true"

    if not cfg.deribit.api_key:
        logger.error("DERIBIT_API_KEY not set — cannot run testnet mode")
        logger.error("Add it to .env (see .env.example) and retry")
        sys.exit(1)

    logger.info("Running pre-flight checks against Deribit TESTNET…")
    report = run_preflight(testnet=True, on_check=lambda r: logger.info(
        f"  {'✅' if r.passed else '❌'} {r.name}: {r.message}"
    ))

    if not report.critical_passed:
        logger.error("Pre-flight checks FAILED — aborting testnet launch")
        logger.error("Run: python preflight.py --testnet  for details")
        sys.exit(1)

    logger.warning("═" * 60)
    logger.warning("  *** TESTNET MODE — orders placed on test.deribit.com ***")
    logger.warning("  Fake BTC — no real money at risk")
    logger.warning("═" * 60)

    bot = WheelBot(cfg, paper=False)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Testnet mode interrupted")


def cmd_live(cfg) -> None:
    """
    Run in LIVE mode — real orders on Deribit mainnet.

    Pre-flight checks run automatically. Requires explicit "YES" confirmation.
    """
    import asyncio
    from bot import WheelBot
    from preflight import run_preflight

    if not cfg.deribit.api_key:
        logger.error("DERIBIT_API_KEY not set — cannot run live mode")
        logger.error("Add it to .env (see .env.example) and retry")
        sys.exit(1)

    logger.info("Running pre-flight checks against Deribit MAINNET…")
    report = run_preflight(testnet=False, on_check=lambda r: logger.info(
        f"  {'✅' if r.passed else '❌'} {r.name}: {r.message}"
    ))

    if not report.critical_passed:
        logger.error("Pre-flight checks FAILED — aborting live launch")
        logger.error("Run: python preflight.py  for details")
        sys.exit(1)

    logger.warning("═" * 60)
    logger.warning("  *** LIVE TRADING MODE — REAL MONEY ON MAINNET ***")
    logger.warning("  Real BTC options will be sold on Deribit.")
    logger.warning("  Ensure your account has sufficient margin.")
    logger.warning("═" * 60)

    # Confirmation step. Two paths:
    #   1. Interactive (TTY attached): prompt for "YES I UNDERSTAND".
    #   2. Non-interactive (systemd, supervisor, container): read
    #      WHEEL_BOT_LIVE_CONFIRM env var. Must equal "YES I UNDERSTAND"
    #      to proceed. If unset, we sys.exit cleanly so the unit fails
    #      fast and visibly rather than blocking on input() forever.
    expected = "YES I UNDERSTAND"
    env_confirm = os.environ.get("WHEEL_BOT_LIVE_CONFIRM", "")
    if env_confirm:
        if env_confirm != expected:
            logger.error(
                f"WHEEL_BOT_LIVE_CONFIRM set but doesn't match "
                f"the expected confirmation phrase. Live mode aborted."
            )
            sys.exit(1)
        logger.info("Live mode confirmed via WHEEL_BOT_LIVE_CONFIRM env var.")
    elif sys.stdin.isatty():
        confirm = input(f"\n  Type '{expected}' to proceed: ").strip()
        if confirm != expected:
            logger.info("Live mode cancelled")
            sys.exit(0)
    else:
        logger.error(
            "Running non-interactively (no TTY) and WHEEL_BOT_LIVE_CONFIRM "
            "is not set. Live mode requires explicit confirmation. To "
            "proceed under systemd / docker / cron, set:\n"
            "  WHEEL_BOT_LIVE_CONFIRM='YES I UNDERSTAND'\n"
            "Aborting to avoid an infinite input() block."
        )
        sys.exit(1)

    logger.info("Live mode confirmed — starting bot…")
    bot = WheelBot(cfg, paper=False)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Live mode interrupted")


def cmd_preflight(testnet: bool = False) -> None:
    """
    Run pre-flight checks only — no trading, no bot startup.
    Useful for verifying credentials before committing to a mode.
    """
    from preflight import run_preflight

    env_label = "TESTNET" if testnet else "MAINNET"
    print(f"\n{'═'*60}")
    print(f"  BTC Wheel Bot — Pre-flight Check ({env_label})")
    print(f"{'═'*60}\n")

    def _print(result) -> None:
        icon = "✅" if result.passed else "❌"
        print(f"  {icon}  {result.name}: {result.message}")
        if result.detail:
            print(f"         {result.detail}")

    report = run_preflight(testnet=testnet, on_check=_print)

    print(f"\n{'═'*60}")
    if report.ready_for_live or report.ready_for_testnet:
        print(f"  🟢  READY — all critical checks passed")
        if testnet:
            print(f"  ➜   Next: python main.py --mode=testnet")
        else:
            print(f"  ➜   Next: python main.py --mode=live")
    else:
        print(f"  🔴  NOT READY — fix failures above before trading")
    print(f"{'═'*60}\n")
    sys.exit(0 if report.critical_passed else 1)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTC Wheel Bot — options premium-collection strategy on Deribit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  backtest   Historical simulation — no live data, no orders
  paper      Live market data, simulated orders (safe for testing logic)
  testnet    Live orders on test.deribit.com (fake money, real API)
  live       Live orders on mainnet (real money — requires confirmation)

Examples:
  python main.py --mode=paper
  python main.py --preflight --testnet
  python main.py --mode=testnet
  python main.py --mode=live --loglevel=DEBUG
        """,
    )
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "testnet", "live"],
        default="backtest",
        help="Execution mode (default: backtest)",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run pre-flight checks and exit (no trading)",
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Force testnet when used with --preflight",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--loglevel",
        default=None,
        help="Override log level: DEBUG | INFO | WARNING | ERROR",
    )
    args = parser.parse_args()

    # --preflight can run before loading config (doesn't need the full bot stack)
    if args.preflight:
        cmd_preflight(testnet=args.testnet or args.mode == "testnet")
        return

    from config import load_config
    cfg = load_config(args.config)
    if args.loglevel:
        cfg.logging.level = args.loglevel.upper()

    _setup_logging(cfg)

    banner = {
        "backtest": "BACKTEST",
        "paper":    "PAPER TRADING",
        "testnet":  "TESTNET (test.deribit.com)",
        "live":     "⚡ LIVE MAINNET ⚡",
    }
    logger.info(f"BTC Wheel Bot — {banner[args.mode]}")
    logger.info(f"Config: {args.config or 'config.yaml'}")

    dispatch = {
        "backtest": cmd_backtest,
        "paper":    cmd_paper,
        "testnet":  cmd_testnet,
        "live":     cmd_live,
    }
    dispatch[args.mode](cfg)


if __name__ == "__main__":
    main()
