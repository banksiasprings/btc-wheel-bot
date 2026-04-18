"""
dashboard.py — Console dashboard for current bot state.

Run standalone:  python dashboard.py
Or imported by main.py for status display.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tabulate import tabulate


def render_position_table(positions: list[dict[str, Any]]) -> str:
    """Render open positions as a console table."""
    if not positions:
        return "  No open positions.\n"
    headers = ["Instrument", "Type", "Strike", "Delta", "Entry Premium", "Unreal. P&L", "DTE"]
    rows = [
        [
            p.get("instrument_name", "—"),
            p.get("option_type", "—").upper(),
            f"${p.get('strike', 0):,.0f}",
            f"{p.get('delta', 0):.3f}",
            f"${p.get('entry_price', 0):,.2f}",
            f"${p.get('unrealised_pnl', 0):+,.2f}",
            p.get("dte", "—"),
        ]
        for p in positions
    ]
    return tabulate(rows, headers=headers, tablefmt="rounded_outline")


def render_account_summary(equity: float, starting_equity: float, num_cycles: int) -> str:
    """Render account summary block."""
    pnl = equity - starting_equity
    pnl_pct = (pnl / starting_equity * 100) if starting_equity > 0 else 0
    rows = [
        ["Current equity", f"${equity:,.2f}"],
        ["Total P&L",      f"${pnl:+,.2f} ({pnl_pct:+.1f}%)"],
        ["Cycles completed", num_cycles],
        ["Last updated",   datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
    ]
    return tabulate(rows, tablefmt="simple")


def print_dashboard(
    positions: list[dict[str, Any]],
    equity: float = 0.0,
    starting_equity: float = 10000.0,
    num_cycles: int = 0,
) -> None:
    """Print the full dashboard to stdout."""
    print("\n" + "═" * 55)
    print("  BTC WHEEL BOT — LIVE DASHBOARD")
    print("═" * 55)
    print("\n  ACCOUNT SUMMARY")
    print(render_account_summary(equity, starting_equity, num_cycles))
    print("\n  OPEN POSITIONS")
    print(render_position_table(positions))
    print()


if __name__ == "__main__":
    # Demo with placeholder data
    demo_positions = [
        {
            "instrument_name": "BTC-28JUN24-60000-P",
            "option_type": "put",
            "strike": 60000,
            "delta": -0.22,
            "entry_price": 450.0,
            "unrealised_pnl": 85.50,
            "dte": 12,
        }
    ]
    print_dashboard(demo_positions, equity=10535.50, starting_equity=10000.0, num_cycles=3)
