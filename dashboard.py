"""
dashboard.py -- Console dashboard showing live position state.

Uses `rich` for colour table rendering.  Falls back to plain text
if rich is not installed.

Usage (within bot.py tick or standalone):
    from dashboard import Dashboard
    dash = Dashboard()
    dash.render(position, equity, next_expiry, greeks)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger


def _try_rich():
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        return Console(), Table, box
    except ImportError:
        return None, None, None


class Dashboard:
    """
    Renders a console table with current bot state.

    Columns:
        Field / Value layout, refreshed each poll.
    """

    def __init__(self) -> None:
        self._console, self._Table, self._box = _try_rich()

    def render(
        self,
        instrument: str | None,
        option_type: str | None,
        strike: float | None,
        delta: float | None,
        mark_price: float | None,
        mark_iv: float | None,
        theta: float | None,
        vega: float | None,
        next_expiry: datetime | None,
        equity_usd: float,
        unrealised_pnl_usd: float,
        btc_price: float,
        iv_rank: float,
        mode: str = "paper",
    ) -> None:
        """Print a full status panel to the console."""
        now = datetime.now(timezone.utc)
        dte = (next_expiry - now).days if next_expiry else None

        if self._console and self._Table and self._box:
            self._render_rich(
                instrument, option_type, strike, delta, mark_price, mark_iv,
                theta, vega, dte, equity_usd, unrealised_pnl_usd, btc_price,
                iv_rank, mode, now,
            )
        else:
            self._render_plain(
                instrument, option_type, strike, delta, mark_price, mark_iv,
                theta, vega, dte, equity_usd, unrealised_pnl_usd, btc_price,
                iv_rank, mode, now,
            )

    def _render_rich(self, instrument, option_type, strike, delta, mark_price,
                     mark_iv, theta, vega, dte, equity_usd, unrealised_pnl_usd,
                     btc_price, iv_rank, mode, now) -> None:
        from rich.console import Console
        from rich.table import Table
        from rich import box

        table = Table(
            title=f"BTC Wheel Bot  [{mode.upper()}]  {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Field",    style="bold", width=22)
        table.add_column("Value",    width=24)

        def _fmt(v, fmt="{}", na="--"):
            return fmt.format(v) if v is not None else na

        rows = [
            ("BTC Spot",         f"${btc_price:,.0f}" if btc_price else "--"),
            ("IV Rank",          f"{iv_rank:.0f}%" if iv_rank else "--"),
            ("Equity (USD)",     f"${equity_usd:,.2f}"),
            ("Unrealised P&L",   f"${unrealised_pnl_usd:+,.2f}" if unrealised_pnl_usd else "--"),
            ("─" * 20,          "─" * 22),
            ("Position",         instrument or "FLAT"),
            ("Type",             (option_type or "").upper() or "--"),
            ("Strike",           f"${strike:,.0f}" if strike else "--"),
            ("Mark IV",          f"{mark_iv:.1f}%" if mark_iv else "--"),
            ("Delta",            f"{delta:.3f}" if delta is not None else "--"),
            ("Theta ($/day)",    f"${theta:.2f}" if theta is not None else "--"),
            ("Vega (per 1% IV)", f"${vega:.2f}" if vega is not None else "--"),
            ("DTE",              f"{dte}d" if dte is not None else "--"),
        ]

        pnl_style = "[green]" if unrealised_pnl_usd and unrealised_pnl_usd >= 0 else "[red]"
        for field, value in rows:
            if "P&L" in field and unrealised_pnl_usd is not None:
                table.add_row(field, f"{pnl_style}{value}[/]")
            else:
                table.add_row(field, value)

        self._console.clear()
        self._console.print(table)

    def _render_plain(self, instrument, option_type, strike, delta, mark_price,
                      mark_iv, theta, vega, dte, equity_usd, unrealised_pnl_usd,
                      btc_price, iv_rank, mode, now) -> None:
        sep = "-" * 42
        print(f"\n{sep}")
        print(f"  BTC Wheel Bot [{mode.upper()}]  {now.strftime('%H:%M:%S UTC')}")
        print(sep)
        print(f"  BTC Spot    : ${btc_price:,.0f}" if btc_price else "  BTC Spot    : --")
        print(f"  IV Rank     : {iv_rank:.0f}%" if iv_rank else "  IV Rank     : --")
        print(f"  Equity      : ${equity_usd:,.2f}")
        if unrealised_pnl_usd is not None:
            print(f"  Unreal P&L  : ${unrealised_pnl_usd:+,.2f}")
        print(sep)
        print(f"  Position    : {instrument or 'FLAT'}")
        if instrument:
            print(f"  Type/Strike : {(option_type or '').upper()} ${strike:,.0f}" if strike else "")
            print(f"  Delta       : {delta:.3f}" if delta is not None else "  Delta       : --")
            print(f"  Mark IV     : {mark_iv:.1f}%" if mark_iv else "  Mark IV     : --")
            print(f"  DTE         : {dte}d" if dte is not None else "  DTE         : --")
        print(sep + "\n")
