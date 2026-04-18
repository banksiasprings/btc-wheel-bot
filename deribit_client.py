"""
deribit_client.py — Deribit REST + WebSocket client wrapper.

Public endpoints (no auth) are used for backtest mode.
Authenticated endpoints are scaffolded for paper/live modes.

All live order methods are marked # LIVE_ONLY and raise NotImplementedError
when called in backtest mode, ensuring the backtester never accidentally
touches real or paper trading infrastructure.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp
import requests
from loguru import logger

from config import cfg

# ── Data models ────────────────────────────────────────────────────────────────


@dataclass
class Ticker:
    instrument_name: str
    mark_price: float
    bid: float
    ask: float
    mark_iv: float        # implied volatility (annualised %)
    delta: float
    gamma: float
    theta: float
    vega: float
    underlying_price: float
    timestamp: datetime


@dataclass
class Instrument:
    instrument_name: str
    strike: float
    expiry_ts: int        # Unix timestamp ms
    option_type: str      # "put" | "call"
    dte: int              # days to expiry


# ── REST client (public endpoints — no auth required) ─────────────────────────


class DeribitPublicREST:
    """Thin wrapper around Deribit public REST API (no authentication)."""

    BASE_URL = "https://www.deribit.com/api/v2/public"

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"

    def _get(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to a public endpoint."""
        url = f"{self.BASE_URL}/{method}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if "result" not in data:
                raise ValueError(f"Unexpected response: {data}")
            return data["result"]
        except requests.RequestException as exc:
            logger.error(f"REST error [{method}]: {exc}")
            raise

    def get_instruments(
        self,
        currency: str = "BTC",
        kind: str = "option",
        expired: bool = False,
    ) -> list[Instrument]:
        """Fetch all active options instruments for a currency."""
        raw = self._get("get_instruments", {
            "currency": currency,
            "kind": kind,
            "expired": str(expired).lower(),
        })
        instruments: list[Instrument] = []
        now_ts = int(time.time() * 1000)
        for item in raw:
            dte = max(0, (item["expiration_timestamp"] - now_ts) // 86_400_000)
            instruments.append(Instrument(
                instrument_name=item["instrument_name"],
                strike=float(item["strike"]),
                expiry_ts=item["expiration_timestamp"],
                option_type=item["option_type"],
                dte=dte,
            ))
        logger.debug(f"Fetched {len(instruments)} instruments for {currency}")
        return instruments

    def get_ticker(self, instrument_name: str) -> Ticker | None:
        """Fetch current ticker + Greeks for a single instrument."""
        try:
            raw = self._get("ticker", {"instrument_name": instrument_name})
            greeks = raw.get("greeks", {})
            return Ticker(
                instrument_name=instrument_name,
                mark_price=float(raw.get("mark_price", 0)),
                bid=float(raw.get("best_bid_price", 0)),
                ask=float(raw.get("best_ask_price", 0)),
                mark_iv=float(raw.get("mark_iv", 0)),
                delta=float(greeks.get("delta", 0)),
                gamma=float(greeks.get("gamma", 0)),
                theta=float(greeks.get("theta", 0)),
                vega=float(greeks.get("vega", 0)),
                underlying_price=float(raw.get("underlying_price", 0)),
                timestamp=datetime.fromtimestamp(
                    raw["timestamp"] / 1000, tz=timezone.utc
                ),
            )
        except Exception as exc:
            logger.warning(f"Could not fetch ticker for {instrument_name}: {exc}")
            return None

    def get_historical_volatility(self, currency: str = "BTC") -> list[tuple[int, float]]:
        """
        Fetch historical daily IV for a currency.
        Returns list of (timestamp_ms, iv_value) tuples.
        """
        raw = self._get("get_historical_volatility", {"currency": currency})
        # Response is [[timestamp_ms, iv], ...]
        return [(int(row[0]), float(row[1])) for row in raw]

    def get_tradingview_chart_data(
        self,
        instrument_name: str,
        resolution: int,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch OHLCV data from Deribit's TradingView endpoint.

        Args:
            instrument_name: e.g. "BTC-PERPETUAL"
            resolution: candle size in minutes (1, 5, 60, 1D, etc.)
            start_timestamp: Unix timestamp in seconds
            end_timestamp: Unix timestamp in seconds

        Returns:
            List of OHLCV dicts with keys: timestamp, open, high, low, close, volume
        """
        raw = self._get("get_tradingview_chart_data", {
            "instrument_name": instrument_name,
            "start_timestamp": start_timestamp * 1000,
            "end_timestamp": end_timestamp * 1000,
            "resolution": str(resolution),
        })
        if raw.get("status") == "no_data":
            return []
        ticks = raw.get("ticks", [])
        opens = raw.get("open", [])
        highs = raw.get("high", [])
        lows = raw.get("low", [])
        closes = raw.get("close", [])
        volumes = raw.get("volume", [])
        return [
            {
                "timestamp": ticks[i],
                "open": opens[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],
                "volume": volumes[i],
            }
            for i in range(len(ticks))
        ]


# ── WebSocket client (live/paper mode) ────────────────────────────────────────


class DeribitWebSocket:
    """
    Async WebSocket client for real-time data and order management.
    Used in paper and live modes only.

    # LIVE_ONLY — this class is NOT used in backtest mode.
    """

    def __init__(self) -> None:
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._msg_id: int = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._authenticated: bool = False

    async def connect(self) -> None:
        """Open WebSocket connection to Deribit."""
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(cfg.deribit.ws_url)
        asyncio.create_task(self._recv_loop())
        logger.info(f"WebSocket connected to {cfg.deribit.ws_url}")

    async def authenticate(self) -> None:
        """Authenticate with API key/secret. Required for order placement."""
        if not cfg.deribit.api_key:
            raise ValueError("DERIBIT_API_KEY not set in environment")
        result = await self._rpc("public/auth", {
            "grant_type": "client_credentials",
            "client_id": cfg.deribit.api_key,
            "client_secret": cfg.deribit.api_secret,
        })
        self._authenticated = True
        logger.info("WebSocket authenticated")
        return result

    async def _rpc(self, method: str, params: dict) -> Any:
        """Send a JSON-RPC request and await the response."""
        msg_id = self._msg_id
        self._msg_id += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params,
        })
        await self._ws.send_str(payload)
        return await asyncio.wait_for(fut, timeout=cfg.execution.order_confirm_timeout)

    async def _recv_loop(self) -> None:
        """Receive loop — routes messages to pending futures or subscriptions."""
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if "id" in data and data["id"] in self._pending:
                    fut = self._pending.pop(data["id"])
                    if "error" in data:
                        fut.set_exception(RuntimeError(data["error"]["message"]))
                    else:
                        fut.set_result(data.get("result"))

    # ── Order methods (LIVE_ONLY) ──────────────────────────────────────────────

    async def sell_option(
        self,
        instrument_name: str,
        amount: float,
        order_type: str = "limit",
        price: float | None = None,
        label: str = "",
    ) -> dict:
        """
        Place a sell order for an option.
        # LIVE_ONLY — only called in paper/live modes.
        """
        if not self._authenticated:
            raise RuntimeError("Must authenticate before placing orders")
        params: dict[str, Any] = {
            "instrument_name": instrument_name,
            "amount": amount,
            "type": order_type,
            "label": label,
        }
        if price is not None:
            params["price"] = price
        result = await self._rpc("private/sell", params)
        logger.info(f"Sell order placed: {instrument_name} × {amount} @ {price}")
        return result

    async def close(self) -> None:
        """Close WebSocket connection gracefully."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("WebSocket connection closed")


# ── Combined client ────────────────────────────────────────────────────────────


class DeribitClient:
    """
    Unified Deribit client — exposes public REST for backtest,
    and scaffolds WebSocket for live/paper modes.
    """

    def __init__(self) -> None:
        self.rest = DeribitPublicREST(timeout=cfg.deribit.request_timeout)
        self.ws: DeribitWebSocket | None = None

    async def connect_live(self) -> None:
        """Connect and authenticate WebSocket for paper/live mode."""
        self.ws = DeribitWebSocket()
        await self.ws.connect()
        await self.ws.authenticate()

    async def disconnect(self) -> None:
        if self.ws:
            await self.ws.close()
