"""
deribit/paper_executor.py — Translates bot farm trade signals into Deribit
testnet API calls and logs every fill to farm/testnet_trades.jsonl.

Signal format (from RL agent or any strategy):
    {"action": "SELL_PUT",  "delta": 0.20, "dte": 7, "contracts": 1}
    {"action": "SELL_CALL", "delta": 0.25, "dte": 7, "contracts": 1}
    {"action": "CLOSE"}   — buy-back shortest-dated open position
    {"action": "HOLD"}    — no-op

Fail-safe design:
    - Every public method catches all exceptions.
    - A Deribit API failure → log + return False; the bot loop continues.
    - Never raises, never crashes the farm.

Log format (one JSON object per line in testnet_trades.jsonl):
    {
      "timestamp":       "2025-05-30T08:01:23Z",
      "action":          "SELL_PUT",
      "instrument":      "BTC-30MAY25-77000-P",
      "direction":       "sell",
      "contracts":       0.1,
      "fill_price_btc":  0.0048,
      "bid":             0.0045,
      "ask":             0.0052,
      "order_id":        "BTC-12345678",
      "status":          "filled"
    }
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from deribit.client import DERIBIT_MIN_CONTRACT_BTC, DeribitClient

logger = logging.getLogger(__name__)


class PaperExecutor:
    """
    Translates bot farm signals into real Deribit testnet orders.

    Wraps DeribitClient; every call is wrapped in a broad try/except so
    a Deribit outage or credential problem never crashes the trading loop.
    """

    def __init__(self, client: DeribitClient, trades_log_path: Path) -> None:
        self._client   = client
        self._log_path = Path(trades_log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "[PaperExecutor] initialised — log: %s", self._log_path
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def execute(self, signal: dict[str, Any]) -> bool:
        """
        Execute a trade signal.  Returns True if an order was placed on
        Deribit, False for HOLD / errors / no-ops.

        This method NEVER raises — all exceptions are caught and logged.
        """
        try:
            action = signal.get("action", "HOLD").upper()

            if action == "HOLD":
                return False

            if action == "CLOSE":
                return self._execute_close()

            if action in ("SELL_PUT", "SELL_CALL"):
                return self._execute_sell(signal, action)

            logger.warning("[PaperExecutor] Unrecognised action %r — ignoring", action)
            return False

        except Exception as exc:
            logger.error(
                "[PaperExecutor] Unexpected error in execute() — non-fatal: %s", exc
            )
            return False

    # ── SELL logic ─────────────────────────────────────────────────────────────

    def _execute_sell(self, signal: dict, action: str) -> bool:
        """Handle SELL_PUT or SELL_CALL signal."""
        try:
            option_type = "put" if action == "SELL_PUT" else "call"
            delta       = float(signal.get("delta", 0.20))
            dte         = int(signal.get("dte", 7))
            # Translate 'contracts' from bot-farm units (whole contracts) to
            # Deribit BTC units.  Minimum is 0.1 BTC per Deribit rules.
            raw_contracts = float(signal.get("contracts", 1))
            amount = max(
                DERIBIT_MIN_CONTRACT_BTC,
                round(raw_contracts * DERIBIT_MIN_CONTRACT_BTC, 1),
            )

            logger.info(
                "[PaperExecutor] %s — searching %s delta≈%.2f DTE≈%d amount=%.1f BTC",
                action, option_type, delta, dte, amount,
            )

            # ── Find instrument ────────────────────────────────────────────────
            instrument = self._client.find_option(option_type, delta, dte)
            if instrument is None:
                logger.warning(
                    "[PaperExecutor] %s: no matching instrument found "
                    "(%s delta≈%.2f DTE≈%d) — skipping",
                    action, option_type, delta, dte,
                )
                self._log_trade({
                    "timestamp": _now_iso(),
                    "action":    action,
                    "direction": "sell",
                    "error":     "no_instrument_found",
                    "status":    "skipped",
                })
                return False

            # ── Fetch order book for context ───────────────────────────────────
            bid = ask = mark = 0.0
            try:
                book = self._client.get_order_book(instrument)
                bid  = float(book.get("best_bid_price", 0) or 0)
                ask  = float(book.get("best_ask_price", 0) or 0)
                mark = float(book.get("mark_price", 0) or 0)
                logger.info(
                    "[PaperExecutor] %s order book: bid=%.6f ask=%.6f mark=%.6f BTC",
                    instrument, bid, ask, mark,
                )
            except Exception as exc:
                logger.warning(
                    "[PaperExecutor] Could not fetch order book for %s: %s",
                    instrument, exc,
                )

            # ── Place market sell order ────────────────────────────────────────
            result = self._client.place_order(
                instrument_name=instrument,
                amount=amount,
                direction="sell",
                order_type="market",
            )

            order      = result.get("order", result)
            fill_price = float(order.get("average_price", 0) or mark or 0)
            order_id   = str(order.get("order_id", ""))
            status     = str(order.get("order_state", "filled"))

            self._log_trade({
                "timestamp":      _now_iso(),
                "action":         action,
                "instrument":     instrument,
                "direction":      "sell",
                "contracts":      amount,
                "fill_price_btc": fill_price,
                "bid":            bid,
                "ask":            ask,
                "order_id":       order_id,
                "status":         status,
            })

            logger.info(
                "[PaperExecutor] ✅ %s filled: %s x%.1f @ %.6f BTC "
                "(order_id=%s status=%s)",
                action, instrument, amount, fill_price, order_id, status,
            )
            return True

        except Exception as exc:
            logger.error(
                "[PaperExecutor] %s failed (non-fatal): %s", action, exc
            )
            self._log_trade({
                "timestamp": _now_iso(),
                "action":    action,
                "direction": "sell",
                "error":     str(exc),
                "status":    "failed",
            })
            return False

    # ── CLOSE logic ────────────────────────────────────────────────────────────

    def _execute_close(self) -> bool:
        """
        Buy back the shortest-dated open option position on Deribit.

        If no positions are open, logs and returns False (not an error).
        """
        try:
            positions = self._client.get_open_positions()
            if not positions:
                logger.info(
                    "[PaperExecutor] CLOSE signal received but no open positions"
                )
                return False

            # Pick shortest-dated position (closest to expiry = most urgent)
            target: Optional[dict] = None
            min_expiry = float("inf")
            for pos in positions:
                expiry = pos.get("expiration_timestamp", float("inf"))
                if expiry < min_expiry:
                    min_expiry = expiry
                    target = pos

            if target is None:
                return False

            instrument = target["instrument_name"]
            # size on Deribit is negative for shorts; take absolute value
            size = abs(float(target.get("size", DERIBIT_MIN_CONTRACT_BTC)))
            size = max(DERIBIT_MIN_CONTRACT_BTC, size)

            logger.info(
                "[PaperExecutor] CLOSE — buying back %s x%.1f BTC",
                instrument, size,
            )

            result = self._client.place_order(
                instrument_name=instrument,
                amount=size,
                direction="buy",
                order_type="market",
            )

            order      = result.get("order", result)
            fill_price = float(order.get("average_price", 0) or 0)
            order_id   = str(order.get("order_id", ""))
            status     = str(order.get("order_state", "filled"))

            self._log_trade({
                "timestamp":      _now_iso(),
                "action":         "CLOSE",
                "instrument":     instrument,
                "direction":      "buy",
                "contracts":      size,
                "fill_price_btc": fill_price,
                "order_id":       order_id,
                "status":         status,
            })

            logger.info(
                "[PaperExecutor] ✅ CLOSE filled: %s x%.1f @ %.6f BTC "
                "(order_id=%s status=%s)",
                instrument, size, fill_price, order_id, status,
            )
            return True

        except Exception as exc:
            logger.error(
                "[PaperExecutor] CLOSE failed (non-fatal): %s", exc
            )
            self._log_trade({
                "timestamp": _now_iso(),
                "action":    "CLOSE",
                "direction": "buy",
                "error":     str(exc),
                "status":    "failed",
            })
            return False

    # ── Log helper ─────────────────────────────────────────────────────────────

    def _log_trade(self, record: dict) -> None:
        """Append a trade record (one JSON object) to the JSONL log file."""
        try:
            with open(self._log_path, "a") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception as exc:
            logger.error(
                "[PaperExecutor] Failed to write trade log to %s: %s",
                self._log_path, exc,
            )


# ── Utility ────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Factory ────────────────────────────────────────────────────────────────────

def load_executor_from_config(
    creds_path: Path,
    trades_log_path: Path,
) -> Optional[PaperExecutor]:
    """
    Build a PaperExecutor from a deribit_testnet.json credentials file.

    Returns None (and logs the reason) if:
      - The file doesn't exist
      - Credentials are still placeholder values
      - Any other error occurs during initialisation

    This factory is called by bot.py and is itself fail-safe.
    """
    if not creds_path.exists():
        logger.info(
            "[testnet] %s not found — PaperExecutor disabled", creds_path
        )
        return None

    try:
        with open(creds_path) as fh:
            creds = json.load(fh)

        client_id     = creds.get("client_id", "")
        client_secret = creds.get("client_secret", "")

        # Detect placeholder values — don't accidentally call real API
        if (
            not client_id
            or "YOUR_" in client_id
            or not client_secret
            or "YOUR_" in client_secret
        ):
            logger.info(
                "[testnet] Placeholder credentials in %s — "
                "PaperExecutor disabled (fill in real testnet keys to enable)",
                creds_path,
            )
            return None

        base_url = creds.get("base_url", "https://test.deribit.com/api/v2")
        client   = DeribitClient(client_id, client_secret, base_url)

        executor = PaperExecutor(client, trades_log_path)
        logger.info(
            "[testnet] PaperExecutor loaded — target: %s | log: %s",
            base_url, trades_log_path,
        )
        return executor

    except Exception as exc:
        logger.warning(
            "[testnet] Failed to initialise PaperExecutor from %s: %s "
            "(non-fatal — continuing in local paper-only mode)",
            creds_path, exc,
        )
        return None
