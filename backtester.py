"""
backtester.py — Historical simulation of the BTC wheel strategy.

Uses Deribit public REST endpoints (no auth required):
  - get_tradingview_chart_data  : BTC-PERPETUAL daily OHLCV  (resolution="1D")
  - get_historical_volatility   : daily implied-volatility history (BTC)

Options are priced with Black-Scholes because Deribit does not expose a
free historical options-chain API.  IV comes from Deribit's own endpoint,
so the simulation uses the actual volatility regime observed each day.

Run:
    python main.py --mode=backtest
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import matplotlib
matplotlib.use("Agg")  # non-interactive -- safe for server / Docker
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from loguru import logger
from scipy.stats import norm

from config import Config, cfg
from deribit_client import DeribitPublicREST

Cycle = Literal["put", "call"]


# ── Black-Scholes helpers ──────────────────────────────────────────────────────

def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """European put price via Black-Scholes (USD)."""
    if T <= 1e-8:
        return max(K - S, 0.0)
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * np.sqrt(T)
    return float(K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1))


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """European call price via Black-Scholes (USD)."""
    if T <= 1e-8:
        return max(S - K, 0.0)
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * np.sqrt(T)
    return float(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))


def bs_put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Put delta -- negative number (e.g. -0.25)."""
    if T <= 1e-8:
        return -1.0 if S < K else 0.0
    return float(norm.cdf(_d1(S, K, T, r, sigma)) - 1.0)


def bs_call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Call delta -- positive number (e.g. +0.25)."""
    if T <= 1e-8:
        return 1.0 if S > K else 0.0
    return float(norm.cdf(_d1(S, K, T, r, sigma)))


def strike_for_put_delta(S: float, target: float, T: float, r: float, sigma: float) -> float:
    """
    Closed-form strike for a put with a given delta target (negative, e.g. -0.25).
    Derived from: delta_put = N(d1) - 1  =>  d1 = N_inv(target + 1).
    """
    d1 = float(norm.ppf(target + 1.0))
    return float(np.exp(np.log(S) - d1 * sigma * np.sqrt(T) + (r + 0.5 * sigma**2) * T))


def strike_for_call_delta(S: float, target: float, T: float, r: float, sigma: float) -> float:
    """
    Closed-form strike for a call with a given delta target (positive, e.g. +0.25).
    Derived from: delta_call = N(d1)  =>  d1 = N_inv(target).
    """
    d1 = float(norm.ppf(target))
    return float(np.exp(np.log(S) - d1 * sigma * np.sqrt(T) + (r + 0.5 * sigma**2) * T))


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    cycle_num: int
    open_date: str
    close_date: str
    option_type: str        # "put" | "call"
    strike: float
    spot_at_open: float
    spot_at_close: float
    dte: int
    entry_iv: float
    premium_usd: float      # total premium received (per-contract * contracts)
    exit_value_usd: float   # option value at close (per-contract * contracts)
    pnl_usd: float          # net P&L (premium - exit - costs)
    itm_at_expiry: bool
    rolled: bool
    roll_reason: str
    contracts: float
    equity_after: float
    iv_rank: float


@dataclass
class BacktestResults:
    trades: list[BacktestTrade]
    equity_curve: list[float]
    dates: list[datetime]
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    total_return_pct: float
    annualized_return_pct: float
    win_rate_pct: float
    avg_premium_yield_pct: float
    num_cycles: int
    starting_equity: float
    ending_equity: float


# ── Backtester ─────────────────────────────────────────────────────────────────

class Backtester:
    """
    Simulates the BTC wheel strategy over historical Deribit data.

    Pricing:   Black-Scholes with Deribit's historical IV.
    Data:      BTC-PERPETUAL daily OHLCV + historical_volatility (public REST).
    No auth:   backtester never touches order endpoints.
    """

    def __init__(self, config: Config | None = None) -> None:
        self._cfg = config or cfg
        self._rest = DeribitPublicREST(timeout=20)

    # ── Data layer ─────────────────────────────────────────────────────────────

    def _fetch_prices(self) -> pd.DataFrame:
        """
        Download BTC-PERPETUAL daily bars.
        Extra 12 months is fetched so the IV-rank rolling window has data
        before the simulation period begins.
        """
        lookback_days = self._cfg.backtest.lookback_months * 32 + 380
        end_ts   = int(time.time())
        start_ts = end_ts - lookback_days * 86_400

        logger.info(
            f"Fetching BTC-PERPETUAL daily bars "
            f"({self._cfg.backtest.lookback_months}m simulation + 12m IV window)..."
        )
        raw = self._rest._get("get_tradingview_chart_data", {
            "instrument_name":  "BTC-PERPETUAL",
            "start_timestamp":  start_ts * 1_000,
            "end_timestamp":    end_ts   * 1_000,
            "resolution":       "1D",
        })
        if not raw or raw.get("status") == "no_data":
            raise RuntimeError("Deribit returned no price data for BTC-PERPETUAL")

        df = pd.DataFrame({
            "date":   pd.to_datetime(raw["ticks"], unit="ms", utc=True).normalize(),
            "open":   pd.array(raw["open"],   dtype=float),
            "high":   pd.array(raw["high"],   dtype=float),
            "low":    pd.array(raw["low"],    dtype=float),
            "close":  pd.array(raw["close"],  dtype=float),
            "volume": pd.array(raw["volume"], dtype=float),
        })
        df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
        logger.info(
            f"  -> {len(df)} bars "
            f"[{df['date'].iloc[0].date()} ... {df['date'].iloc[-1].date()}]"
        )
        return df

    def _fetch_iv(self) -> pd.DataFrame:
        """
        Download Deribit's BTC historical implied volatility (daily %).
        Returns empty DataFrame when the endpoint has insufficient history.
        """
        logger.info("Fetching BTC historical implied volatility...")
        try:
            raw = self._rest._get("get_historical_volatility", {"currency": "BTC"})
            df  = pd.DataFrame(raw, columns=["ts_ms", "iv"])
            df["date"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True).dt.normalize()
            df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
            logger.info(
                f"  -> {len(df)} Deribit IV points "
                f"[{df['date'].iloc[0].date()} ... {df['date'].iloc[-1].date()}]"
            )
            return df[["date", "iv"]].copy()
        except Exception as exc:
            logger.warning(f"IV fetch failed: {exc}")
            return pd.DataFrame(columns=["date", "iv"])

    def _synthesise_iv(self, prices: pd.DataFrame) -> pd.DataFrame:
        """
        Compute realised volatility from OHLCV data as IV proxy.

        Uses Garman-Klass estimator (more efficient than close-to-close)
        then annualises and scales by 1.25 to approximate implied vol
        (BTC implied is typically 20-30% above realised).
        """
        p = prices.sort_values("date").copy()

        log_hl = np.log(p["high"] / p["low"])
        log_co = np.log(p["close"] / p["open"])
        gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
        p["rv_daily"] = np.sqrt(gk.rolling(30, min_periods=5).mean() * 252) * 100

        # IV = 1.25 × realised vol (typical BTC vol-of-vol premium)
        p["iv"] = p["rv_daily"] * 1.25

        result = p[["date", "iv"]].dropna().reset_index(drop=True)
        logger.info(
            f"  -> {len(result)} synthesised IV points "
            f"(Garman-Klass, IV = 1.25 × RV)"
        )
        return result

    def _build_dataset(self) -> pd.DataFrame:
        """Merge price + IV, compute rolling IV rank (0-100)."""
        prices = self._fetch_prices()
        iv_df  = self._fetch_iv()

        # Deribit's endpoint often returns only 2–4 weeks of data;
        # fall back to realised-vol proxy when coverage is short.
        if len(iv_df) < 60:
            logger.warning(
                f"Deribit IV history only {len(iv_df)} days — "
                "synthesising from 30-day Garman-Klass realised vol"
            )
            iv_df = self._synthesise_iv(prices)

        df = (
            pd.merge(prices, iv_df, on="date", how="inner")
            .sort_values("date")
            .reset_index(drop=True)
        )

        window  = min(365, len(df) - 1)
        min_per = min(30, window)

        def _iv_rank(x: pd.Series) -> float:
            lo, hi = x.min(), x.max()
            return (x.iloc[-1] - lo) / (hi - lo) * 100.0 if hi > lo else 50.0

        df["iv_rank"] = df["iv"].rolling(window, min_periods=min_per).apply(
            _iv_rank, raw=False
        )
        df = df.dropna(subset=["iv_rank"]).reset_index(drop=True)

        # Trim to requested simulation horizon
        sim_start = df["date"].iloc[-1] - pd.DateOffset(
            months=self._cfg.backtest.lookback_months
        )
        df = df[df["date"] >= sim_start].reset_index(drop=True)

        logger.info(
            f"Simulation dataset: {len(df)} days "
            f"[{df['date'].iloc[0].date()} ... {df['date'].iloc[-1].date()}]"
        )
        return df

    # ── Pricing helpers ────────────────────────────────────────────────────────

    def _price(self, cyc: Cycle, S: float, K: float, T: float, iv: float) -> float:
        r, sig = self._cfg.backtest.risk_free_rate, iv / 100.0
        return bs_put_price(S, K, T, r, sig) if cyc == "put" else bs_call_price(S, K, T, r, sig)

    def _delta_abs(self, cyc: Cycle, S: float, K: float, T: float, iv: float) -> float:
        r, sig = self._cfg.backtest.risk_free_rate, iv / 100.0
        T = max(T, 1e-8)
        fn = bs_put_delta if cyc == "put" else bs_call_delta
        return abs(fn(S, K, T, r, sig))

    def _target_strike(self, cyc: Cycle, S: float, T: float, iv: float) -> float:
        r, sig = self._cfg.backtest.risk_free_rate, iv / 100.0
        mid = (
            self._cfg.strategy.target_delta_min
            + self._cfg.strategy.target_delta_max
        ) / 2.0
        if cyc == "put":
            return strike_for_put_delta(S, -mid, T, r, sig)
        return strike_for_call_delta(S, mid, T, r, sig)

    def _size(self, equity: float, strike: float) -> float:
        max_notional = equity * self._cfg.sizing.max_equity_per_leg
        collateral   = strike * self._cfg.sizing.contract_size_btc
        if collateral <= 0:
            return 0.0
        raw = max_notional / collateral
        return max(0.1, round(raw * 10) / 10)   # nearest 0.1

    def _dte(self) -> int:
        pref = self._cfg.strategy.expiry_preference
        return 28 if (pref and pref[0] == "monthly") else 7

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self) -> BacktestResults:
        """Execute the full backtest, fetching its own data."""
        return self._simulate(self._build_dataset())

    def run_with_data(
        self,
        ohlcv_df: pd.DataFrame,
        iv_history: list,
        iv_window: int = 365,
    ) -> BacktestResults:
        """
        Run simulation using pre-fetched data (used by optimizer workers).

        Parameters
        ----------
        ohlcv_df   : DataFrame with columns [date, open, high, low, close, volume];
                     date must be UTC-normalised datetime64.
        iv_history : List of [ts_ms, iv] pairs from Deribit get_historical_volatility.
                     Pass an empty list to synthesise IV from realised vol instead.
        iv_window  : Rolling window (days) for IV-rank calculation.  Overrides the
                     config default so the optimizer can tune this independently.
        """
        # ── Build IV DataFrame ────────────────────────────────────────────
        # Note: Deribit get_historical_volatility returns intra-day (hourly)
        # data, so len(iv_history) may be large but daily unique rows may be
        # very few. After deduplication check we have ≥ 60 daily rows;
        # otherwise fall back to synthesised IV from realised volatility.
        iv_df = None
        if iv_history and len(iv_history) >= 60:
            _raw = pd.DataFrame(iv_history, columns=["ts_ms", "iv"])
            _raw["date"] = (
                pd.to_datetime(_raw["ts_ms"], unit="ms", utc=True).dt.normalize()
            )
            _daily = (
                _raw.sort_values("date")
                .drop_duplicates("date")
                .reset_index(drop=True)[["date", "iv"]]
                .copy()
            )
            if len(_daily) >= 60:
                iv_df = _daily
            else:
                logger.debug(
                    f"Deribit IV history has only {len(_daily)} daily rows after "
                    f"dedup — falling back to synthesised IV"
                )
        if iv_df is None:
            iv_df = self._synthesise_iv(ohlcv_df)

        # ── Merge price + IV ──────────────────────────────────────────────
        df = (
            pd.merge(ohlcv_df, iv_df, on="date", how="inner")
            .sort_values("date")
            .reset_index(drop=True)
        )

        # ── Rolling IV rank (configurable window) ─────────────────────────
        window  = min(iv_window, max(len(df) - 1, 1))
        min_per = min(30, window)

        def _iv_rank_fn(x: pd.Series) -> float:
            lo, hi = x.min(), x.max()
            return (x.iloc[-1] - lo) / (hi - lo) * 100.0 if hi > lo else 50.0

        df["iv_rank"] = df["iv"].rolling(window, min_periods=min_per).apply(
            _iv_rank_fn, raw=False
        )
        df = df.dropna(subset=["iv_rank"]).reset_index(drop=True)

        if len(df) == 0:
            raise ValueError("run_with_data: empty dataset after IV-rank filtering")

        # ── Trim to simulation horizon ─────────────────────────────────────
        sim_start = df["date"].iloc[-1] - pd.DateOffset(
            months=self._cfg.backtest.lookback_months
        )
        df = df[df["date"] >= sim_start].reset_index(drop=True)

        return self._simulate(df)

    def _build_ranked_df(
        self,
        ohlcv_df: pd.DataFrame,
        iv_history: list,
        iv_window: int = 365,
    ) -> pd.DataFrame:
        """
        Build the merged + IV-ranked DataFrame from pre-fetched data WITHOUT
        trimming to lookback_months. Used by walk-forward and Monte Carlo
        validation so callers can slice the data themselves.
        """
        iv_df = None
        if iv_history and len(iv_history) >= 60:
            _raw = pd.DataFrame(iv_history, columns=["ts_ms", "iv"])
            _raw["date"] = pd.to_datetime(_raw["ts_ms"], unit="ms", utc=True).dt.normalize()
            _daily = (
                _raw.sort_values("date")
                .drop_duplicates("date")
                .reset_index(drop=True)[["date", "iv"]]
                .copy()
            )
            if len(_daily) >= 60:
                iv_df = _daily
        if iv_df is None:
            iv_df = self._synthesise_iv(ohlcv_df)

        df = (
            pd.merge(ohlcv_df, iv_df, on="date", how="inner")
            .sort_values("date")
            .reset_index(drop=True)
        )

        window  = min(iv_window, max(len(df) - 1, 1))
        min_per = min(30, window)

        def _iv_rank_fn(x: pd.Series) -> float:
            lo, hi = x.min(), x.max()
            return (x.iloc[-1] - lo) / (hi - lo) * 100.0 if hi > lo else 50.0

        df["iv_rank"] = df["iv"].rolling(window, min_periods=min_per).apply(
            _iv_rank_fn, raw=False
        )
        return df.dropna(subset=["iv_rank"]).reset_index(drop=True)

    def _simulate(self, df: pd.DataFrame) -> BacktestResults:
        """
        Core simulation loop — shared by run() and run_with_data().

        Expects a DataFrame with columns: date, close, iv, iv_rank.

        DRAWDOWN FIX (2026-04): Previously, `equity` was only updated when a
        position closed (expiry or roll breach), so equity_curve stayed flat
        while a short put was open — even if BTC dropped 30% intra-cycle.
        This caused max_drawdown to be severely understated (e.g. -2% instead
        of the true -20%+ when a deep-ITM put was held to expiry).

        Fix: track `_mtm_unreal` = unrealized P&L from the open leg each day.
        Append `equity + _mtm_unreal` (mark-to-market equity) to equity_curve
        and use it for the drawdown guard, so peak-to-trough is computed on the
        true running portfolio value, not just closed-trade P&L.
        """
        equity      = self._cfg.backtest.starting_equity
        peak_equity = equity
        cycle: Cycle = self._cfg.strategy.initial_cycle

        # iv_rank in data is 0-100; config threshold stored as 0-1
        iv_thresh = self._cfg.strategy.iv_rank_threshold * 100.0

        # Regime filter: pre-compute MA of close prices if enabled
        use_regime     = int(getattr(self._cfg.strategy, "use_regime_filter", 0))
        regime_ma_days = int(getattr(self._cfg.strategy, "regime_ma_days", 50))
        _price_ma: np.ndarray | None = None
        if use_regime:
            _price_ma = df["close"].rolling(regime_ma_days, min_periods=1).mean().values

        leg: dict | None = None
        equity_curve: list[float]    = [equity]
        dates:        list[datetime] = []
        trades:       list[BacktestTrade] = []

        logger.info("=" * 60)
        logger.info("BACKTEST START")
        logger.info(f"  Equity    : ${equity:,.0f}")
        logger.info(f"  IV thresh : {iv_thresh:.0f}%  (config={self._cfg.strategy.iv_rank_threshold})")
        mid_d = (self._cfg.strategy.target_delta_min + self._cfg.strategy.target_delta_max) / 2.0
        logger.info(f"  Delta tgt : +/-{mid_d:.2f}")
        logger.info(f"  First leg : {cycle}")
        if use_regime:
            logger.info(f"  Regime MA : {regime_ma_days}d (filter ON)")
        logger.info("=" * 60)

        for idx, (_, row) in enumerate(df.iterrows()):
            date: datetime = row["date"].to_pydatetime()
            spot: float    = float(row["close"])
            iv:   float    = float(row["iv"])      # annualised % from Deribit
            ivr:  float    = float(row["iv_rank"]) # 0-100

            dates.append(date)

            if spot <= 0 or iv <= 0:
                equity_curve.append(equity)
                continue

            # Unrealized P&L from open position — non-zero only when leg is held
            # without action this day (set in the roll-check else branch below).
            _mtm_unreal: float = 0.0

            # ── Expiry settlement ──────────────────────────────────────────
            if leg is not None and date >= leg["expiry"]:
                exit_val  = self._price(leg["cycle"], spot, leg["strike"], 0.0, iv)
                gross_pnl = (leg["premium"] - exit_val) * leg["contracts"]
                pnl       = gross_pnl - self._cfg.backtest.transaction_cost * leg["contracts"]
                equity   += pnl

                itm = (leg["cycle"] == "put"  and spot < leg["strike"]) or \
                      (leg["cycle"] == "call" and spot > leg["strike"])

                trades.append(BacktestTrade(
                    cycle_num=len(trades) + 1,
                    open_date=str(leg["entry_date"].date()),
                    close_date=str(date.date()),
                    option_type=leg["cycle"],
                    strike=round(leg["strike"], 0),
                    spot_at_open=round(leg["entry_spot"], 0),
                    spot_at_close=round(spot, 0),
                    dte=leg["dte"],
                    entry_iv=round(leg["entry_iv"], 2),
                    premium_usd=round(leg["premium"] * leg["contracts"], 2),
                    exit_value_usd=round(exit_val * leg["contracts"], 2),
                    pnl_usd=round(pnl, 2),
                    itm_at_expiry=itm,
                    rolled=False,
                    roll_reason="",
                    contracts=leg["contracts"],
                    equity_after=round(equity, 2),
                    iv_rank=round(ivr, 1),
                ))
                tag = "ITM" if itm else "OTM"
                wnl = "WIN" if pnl >= 0 else "LOSS"
                logger.info(
                    f"[EXPIRY {date.date()}] {leg['cycle'].upper()} "
                    f"K={leg['strike']:,.0f} {tag}  {wnl} ${pnl:+,.0f}  "
                    f"equity=${equity:,.0f}"
                )
                cycle = "call" if leg["cycle"] == "put" else "put"
                leg   = None

            # ── In-trade roll checks ───────────────────────────────────────
            elif leg is not None:
                dte_left   = max((leg["expiry"] - date).days, 1)
                T_left     = dte_left / 365.0
                cur_val    = self._price(leg["cycle"], spot, leg["strike"], T_left, iv)
                cur_delta  = self._delta_abs(leg["cycle"], spot, leg["strike"], T_left, iv)
                unreal_pnl = (leg["premium"] - cur_val) * leg["contracts"]
                loss_pct   = -unreal_pnl / equity if unreal_pnl < 0 else 0.0

                roll_reason = ""
                if cur_delta > self._cfg.risk.max_adverse_delta:
                    roll_reason = "delta_breach"
                elif loss_pct > self._cfg.risk.max_loss_per_leg:
                    roll_reason = "loss_breach"

                if roll_reason:
                    pnl     = unreal_pnl - self._cfg.backtest.transaction_cost * leg["contracts"]
                    equity += pnl
                    trades.append(BacktestTrade(
                        cycle_num=len(trades) + 1,
                        open_date=str(leg["entry_date"].date()),
                        close_date=str(date.date()),
                        option_type=leg["cycle"],
                        strike=round(leg["strike"], 0),
                        spot_at_open=round(leg["entry_spot"], 0),
                        spot_at_close=round(spot, 0),
                        dte=leg["dte"],
                        entry_iv=round(leg["entry_iv"], 2),
                        premium_usd=round(leg["premium"] * leg["contracts"], 2),
                        exit_value_usd=round(cur_val * leg["contracts"], 2),
                        pnl_usd=round(pnl, 2),
                        itm_at_expiry=False,
                        rolled=True,
                        roll_reason=roll_reason,
                        contracts=leg["contracts"],
                        equity_after=round(equity, 2),
                        iv_rank=round(ivr, 1),
                    ))
                    logger.warning(
                        f"[ROLL {date.date()}] {leg['cycle'].upper()} "
                        f"K={leg['strike']:,.0f}  reason={roll_reason}  "
                        f"${pnl:+,.0f}  equity=${equity:,.0f}"
                    )
                    cycle = "call" if leg["cycle"] == "put" else "put"
                    leg   = None
                else:
                    # Leg held — capture unrealized P&L so the equity curve
                    # reflects the true mark-to-market portfolio value each day.
                    _mtm_unreal = unreal_pnl

            # ── Mark-to-market equity (closed equity + open unrealized) ────
            mtm_equity = equity + _mtm_unreal

            # ── Drawdown guard (uses MTM equity, not just closed P&L) ──────
            peak_equity = max(peak_equity, mtm_equity)
            drawdown    = (peak_equity - mtm_equity) / peak_equity
            if drawdown > self._cfg.risk.max_daily_drawdown:
                logger.warning(
                    f"[PAUSE {date.date()}] drawdown {drawdown:.1%} -- no new legs"
                )
                equity_curve.append(mtm_equity)
                continue

            # ── Open new leg ───────────────────────────────────────────────
            if leg is None and ivr >= iv_thresh:
                # Regime filter: skip opening when spot is below MA (bearish regime)
                if use_regime and _price_ma is not None and spot < _price_ma[idx]:
                    equity_curve.append(mtm_equity)
                    continue

                dte = self._dte()
                T   = dte / 365.0
                try:
                    strike  = self._target_strike(cycle, spot, T, iv)
                    premium = self._price(cycle, spot, strike, T, iv)
                except Exception as exc:
                    logger.debug(f"BS failed {date.date()}: {exc}")
                    equity_curve.append(mtm_equity)
                    continue

                if premium <= 0 or strike <= 0:
                    equity_curve.append(mtm_equity)
                    continue

                # Enforce minimum free equity buffer before opening.
                # Use the actual sized position to compute real collateral consumed.
                tentative_contracts = self._size(equity, strike)
                actual_collateral = tentative_contracts * strike * self._cfg.sizing.contract_size_btc
                min_free = getattr(self._cfg.sizing, "min_free_equity_fraction", 0.0)
                if min_free > 0 and equity > 0:
                    free_fraction_after = (equity - actual_collateral) / equity
                    if free_fraction_after < min_free:
                        equity_curve.append(mtm_equity)
                        continue

                contracts  = tentative_contracts
                expiry_dt  = date + timedelta(days=dte)
                prem_yield = (premium / strike) * 100

                leg = dict(
                    cycle=cycle, entry_date=date, expiry=expiry_dt,
                    strike=strike, premium=premium, contracts=contracts,
                    entry_spot=spot, entry_iv=iv, dte=dte,
                )
                logger.info(
                    f"[OPEN {date.date()}] {cycle.upper()} K={strike:,.0f} "
                    f"exp={expiry_dt.date()} DTE={dte} "
                    f"IV={iv:.1f}% IVR={ivr:.0f}% "
                    f"prem=${premium:,.2f}/ct yield={prem_yield:.2f}% x{contracts}"
                )

            equity_curve.append(mtm_equity)

        return self._metrics(equity, equity_curve, dates, trades)

    # ── Metrics ────────────────────────────────────────────────────────────────

    def _metrics(
        self,
        final_equity: float,
        curve: list[float],
        dates: list[datetime],
        trades: list[BacktestTrade],
    ) -> BacktestResults:
        eq     = np.array(curve, dtype=float)
        start  = self._cfg.backtest.starting_equity
        n_days = max((dates[-1] - dates[0]).days, 1) if len(dates) >= 2 else 1

        total_ret = (final_equity - start) / start
        ann_ret   = (1 + total_ret) ** (365 / n_days) - 1

        daily_ret = pd.Series(eq).pct_change().dropna()
        rf_daily  = self._cfg.backtest.risk_free_rate / 252
        excess    = daily_ret - rf_daily
        sqrt_252  = np.sqrt(252)

        sharpe  = float((excess.mean() / excess.std()) * sqrt_252) if excess.std() > 0 else 0.0
        down    = excess[excess < 0]
        sortino = float((excess.mean() / down.std()) * sqrt_252) if (len(down) > 0 and down.std() > 0) else 0.0

        peak   = np.maximum.accumulate(eq)
        max_dd = float(((eq - peak) / peak).min())

        wins   = [t for t in trades if t.pnl_usd >= 0]
        yields = [
            (t.premium_usd / (t.strike * t.contracts)) * 100
            for t in trades if t.strike > 0 and t.contracts > 0
        ]

        return BacktestResults(
            trades=trades,
            equity_curve=list(eq),
            dates=dates,
            sharpe_ratio=round(sharpe,   2),
            sortino_ratio=round(sortino,  2),
            max_drawdown_pct=round(max_dd * 100, 2),
            total_return_pct=round(total_ret * 100, 2),
            annualized_return_pct=round(ann_ret * 100, 2),
            win_rate_pct=round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
            avg_premium_yield_pct=round(float(np.mean(yields)), 2) if yields else 0.0,
            num_cycles=len(trades),
            starting_equity=start,
            ending_equity=round(final_equity, 2),
        )

    # ── Output ─────────────────────────────────────────────────────────────────

    def print_summary(self, results: BacktestResults) -> None:
        """Print formatted summary + last 15 trades to stdout."""
        try:
            from tabulate import tabulate
            _tab = True
        except ImportError:
            _tab = False

        W = 66
        print("\n" + "=" * W)
        print("  BTC WHEEL BOT -- BACKTEST RESULTS")
        print("=" * W)

        summary = [
            ["Lookback period",    f"{self._cfg.backtest.lookback_months} months"],
            ["Simulation days",    str(len(results.dates))],
            ["Total trades",       str(results.num_cycles)],
            ["Starting equity",    f"${results.starting_equity:,.2f}"],
            ["Ending equity",      f"${results.ending_equity:,.2f}"],
            ["Total return",       f"{results.total_return_pct:+.2f}%"],
            ["Annualised return",  f"{results.annualized_return_pct:+.2f}%"],
            ["Sharpe ratio",       f"{results.sharpe_ratio:.2f}"],
            ["Sortino ratio",      f"{results.sortino_ratio:.2f}"],
            ["Max drawdown",       f"{results.max_drawdown_pct:.2f}%"],
            ["Win rate",           f"{results.win_rate_pct:.1f}%"],
            ["Avg premium yield",  f"{results.avg_premium_yield_pct:.2f}% / contract"],
        ]

        if _tab:
            from tabulate import tabulate
            print(tabulate(summary, tablefmt="simple", colalign=("left", "right")))
        else:
            for row in summary:
                print(f"  {row[0]:<22} {row[1]:>16}")

        if results.trades:
            n_show = min(15, len(results.trades))
            print(f"\n  LAST {n_show} TRADES")
            print("-" * W)
            rows = [
                [
                    t.cycle_num,
                    t.open_date,
                    t.close_date,
                    t.option_type.upper(),
                    f"${t.strike:,.0f}",
                    f"${t.spot_at_close:,.0f}",
                    "ITM" if t.itm_at_expiry else ("ROLL" if t.rolled else "OTM"),
                    f"${t.pnl_usd:+,.2f}",
                    f"${t.equity_after:,.0f}",
                ]
                for t in results.trades[-n_show:]
            ]
            hdrs = ["#", "Open", "Close", "Type", "Strike", "Close Px",
                    "Result", "P&L", "Equity"]
            if _tab:
                from tabulate import tabulate
                print(tabulate(rows, headers=hdrs, tablefmt="simple"))
            else:
                print("  " + "  ".join(f"{h:>10}" for h in hdrs))
                for r in rows:
                    print("  " + "  ".join(f"{v:>10}" for v in r))

        print("\n" + "=" * W + "\n")

    def save_csv(self, results: BacktestResults) -> None:
        """Save all trades to CSV."""
        import csv
        from pathlib import Path
        path = Path(self._cfg.backtest.results_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = list(BacktestTrade.__dataclass_fields__.keys())
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for t in results.trades:
                w.writerow(t.__dict__)
        logger.info(f"Trades CSV -> {path}")

    def save_plot(self, results: BacktestResults) -> None:
        """Save equity-curve + drawdown chart as PNG."""
        if len(results.dates) < 2:
            logger.warning("Not enough data to plot")
            return

        n      = min(len(results.dates), len(results.equity_curve))
        dates  = results.dates[:n]
        eq_arr = np.array(results.equity_curve[:n], dtype=float)
        peak   = np.maximum.accumulate(eq_arr)
        dd_pct = (eq_arr - peak) / peak * 100

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(13, 7), sharex=True,
            gridspec_kw={"height_ratios": [3, 1]}
        )
        fig.patch.set_facecolor("#0d1117")
        for ax in (ax1, ax2):
            ax.set_facecolor("#161b22")
            ax.tick_params(colors="white")
            ax.yaxis.label.set_color("white")
            ax.xaxis.label.set_color("white")
            for spine in ax.spines.values():
                spine.set_color("#30363d")

        ax1.plot(dates, eq_arr, color="#58a6ff", linewidth=1.5, label="Equity")
        ax1.axhline(results.starting_equity, color="#8b949e", linestyle="--",
                    linewidth=0.8, label=f"Start ${results.starting_equity:,.0f}")
        ax1.fill_between(dates, results.starting_equity, eq_arr,
                         where=(eq_arr >= results.starting_equity),
                         alpha=0.15, color="#3fb950")
        ax1.fill_between(dates, results.starting_equity, eq_arr,
                         where=(eq_arr < results.starting_equity),
                         alpha=0.25, color="#f85149")
        ax1.set_ylabel("Portfolio Value (USD)", color="white")
        ax1.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="white", fontsize=8)
        ax1.set_title(
            f"BTC Wheel Strategy -- Backtest  |  "
            f"Return {results.total_return_pct:+.1f}%  |  "
            f"Ann {results.annualized_return_pct:+.1f}%  |  "
            f"Sharpe {results.sharpe_ratio:.2f}  |  "
            f"MaxDD {results.max_drawdown_pct:.1f}%  |  "
            f"Win {results.win_rate_pct:.0f}%  |  "
            f"{results.num_cycles} trades",
            color="white", pad=8, fontsize=9,
        )

        ax2.fill_between(dates, 0, dd_pct, color="#f85149", alpha=0.6)
        ax2.plot(dates, dd_pct, color="#da3633", linewidth=0.8)
        ax2.set_ylabel("Drawdown %", color="white")
        ax2.set_xlabel("Date", color="white")

        for ax in (ax1, ax2):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=35, ha="right",
                     color="white", fontsize=7)

        plt.tight_layout()
        path = self._cfg.backtest.results_image
        plt.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close()
        logger.info(f"Equity chart -> {path}")
        print(f"\n  Chart saved -> {path}")
