"""notifier.py — Telegram notification helper for BTC Wheel Bot."""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "data" / "notifier_config.json"


def _load() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except Exception:
        return {}


def _send(text: str) -> None:
    cfg = _load()
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass


def notify_bot_started(mode: str) -> None:
    _send(f"🟢 <b>Bot started</b> — mode: <code>{mode}</code>")


def notify_bot_stopped() -> None:
    _send("🔴 <b>Bot stopped</b>")


def notify_trade_opened(instrument: str, strike: float, premium_btc: float, dte: int) -> None:
    _send(
        f"📥 <b>Opened</b> {instrument}\n"
        f"Strike: ${strike:,.0f}  •  Premium: {premium_btc:.5f} BTC  •  DTE: {dte}"
    )


def notify_trade_closed(instrument: str, pnl_usd: float, reason: str) -> None:
    sign = "+" if pnl_usd >= 0 else ""
    emoji = "✅" if pnl_usd >= 0 else "❌"
    _send(
        f"{emoji} <b>Closed</b> {instrument}\n"
        f"P&L: {sign}${pnl_usd:,.2f}  •  Reason: {reason}"
    )


def notify_error(message: str) -> None:
    _send(f"⚠️ <b>Bot error</b>\n{message[:300]}")
