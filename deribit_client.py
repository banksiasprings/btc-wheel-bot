"""
deribit_client.py — Deribit REST + WebSocket client wrapper.

Public endpoints (no auth) are used for backtest mode.
DeribitPrivateREST uses OAuth2 client_credentials for authenticated
account queries (positions, equity, settlements).
DeribitWebSocket handles real-time data, order execution, and settlement
event subscriptions for live/paper mode.

All live order methods are marked # LIVE_ONLY.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import aiohttp
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
import logging as _logging

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
    greeks: dict | None = None   # raw greeks dict for easy access


@dataclass
class Instrument:
    instrument_name: str
    strike: float
    expiry_ts: int        # Unix timestamp ms
    option_type: str      # "put" | "call"
    dte: int              # days to expiry


@dataclass
class AccountSummary:
    equity: float          # total account equity in BTC
    balance: float         # cash balance in BTC
    available_funds: float # funds available for new positions
    margin_balance: float  # margin balance
    currency: str = "BTC"


@dataclass
class ExchangePosition:
    """An open position as reported by Deribit (live mode)."""
    instrument_name: str
    size: float            # positive = long, negative = short
    direction: str         # "buy" | "sell"
    average_price: float   # average entry price (BTC)
    mark_price: float
    floating_profit_loss: float
    delta: float
    option_type: str       # "put" | "call"
    expiry_ts: int


# ── REST client (public endpoints — no auth required) ─────────────────────────


class DeribitPublicREST:
    """Thin wrapper around Deribit public REST API (no authentication)."""

    BASE_URL = "https://www.deribit.com/api/v2/public"

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(logger, _logging.WARNING),
        reraise=True,
    )
    def _get(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to a public endpoint (auto-retries on network errors)."""
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
            greeks_raw = raw.get("greeks", {})
            return Ticker(
                instrument_name=instrument_name,
                mark_price=float(raw.get("mark_price", 0)),
                bid=float(raw.get("best_bid_price", 0)),
                ask=float(raw.get("best_ask_price", 0)),
                mark_iv=float(raw.get("mark_iv", 0)),
                delta=float(greeks_raw.get("delta", 0)),
                gamma=float(greeks_raw.get("gamma", 0)),
                theta=float(greeks_raw.get("theta", 0)),
                vega=float(greeks_raw.get("vega", 0)),
                underlying_price=float(raw.get("underlying_price", 0)),
                timestamp=datetime.fromtimestamp(
                    raw["timestamp"] / 1000, tz=timezone.utc
                ),
                greeks=greeks_raw,
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
        ticks   = raw.get("ticks", [])
        opens   = raw.get("open", [])
        highs   = raw.get("high", [])
        lows    = raw.get("low", [])
        closes  = raw.get("close", [])
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


# ── Private REST client (authenticated — Phase 2) ─────────────────────────────


class DeribitPrivateREST:
    """
    Authenticated REST client for Deribit private endpoints.

    Uses OAuth2 client_credentials flow to obtain an access token,
    then includes it as a Bearer token in all private requests.

    Requires DERIBIT_API_KEY and DERIBIT_API_SECRET environment variables.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        timeout: int = 10,
    ) -> None:
        self._api_key    = api_key
        self._api_secret = api_secret
        self._timeout    = timeout
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

        base = "https://test.deribit.com" if testnet else "https://www.deribit.com"
        self._pub_url  = f"{base}/api/v2/public"
        self._priv_url = f"{base}/api/v2/private"
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"

    def _authenticate(self) -> None:
        """Obtain an OAuth2 access token via client_credentials grant."""
        if not self._api_key or not self._api_secret:
            raise ValueError(
                "DERIBIT_API_KEY and DERIBIT_API_SECRET must be set in environment "
                "to use private REST endpoints."
            )
        url = f"{self._pub_url}/auth"
        params = {
            "grant_type": "client_credentials",
            "client_id": self._api_key,
            "client_secret": self._api_secret,
        }
        resp = self.session.get(url, params=params, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Authentication failed: {data['error']}")
        result = data["result"]
        self._access_token = result["access_token"]
        # Refresh 30s before the token actually expires
        self._token_expires_at = time.time() + result.get("expires_in", 900) - 30
        logger.info("Deribit private REST authenticated (token valid ~15min)")

    def _ensure_auth(self) -> None:
        """Re-authenticate if the token has expired."""
        if time.time() >= self._token_expires_at:
            self._authenticate()

    def _get(self, method: str, params: dict | None = None) -> Any:
        """Authenticated GET to a private endpoint."""
        self._ensure_auth()
        url = f"{self._priv_url}/{method}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            resp = self.session.get(
                url, params=params or {}, headers=headers, timeout=self._timeout
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"Deribit API error [{method}]: {data['error']}")
            return data["result"]
        except requests.RequestException as exc:
            logger.error(f"Private REST error [{method}]: {exc}")
            raise

    # ── Account state ──────────────────────────────────────────────────────────

    def get_account_summary(self, currency: str = "BTC") -> AccountSummary:
        """Fetch live account equity, balance, and available funds."""
        raw = self._get("get_account_summary", {
            "currency": currency,
        })
        return AccountSummary(
            equity=float(raw.get("equity", 0.0)),
            balance=float(raw.get("balance", 0.0)),
            available_funds=float(raw.get("available_funds", 0.0)),
            margin_balance=float(raw.get("margin_balance", 0.0)),
            currency=currency,
        )

    def get_positions(
        self, currency: str = "BTC", kind: str = "option"
    ) -> list[ExchangePosition]:
        """Fetch all currently open option positions."""
        raw_list = self._get("get_positions", {
            "currency": currency,
            "kind": kind,
        })
        positions: list[ExchangePosition] = []
        for raw in raw_list:
            inst = raw.get("instrument_name", "")
            # Parse option type and expiry from instrument name e.g. BTC-25APR25-90000-P
            parts = inst.split("-")
            opt_type = "put" if parts[-1] == "P" else "call"
            try:
                from datetime import datetime as _dt
                expiry_str = parts[1]
                expiry_dt = _dt.strptime(expiry_str, "%d%b%y").replace(
                    hour=8, minute=0, second=0, tzinfo=timezone.utc
                )
                expiry_ts = int(expiry_dt.timestamp() * 1000)
            except Exception:
                expiry_ts = 0

            positions.append(ExchangePosition(
                instrument_name=inst,
                size=float(raw.get("size", 0.0)),
                direction=raw.get("direction", "sell"),
                average_price=float(raw.get("average_price", 0.0)),
                mark_price=float(raw.get("mark_price", 0.0)),
                floating_profit_loss=float(raw.get("floating_profit_loss", 0.0)),
                delta=float(raw.get("delta", 0.0)),
                option_type=opt_type,
                expiry_ts=expiry_ts,
            ))
        logger.debug(f"Fetched {len(positions)} open {kind} positions for {currency}")
        return positions

    def get_open_orders(
        self, currency: str = "BTC", kind: str = "option"
    ) -> list[dict]:
        """Fetch all open (unfilled) orders."""
        return self._get("get_open_orders_by_currency", {
            "currency": currency,
            "kind": kind,
        })

    def get_settlement_history_by_instrument(
        self,
        instrument_name: str,
        settlement_type: str = "settlement",
        count: int = 5,
    ) -> list[dict]:
        """
        Fetch recent settlement records for a specific instrument.

        settlement_type: "settlement" | "delivery" | "bankruptcy"
        Returns list of settlement records (newest first).
        """
        return self._get("get_settlement_history_by_instrument", {
            "instrument_name": instrument_name,
            "type": settlement_type,
            "count": count,
        })

    def cancel_order(self, order_id: str) -> dict:
        """Cancel a specific open order by ID."""
        return self._get("cancel", {"order_id": order_id})

    def cancel_all_by_instrument(self, instrument_name: str) -> int:
        """Cancel all orders for a given instrument. Returns number cancelled."""
        return self._get("cancel_all_by_instrument", {
            "instrument_name": instrument_name,
        })


# ── WebSocket client (live/paper mode) ────────────────────────────────────────


class DeribitWebSocket:
    """
    Async WebSocket client for real-time data, order management, and
    settlement event subscriptions.

    # LIVE_ONLY — this class is NOT used in backtest mode.

    Phase 2 additions:
      - subscribe() — subscribe to private channels (e.g. settlement events)
      - buy_option() — close a short option position (buy to close)
      - Settlement callback routing via _subscriptions dict
    """

    def __init__(self) -> None:
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._msg_id: int = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._authenticated: bool = False
        # channel_name → callback(data: dict)
        self._subscriptions: dict[str, Callable[[dict], None]] = {}
        # Reconnect tracking
        self._connected: bool = False
        self._reconnect_count: int = 0
        self._running: bool = False

    async def connect(self) -> None:
        """Open WebSocket connection to Deribit."""
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(cfg.deribit.ws_url)
        self._connected = True
        self._running = True
        asyncio.create_task(self._recv_loop())
        logger.info(f"WebSocket connected to {cfg.deribit.ws_url}")

    async def disconnect(self) -> None:
        """Gracefully close the WebSocket connection."""
        self._running = False
        self._connected = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("WebSocket disconnected")

    async def authenticate(self) -> None:
        """Authenticate with API key/secret. Required for order placement and private subscriptions."""
        if not cfg.deribit.api_key:
            raise ValueError("DERIBIT_API_KEY not set in environment")
        await self._rpc("public/auth", {
            "grant_type": "client_credentials",
            "client_id": cfg.deribit.api_key,
            "client_secret": cfg.deribit.api_secret,
        })
        self._authenticated = True
        logger.info("WebSocket authenticated")

    async def subscribe(
        self,
        channels: list[str],
        callback: Callable[[dict], None],
    ) -> None:
        """
        Subscribe to one or more private WebSocket channels.

        All channels share the same callback; route internally if needed.

        Example channels:
            "user.changes.any.BTC.raw"   — all account changes (fills, settlements)
            "user.portfolio.btc"          — portfolio/equity updates

        # LIVE_ONLY
        """
        if not self._authenticated:
            raise RuntimeError("Must authenticate before subscribing to private channels")
        for channel in channels:
            self._subscriptions[channel] = callback
        result = await self._rpc("private/subscribe", {"channels": channels})
        logger.info(f"Subscribed to WebSocket channels: {channels}")
        return result

    async def _rpc(self, method: str, params: dict) -> Any:
        """Send a JSON-RPC request and await the response."""
        msg_id = self._msg_id
        self._msg_id += 1
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
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
        """
        Receive loop — routes messages to:
          1. Pending RPC futures (request/response pairs)
          2. Subscription callbacks (push notifications)

        Auto-reconnects on disconnect with exponential backoff (5s → 60s cap).
        Re-authenticates and re-subscribes after each reconnect.
        """
        while self._running:
            try:
                async for msg in self._ws:
                    if msg.type == aiohttp.WSMsgType.ERROR:
                        logger.warning(f"WebSocket error message: {msg}")
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue

                    # Route RPC responses
                    if "id" in data and data["id"] in self._pending:
                        fut = self._pending.pop(data["id"])
                        if "error" in data:
                            err = data["error"]
                            # Surface the full error payload (incl. nested 'data')
                            # rather than just the generic 'message'. Deribit's
                            # "Invalid params" / "insufficient_funds" / etc. comes
                            # back with 'data.reason' that points at the real
                            # cause; without this we can never debug failures.
                            msg = err.get("message", "unknown")
                            extra = err.get("data") or {}
                            if extra:
                                msg = f"{msg} (data={extra})"
                            code = err.get("code")
                            if code is not None:
                                msg = f"{msg} [code={code}]"
                            fut.set_exception(RuntimeError(msg))
                        else:
                            fut.set_result(data.get("result"))

                    # Route subscription push messages
                    elif data.get("method") == "subscription":
                        params = data.get("params", {})
                        channel = params.get("channel", "")
                        channel_data = params.get("data", {})
                        cb = self._subscriptions.get(channel)
                        if cb:
                            try:
                                cb(channel_data)
                            except Exception as exc:
                                logger.error(f"Subscription callback error [{channel}]: {exc}")
                        else:
                            # Wildcard: match channel prefix e.g. "user.changes.any.BTC.raw"
                            for sub_channel, sub_cb in self._subscriptions.items():
                                if channel.startswith(sub_channel.rstrip("*")):
                                    try:
                                        sub_cb(channel_data)
                                    except Exception as exc:
                                        logger.error(f"Subscription callback error [{channel}]: {exc}")
                                    break

            except Exception as exc:
                logger.warning(f"WebSocket recv_loop exception: {exc}")

            if not self._running:
                break

            # Connection dropped — attempt reconnect with exponential backoff
            self._connected = False
            self._authenticated = False
            self._reconnect_count += 1
            backoff = min(5 * (2 ** min(self._reconnect_count - 1, 4)), 60)
            logger.warning(
                f"WebSocket disconnected (reconnect #{self._reconnect_count}). "
                f"Retrying in {backoff}s…"
            )
            # Cancel pending futures so callers don't hang
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("WebSocket reconnecting"))
            self._pending.clear()

            await asyncio.sleep(backoff)
            try:
                if self._session and not self._session.closed:
                    await self._session.close()
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(cfg.deribit.ws_url)
                self._connected = True
                logger.info(
                    f"WebSocket reconnected (attempt #{self._reconnect_count}) "
                    f"to {cfg.deribit.ws_url}"
                )
                # Re-authenticate if credentials are available
                if cfg.deribit.api_key and cfg.deribit.api_secret:
                    try:
                        await self.authenticate()
                    except Exception as auth_exc:
                        logger.warning(f"Re-auth after reconnect failed: {auth_exc}")
            except Exception as reconnect_exc:
                logger.error(f"Reconnect attempt #{self._reconnect_count} failed: {reconnect_exc}")

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
        Place a sell order for an option (open short position).
        # LIVE_ONLY
        """
        if not self._authenticated:
            raise RuntimeError("Must authenticate before placing orders")
        params = self._build_order_params(
            instrument_name, amount, order_type, price, label
        )
        result = await self._rpc("private/sell", params)
        logger.info(f"Sell order placed: {instrument_name} × {amount} @ {price}")
        return result

    async def buy_option(
        self,
        instrument_name: str,
        amount: float,
        order_type: str = "limit",
        price: float | None = None,
        label: str = "",
    ) -> dict:
        """
        Place a buy order to close a short option position (buy to close).
        # LIVE_ONLY
        """
        if not self._authenticated:
            raise RuntimeError("Must authenticate before placing orders")
        params = self._build_order_params(
            instrument_name, amount, order_type, price, label
        )
        result = await self._rpc("private/buy", params)
        logger.info(f"Buy-to-close order placed: {instrument_name} × {amount} @ {price}")
        return result

    @staticmethod
    def _build_order_params(
        instrument_name: str,
        amount: float,
        order_type: str,
        price: float | None,
        label: str,
    ) -> dict[str, Any]:
        """
        Construct the params dict for private/buy and private/sell.

        Two real-world gotchas live here:
          1. Empty `label`: Deribit accepts `label` as optional, but historically
             rejected an empty string with a generic "Invalid params". Only send
             the field when non-empty.
          2. Limit price with no price: Deribit requires `price` for limit
             orders; for market orders it must be omitted entirely.
        """
        params: dict[str, Any] = {
            "instrument_name": instrument_name,
            "amount": amount,
            "type": order_type,
        }
        if label:
            params["label"] = label
        if order_type == "limit" and price is not None:
            params["price"] = price
        return params

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
    Unified Deribit client — exposes public REST for backtest/paper,
    private REST for live account sync, and WebSocket for real-time
    data and order execution.
    """

    def __init__(self) -> None:
        self.rest = DeribitPublicREST(timeout=cfg.deribit.request_timeout)
        self.ws: DeribitWebSocket | None = None
        # Private REST — instantiated only when API keys are available
        self._private: DeribitPrivateREST | None = None
        if cfg.deribit.api_key and cfg.deribit.api_secret:
            self._private = DeribitPrivateREST(
                api_key=cfg.deribit.api_key,
                api_secret=cfg.deribit.api_secret,
                testnet=cfg.deribit.testnet,
                timeout=cfg.deribit.request_timeout,
            )
            logger.info("DeribitPrivateREST initialised (API keys found)")
        else:
            logger.info(
                "No DERIBIT_API_KEY/SECRET — private REST unavailable; "
                "running in public-only mode (paper trading with simulated state)"
            )

    @property
    def private(self) -> DeribitPrivateREST | None:
        """Return the private REST client, or None if API keys are not configured."""
        return self._private

    def has_private_access(self) -> bool:
        """True if API keys are set and private REST is available."""
        return self._private is not None

    async def connect_live(self) -> None:
        """Connect and authenticate WebSocket for paper/live mode."""
        self.ws = DeribitWebSocket()
        await self.ws.connect()
        await self.ws.authenticate()

    async def disconnect(self) -> None:
        if self.ws:
            await self.ws.close()
