"""
backtester.py — Historical simulation of the BTC wheel strategy.

Uses only free Deribit public endpoints:
  - BTC-PERPETUAL OHLCV via get_tradingview_chart_data (1D candles)
  - BTC historical IV via get_historical_volatility

Simulation logic:
  - For each weekly period in the lookback window:
      1. Check IV rank (rolling 52-week window)
      2. If IV rank > threshold: "sell" an OTM put at approx_otm_offset below ATM
      3. Collect simulated premium = spot × premium_fraction
      4. At expiry: if spot < strike → assigned (loss); else → full premium kept
  - Alternate put/call each cycle
  - Apply risk rules: skip if drawdown limit hit

Output:
  - Console table of per-cycle results
  - Equity curve plot saved to config backtest.results_image
  - CSV of all simulated trades
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend — safe for servers
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from loguru import logger

from config import cfg
from deribit_client import DeribitPublicREST
from risk_manager import RiskManager

# ── Data models ────────────────────────────────────────────────────────────────


@dataclass
class BacktestTrade:
    cycle_num: int
    open_date: str
    close_date: str
    option_type: str      # "put" | "call"
    strike: float
    spot_at_open: float
    spot_at_close: float
    premium_usd: float    # premium received
    pnl_usd: float        # net P&L after assignment/expiry
    assigned: bool        # True if option expired ITM
    equity_after: float
    iv_rank: float
    dte: int


@dataclass
class BacktestResults:
    trades: list[BacktestTrade]
    equity_curve: list[float]
    dates: list[datetime]
    sharpe_ratio: float
    max_drawdown_pct: float
    total_return_pct: float
    win_rate_pct: float
    avg_premium_yield_pct: float
    num_cycles: int
    starting_equity: float
    ending_equity: float


# ── Backtester ─────────────────────────────────────────────────────────────────


class Backtester:
    """
    Simulates the wheel strategy over historical BTC price + IV data.

    All data is fetched from Deribit public endpoints.
    No authentication or live orders are used.
    """

    WEEKLY_SECONDS = 7 * 24 * 3600

    def __init__(self) -> None:
        self._rest = DeribitPublicREST(timeout=15)
        self._risk = RiskManager()

    # ── Data fetching ─────────────────────────────────────────────────────────

    def _fetch_btc_ohlcv(self, lookback_months: int) -> pd.DataFrame:
        """
        Download BTC-PERPETUAL daily candles for the lookback period.
        Fetches in 30-day chunks to respect Deribit API limits.
        """
        logger.info(f"Fetching BTC-PERPETUAL OHLCV ({lookback_months} months)...")
        now = int(time.time())
        start = now - lookback_months * 30 * 86400

        all_candles: list[dict] = []
        chunk_start = start
        chunk_days = 90  # fetch 90 days at a time

        while chunk_start < now:
            chunk_end = min(chunk_start + chunk_days * 86400, now)
            try:
                candles = self._rest.get_tradingview_chart_data(
                    instrument_name="BTC-PERPETUAL",
                    resolution=1440,      # 1D candles (1440 minutes)
                    start_timestamp=chunk_start,
                    end_timestamp=chunk_end,
                )
                all_candles.extend(candles)
                logger.debug(
                    f"Fetched {len(candles)} candles "
                    f"({datetime.fromtimestamp(chunk_start).date()} → "
                    f"{datetime.fromtimestamp(chunk_end).date()})"
                )
            except Exception as exc:
                logger.warning(f"OHLCV fetch error: {exc}")
            chunk_start = chunk_end
            time.sleep(0.2)  # polite rate limiting

        if not all_candles:
            raise RuntimeError("No OHLCV data fetched — check Deribit connectivity")

        df = pd.DataFrame(all_candles)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
        logger.info(f"OHLCV loaded: {len(df)} daily candles "
                    f"({df['datetime'].iloc[0].date()} → {df['datetime'].iloc[-1].date()})")
        return df

    def _fetch_iv_history(self) -> list[tuple[int, float]]:
        """Download BTC historical volatility (daily) from Deribit."""
        logger.info("Fetching BTC historical volatility...")
        try:
            iv_data = self._rest.get_historical_volatility(currency="BTC")
            logger.info(f"IV history loaded: {len(iv_data)} data points")
            return iv_data
        except Exception as exc:
            logger.warning(f"IV history fetch failed: {exc}. Using synthetic IV.")
            return []

    # ── IV rank calculation ───────────────────────────────────────────────────

    @staticmethod
    def _calculate_iv_rank_at(
        iv_history: list[tuple[int, float]],
        as_of_ts: int,        # Unix ms
        window_days: int = 365,
    ) -> float:
        """Calculate IV rank at a specific point in time."""
        # Filter to data available at as_of_ts
        available = [(ts, v) for ts, v in iv_history if ts <= as_of_ts]
        if len(available) < 10:
            return 0.5  # default to middle when insufficient history

        # Take rolling window
        recent = available[-window_days:]
        values = [v for _, v in recent]
        current_iv = values[-1]
        low_52w = min(values)
        high_52w = max(values)

        if high_52w == low_52w:
            return 0.0  # flat IV → no rank signal → don't trade
        return float(np.clip((current_iv - low_52w) / (high_52w - low_52w), 0.0, 1.0))

    # ── Simulation ────────────────────────────────────────────────────────────

    def _simulate_option_pnl(
        self,
        option_type: str,
        spot_at_open: float,
        spot_at_close: float,
        equity: float,
    ) -> tuple[float, float, float, bool]:
        """
        Simulate P&L for one option cycle.

        Returns:
            (strike, premium_usd, pnl_usd, assigned)
        """
        offset = cfg.backtest.approx_otm_offset

        if option_type == "put":
            strike = spot_at_open * (1.0 - offset)
        else:
            strike = spot_at_open * (1.0 + offset)

        # Position size: max_equity_per_leg fraction of equity
        # Treated as USD notional here for simplicity
        max_notional = equity * cfg.sizing.max_equity_per_leg
        contracts = max_notional / (strike * cfg.sizing.contract_size_btc)
        contracts = max(0.1, round(contracts, 1))

        # Premium received
        premium_per_contract = (
            spot_at_open
            * cfg.backtest.premium_fraction_of_spot
            * cfg.sizing.contract_size_btc
        )
        premium_total = premium_per_contract * contracts

        # Transaction costs
        transaction_cost = cfg.backtest.transaction_cost * contracts
        premium_net = premium_total - transaction_cost

        # Expiry outcome
        assigned = False
        if option_type == "put" and spot_at_close < strike:
            assigned = True
            loss_per_contract = (strike - spot_at_close) * cfg.sizing.contract_size_btc
            pnl_usd = premium_net - (loss_per_contract * contracts)
        elif option_type == "call" and spot_at_close > strike:
            assigned = True
            loss_per_contract = (spot_at_close - strike) * cfg.sizing.contract_size_btc
            pnl_usd = premium_net - (loss_per_contract * contracts)
        else:
            pnl_usd = premium_net

        return strike, premium_net, pnl_usd, assigned

    def run(self) -> BacktestResults:
        """
        Execute the full backtest.

        1. Download price + IV data
        2. Step weekly through the lookback period
        3. Simulate opening and closing one option per week
        4. Track equity, apply risk rules
        5. Return BacktestResults
        """
        bt = cfg.backtest
        lookback = bt.lookback_months

        # ── Data download ──────────────────────────────────────────────────────
        ohlcv_df = self._fetch_btc_ohlcv(lookback)
        iv_history = self._fetch_iv_history()

        # If IV history unavailable, synthesise from price volatility
        if not iv_history:
            logger.info("Synthesising IV from realised 30-day price volatility...")
            ohlcv_df["log_ret"] = np.log(ohlcv_df["close"] / ohlcv_df["close"].shift(1))
            ohlcv_df["rv_30d"] = ohlcv_df["log_ret"].rolling(30).std() * np.sqrt(365) * 100
            iv_history = [
                (int(row["datetime"].timestamp() * 1000), float(row["rv_30d"]))
                for _, row in ohlcv_df.dropna(subset=["rv_30d"]).iterrows()
            ]

        # ── Weekly simulation loop ─────────────────────────────────────────────
        equity = bt.starting_equity
        equity_curve: list[float] = [equity]
        dates: list[datetime] = [ohlcv_df["datetime"].iloc[0].to_pydatetime()]
        trades: list[BacktestTrade] = []
        cycle: str = cfg.strategy.initial_cycle
        dte = 7  # weekly

        # Step through data in 7-day increments
        min_index = 30  # need at least 30 days of history for IV rank
        i = min_index

        while i + dte < len(ohlcv_df):
            row_open = ohlcv_df.iloc[i]
            row_close = ohlcv_df.iloc[i + dte]

            open_ts = int(row_open["datetime"].timestamp() * 1000)
            close_ts = int(row_close["datetime"].timestamp() * 1000)
            spot_open = float(row_open["close"])
            spot_close = float(row_close["close"])

            open_dt = row_open["datetime"].to_pydatetime()
            close_dt = row_close["datetime"].to_pydatetime()

            # IV rank at open
            iv_rank = self._calculate_iv_rank_at(iv_history, open_ts)

            # Risk check: drawdown
            if not self._risk.check_drawdown(equity_curve):
                logger.info(f"[{open_dt.date()}] Skipping — drawdown limit reached")
                equity_curve.append(equity)
                dates.append(close_dt)
                i += dte
                continue

            # IV rank filter
            if iv_rank < cfg.strategy.iv_rank_threshold:
                logger.debug(f"[{open_dt.date()}] IV rank {iv_rank:.2%} too low — skip")
                equity_curve.append(equity)
                dates.append(close_dt)
                i += dte
                continue

            # Simulate option P&L
            strike, premium, pnl, assigned = self._simulate_option_pnl(
                option_type=cycle,
                spot_at_open=spot_open,
                spot_at_close=spot_close,
                equity=equity,
            )

            equity += pnl
            equity = max(equity, 0.0)  # floor at zero

            cycle_num = len(trades) + 1
            trade = BacktestTrade(
                cycle_num=cycle_num,
                open_date=str(open_dt.date()),
                close_date=str(close_dt.date()),
                option_type=cycle,
                strike=round(strike, 0),
                spot_at_open=round(spot_open, 0),
                spot_at_close=round(spot_close, 0),
                premium_usd=round(premium, 2),
                pnl_usd=round(pnl, 2),
                assigned=assigned,
                equity_after=round(equity, 2),
                iv_rank=round(iv_rank, 3),
                dte=dte,
            )
            trades.append(trade)
            equity_curve.append(equity)
            dates.append(close_dt)

            status = "ASSIGNED" if assigned else "EXPIRED"
            logger.info(
                f"[{open_dt.date()}→{close_dt.date()}] {cycle.upper()} "
                f"strike={strike:,.0f} | pnl=${pnl:+.2f} | equity=${equity:,.2f} | {status}"
            )

            # Alternate cycle
            cycle = "call" if cycle == "put" else "put"
            i += dte

        # ── Metrics ────────────────────────────────────────────────────────────
        results = self._compute_metrics(trades, equity_curve, dates, bt.starting_equity)
        return results

    def _compute_metrics(
        self,
        trades: list[BacktestTrade],
        equity_curve: list[float],
        dates: list[datetime],
        starting_equity: float,
    ) -> BacktestResults:
        """Calculate summary statistics from simulation output."""
        if not trades:
            logger.warning("No trades generated — check IV rank threshold or data range")
            return BacktestResults(
                trades=[], equity_curve=equity_curve, dates=dates,
                sharpe_ratio=0.0, max_drawdown_pct=0.0, total_return_pct=0.0,
                win_rate_pct=0.0, avg_premium_yield_pct=0.0, num_cycles=0,
                starting_equity=starting_equity, ending_equity=starting_equity,
            )

        eq = np.array(equity_curve)
        weekly_returns = np.diff(eq) / eq[:-1]
        weekly_returns = weekly_returns[np.isfinite(weekly_returns)]

        # Sharpe (annualised weekly)
        rf_weekly = cfg.backtest.risk_free_rate / 52
        excess = weekly_returns - rf_weekly
        sharpe = (
            float(np.mean(excess) / np.std(excess) * np.sqrt(52))
            if np.std(excess) > 0 else 0.0
        )

        # Max drawdown
        peak = np.maximum.accumulate(eq)
        drawdown = (peak - eq) / peak
        max_dd = float(np.max(drawdown))

        # Total return
        ending = equity_curve[-1]
        total_return = (ending - starting_equity) / starting_equity

        # Win rate
        wins = sum(1 for t in trades if t.pnl_usd > 0)
        win_rate = wins / len(trades) if trades else 0.0

        # Average premium yield (premium / equity at open)
        avg_yield = np.mean([
            t.premium_usd / (t.equity_after - t.pnl_usd)
            for t in trades
            if (t.equity_after - t.pnl_usd) > 0
        ]) if trades else 0.0

        return BacktestResults(
            trades=trades,
            equity_curve=equity_curve,
            dates=dates,
            sharpe_ratio=round(sharpe, 2),
            max_drawdown_pct=round(max_dd * 100, 2),
            total_return_pct=round(total_return * 100, 2),
            win_rate_pct=round(win_rate * 100, 1),
            avg_premium_yield_pct=round(float(avg_yield) * 100, 2),
            num_cycles=len(trades),
            starting_equity=starting_equity,
            ending_equity=round(ending, 2),
        )

    # ── Output ────────────────────────────────────────────────────────────────

    def print_summary(self, results: BacktestResults) -> None:
        """Print a formatted summary table to the console."""
        from tabulate import tabulate

        print("\n" + "═" * 60)
        print("  BTC WHEEL BOT — BACKTEST RESULTS")
        print("═" * 60)

        summary = [
            ["Lookback period", f"{cfg.backtest.lookback_months} months"],
            ["Total cycles", results.num_cycles],
            ["Starting equity", f"${results.starting_equity:,.2f}"],
            ["Ending equity",   f"${results.ending_equity:,.2f}"],
            ["Total return",    f"{results.total_return_pct:+.1f}%"],
            ["Sharpe ratio",    f"{results.sharpe_ratio:.2f}"],
            ["Max drawdown",    f"{results.max_drawdown_pct:.1f}%"],
            ["Win rate",        f"{results.win_rate_pct:.1f}%"],
            ["Avg premium/wk",  f"{results.avg_premium_yield_pct:.2f}%"],
        ]
        print(tabulate(summary, tablefmt="simple"))

        if results.trades:
            print("\n  LAST 10 TRADES")
            print("─" * 60)
            trade_rows = [
                [
                    t.cycle_num,
                    t.open_date,
                    t.option_type.upper(),
                    f"${t.strike:,.0f}",
                    f"${t.spot_at_close:,.0f}",
                    "✓ ASSIGNED" if t.assigned else "  expired",
                    f"${t.pnl_usd:+.2f}",
                    f"${t.equity_after:,.2f}",
                ]
                for t in results.trades[-10:]
            ]
            print(tabulate(
                trade_rows,
                headers=["#", "Open", "Type", "Strike", "Close", "Result", "P&L", "Equity"],
                tablefmt="simple",
            ))
        print()

    def save_csv(self, results: BacktestResults) -> None:
        """Save all trades to CSV file."""
        path = Path(cfg.backtest.results_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=BacktestTrade.__dataclass_fields__.keys())
            writer.writeheader()
            for trade in results.trades:
                writer.writerow(trade.__dict__)
        logger.info(f"Trade CSV saved: {path}")

    def save_plot(self, results: BacktestResults) -> None:
        """Save equity curve + drawdown chart as PNG."""
        if not results.dates or len(results.equity_curve) < 2:
            logger.warning("Not enough data to plot")
            return

        dates = results.dates
        equity = results.equity_curve
        eq_arr = np.array(equity)

        # Align lengths
        min_len = min(len(dates), len(equity))
        dates = dates[:min_len]
        eq_arr = eq_arr[:min_len]

        # Drawdown series
        peak = np.maximum.accumulate(eq_arr)
        drawdown = (peak - eq_arr) / peak * 100

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                        gridspec_kw={"height_ratios": [3, 1]})
        fig.patch.set_facecolor("#0d1117")
        for ax in (ax1, ax2):
            ax.set_facecolor("#161b22")
            ax.tick_params(colors="white")
            ax.spines[:].set_color("#30363d")

        # Equity curve
        ax1.plot(dates, eq_arr, color="#58a6ff", linewidth=1.5, label="Equity")
        ax1.axhline(results.starting_equity, color="#8b949e", linestyle="--",
                    linewidth=0.8, label="Starting equity")
        ax1.fill_between(dates, results.starting_equity, eq_arr,
                         where=(eq_arr >= results.starting_equity),
                         alpha=0.15, color="#3fb950")
        ax1.fill_between(dates, results.starting_equity, eq_arr,
                         where=(eq_arr < results.starting_equity),
                         alpha=0.15, color="#f85149")
        ax1.set_ylabel("Portfolio Value (USD)", color="white")
        ax1.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="white")
        ax1.set_title(
            f"BTC Wheel Bot Backtest | "
            f"Return: {results.total_return_pct:+.1f}% | "
            f"Sharpe: {results.sharpe_ratio:.2f} | "
            f"MaxDD: {results.max_drawdown_pct:.1f}%",
            color="white", pad=10,
        )

        # Drawdown
        ax2.fill_between(dates, 0, -drawdown, color="#f85149", alpha=0.7)
        ax2.set_ylabel("Drawdown %", color="white")
        ax2.set_xlabel("Date", color="white")
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right", color="white")

        plt.tight_layout()
        path = cfg.backtest.results_image
        plt.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close()
        logger.info(f"Equity curve saved: {path}")
        print(f"\n  Chart saved → {path}")
