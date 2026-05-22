"""
deribit/client.py — Lightweight Deribit REST client for testnet paper trading.

Uses only stdlib + requests (no aiohttp, loguru, or other heavy deps).
Handles auth via OAuth2 client_credentials, instrument lookup, order book
queries, and order placement/management.

Design goals:
  - Synchronous, blocking calls — suitable for paper-trading hooks
  - Auto-refresh access token before expiry
  - Works against test.deribit.com (testnet) or www.deribit.com (mainnet)
  - Minimal contract size on Deribit BTC options is 0.1 BTC

Typical Deribit instrument names:  BTC-30MAY25-77000-P  (put)
                                    BTC-30MAY25-80000-C  (call)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# Deribit BTC options: minimum contract size is 0.1 BTC
DERIBIT_MIN_CONTRACT_BTC = 0.1

# How many seconds before token expiry to trigger a refresh
_TOKEN_REFRESH_BUFFER_S = 30


class DeribitClient:
    """
    Lightweight Deribit REST client for testnet paper trading.

    Handles auth, instrument lookup, and order placement against the
    Deribit REST API v2.  All calls are synchronous (blocking).
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str,
        timeout: int = 10,
    ) -> None:
        self.client_id     = client_id
        self.client_secret = client_secret
        self.base_url      = base_url.rstrip("/")
        self.timeout       = timeout

        self._access_token: str   = ""
        self._token_expires_at: float = 0.0
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"

    # ── Authentication ─────────────────────────────────────────────────────────

    def authenticate(self) -> str:
        """
        POST /public/auth with client_credentials grant.
        Returns the access_token and stores it for subsequent calls.
        Token expires in 900 s; we refresh 30 s early.
        """
        url = f"{self.base_url}/public/auth"
        params = {
            "grant_type":    "client_credentials",
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
        }
        resp = self._session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise RuntimeError(f"Deribit auth failed: {data['error']}")

        result = data["result"]
        self._access_token    = result["access_token"]
        expires_in            = result.get("expires_in", 900)
        self._token_expires_at = time.time() + expires_in - _TOKEN_REFRESH_BUFFER_S
        logger.debug(
            "Deribit auth OK (token valid ~%ds)", expires_in - _TOKEN_REFRESH_BUFFER_S
        )
        return self._access_token

    def _ensure_auth(self) -> None:
        """Re-authenticate if the token is missing or has (nearly) expired."""
        if not self._access_token or time.time() >= self._token_expires_at:
            self.authenticate()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _public_get(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """GET a public endpoint (no auth required)."""
        url = f"{self.base_url}/public/{method}"
        resp = self._session.get(url, params=params or {}, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if "result" not in data:
            raise ValueError(f"Unexpected response from {method}: {data}")
        return data["result"]

    def _private_get(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """GET a private endpoint (Bearer token required)."""
        self._ensure_auth()
        url = f"{self.base_url}/private/{method}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = self._session.get(
            url, params=params or {}, headers=headers, timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            err = data["error"]
            code = err.get("code", "")
            msg  = err.get("message", str(err))
            raise RuntimeError(f"Deribit API error [{method}] code={code}: {msg}")
        return data["result"]

    # ── Public endpoints ───────────────────────────────────────────────────────

    def get_instruments(
        self,
        currency: str = "BTC",
        kind: str = "option",
        expired: bool = False,
    ) -> list[dict]:
        """
        GET /public/get_instruments — return list of all tradeable option instruments.

        Each dict has keys: instrument_name, strike, expiration_timestamp,
        option_type ('put'|'call'), etc.
        """
        return self._public_get("get_instruments", {
            "currency": currency,
            "kind":     kind,
            "expired":  str(expired).lower(),
        })

    def get_ticker(self, instrument_name: str) -> dict:
        """
        GET /public/ticker — return ticker including Greeks for one instrument.

        Relevant keys in result: mark_price, best_bid_price, best_ask_price,
        mark_iv, greeks.delta, underlying_price.
        """
        return self._public_get("ticker", {"instrument_name": instrument_name})

    def get_order_book(self, instrument_name: str, depth: int = 1) -> dict:
        """
        GET /public/get_order_book — return bid/ask and mark price for an instrument.
        """
        return self._public_get("get_order_book", {
            "instrument_name": instrument_name,
            "depth":           depth,
        })

    # ── Instrument selection ───────────────────────────────────────────────────

    def find_option(
        self,
        option_type: str,
        delta_target: float,
        expiry_days_target: int,
    ) -> Optional[str]:
        """
        Find the closest-matching BTC option instrument.

        Searches active instruments for the best combination of:
          - option_type matching ('put' or 'call')
          - DTE closest to expiry_days_target
          - |delta| closest to delta_target

        Fetches tickers for up to 20 candidates (sorted by DTE proximity) to
        check delta values, then returns the instrument_name with the lowest
        combined score, or None if nothing suitable is found.

        Returns:
            instrument_name str  e.g. 'BTC-30MAY25-77000-P'
            None                 if no matching instrument found
        """
        instruments = self.get_instruments()
        now_ms = int(time.time() * 1000)

        # Build (dte, instrument_dict) list filtered to the requested option_type
        candidates: list[tuple[int, dict]] = []
        for inst in instruments:
            if inst.get("option_type") != option_type:
                continue
            expiry_ts = inst.get("expiration_timestamp", 0)
            dte = max(0, (expiry_ts - now_ms) // 86_400_000)
            if dte < 1:
                continue  # skip expired / same-day
            candidates.append((dte, inst))

        if not candidates:
            logger.warning(
                "find_option: no %s instruments found on %s",
                option_type,
                self.base_url,
            )
            return None

        # Sort by DTE proximity to target
        candidates.sort(key=lambda x: abs(x[0] - expiry_days_target))

        # Score = delta_distance + 0.1 * dte_fraction_distance
        # Cap at 20 ticker calls to keep latency reasonable
        best_name:  Optional[str]  = None
        best_score: float          = float("inf")

        for dte, inst in candidates[:20]:
            inst_name = inst.get("instrument_name", "")
            if not inst_name:
                continue
            try:
                ticker  = self.get_ticker(inst_name)
                greeks  = ticker.get("greeks") or {}
                raw_delta = greeks.get("delta")
                if raw_delta is None:
                    continue
                delta_abs  = abs(float(raw_delta))
                if delta_abs <= 0:
                    continue

                delta_dist = abs(delta_abs - delta_target)
                dte_dist   = (
                    abs(dte - expiry_days_target) / max(expiry_days_target, 1)
                )
                score = delta_dist + 0.1 * dte_dist

                if score < best_score:
                    best_score = score
                    best_name  = inst_name

            except Exception as exc:
                logger.debug("find_option: ticker fetch failed for %s: %s", inst_name, exc)
                continue

        if best_name:
            logger.info(
                "find_option: selected %s (score=%.4f) for %s delta≈%.2f DTE≈%d",
                best_name, best_score, option_type, delta_target, expiry_days_target,
            )
        else:
            logger.warning(
                "find_option: could not find any %s with delta near %.2f",
                option_type, delta_target,
            )

        return best_name

    # ── Private endpoints ──────────────────────────────────────────────────────

    def place_order(
        self,
        instrument_name: str,
        amount: float,
        direction: str,
        order_type: str = "market",
    ) -> dict:
        """
        Place a sell or buy order on Deribit.

        Args:
            instrument_name: e.g. 'BTC-30MAY25-77000-P'
            amount:          number of contracts in BTC (min 0.1 BTC for options)
            direction:       'sell' (open short) or 'buy' (close short / open long)
            order_type:      'market' or 'limit'

        Returns:
            dict with keys: order (containing order_id, order_state,
                            average_price, amount, instrument_name)

        Notes:
            - Options are priced in BTC; convert to USD using underlying_price.
            - Deribit's minimum contract size for BTC options is 0.1 BTC.
            - Market orders on Deribit can be placed as 'market' type but the
              exchange may fill them at mark price for options with thin books.
        """
        direction = direction.lower()
        if direction not in ("buy", "sell"):
            raise ValueError(f"direction must be 'buy' or 'sell', got {direction!r}")

        # Ensure minimum contract size
        amount = max(round(amount, 1), DERIBIT_MIN_CONTRACT_BTC)

        self._ensure_auth()
        url     = f"{self.base_url}/private/{direction}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        params: dict[str, Any] = {
            "instrument_name": instrument_name,
            "amount":          amount,
            "type":            order_type,
        }

        logger.info(
            "place_order: %s %s x%.1f (%s)",
            direction.upper(), instrument_name, amount, order_type,
        )
        resp = self._session.get(
            url, params=params, headers=headers, timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            err  = data["error"]
            code = err.get("code", "")
            msg  = err.get("message", str(err))
            raise RuntimeError(
                f"Order failed [{direction} {instrument_name}] code={code}: {msg}"
            )
        return data["result"]

    def get_open_positions(
        self,
        currency: str = "BTC",
        kind: str = "option",
    ) -> list[dict]:
        """
        GET /private/get_positions — return all open option positions.

        Each dict includes: instrument_name, size, direction, average_price,
        mark_price, floating_profit_loss, delta, expiration_timestamp.
        """
        return self._private_get("get_positions", {
            "currency": currency,
            "kind":     kind,
        })

    def cancel_order(self, order_id: str) -> dict:
        """
        GET /private/cancel — cancel a specific open order by ID.
        Returns the cancelled order dict.
        """
        return self._private_get("cancel", {"order_id": order_id})
