"""notifier.py — Telegram notification helper for BTC Wheel Bot."""

from __future__ import annotations

import json
import os
from pathlib import Path

# Master notifier config (always present)
_MASTER_CONFIG_PATH = Path(__file__).parent / "data" / "notifier_config.json"


def _load() -> dict:
    """
    Load notifier config.
    Farm bots set WHEEL_BOT_DATA_DIR to their own data directory.
    Try {DATA_DIR}/notifier_config.json first, then fall back to the
    master data/notifier_config.json so farm bots share the same
    Telegram credentials without needing their own copy.
    """
    data_dir = os.environ.get("WHEEL_BOT_DATA_DIR", "")
    if data_dir:
        bot_cfg = Path(data_dir) / "notifier_config.json"
        if bot_cfg.exists():
            try:
                return json.loads(bot_cfg.read_text())
            except Exception:
                pass
    # Fallback to master config
    try:
        return json.loads(_MASTER_CONFIG_PATH.read_text())
    except Exception:
        return {}


def _bot_name() -> str:
    """
    Derive a human-readable bot name from the runtime environment.
    Reads config_name from bot_state.json if available, else falls back
    to the last segment of WHEEL_BOT_DATA_DIR (e.g. "bot_0").
    """
    data_dir = os.environ.get("WHEEL_BOT_DATA_DIR", "")
    if data_dir:
        state_path = Path(data_dir) / "bot_state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                name = state.get("config_name") or state.get("bot_name", "")
                if name:
                    return str(name)
            except Exception:
                pass
        # Fall back to directory name (e.g. "bot_0")
        return Path(data_dir).parent.name or Path(data_dir).name
    return "bot"


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


def notify_drawdown_warning(drawdown_pct: float, equity_usd: float, bot_name: str = "") -> None:
    name = bot_name or _bot_name()
    _send(
        f"🚨 <b>Drawdown Warning — {name}</b>\n"
        f"Current drawdown: <b>{drawdown_pct:.1%}</b>\n"
        f"Equity: ${equity_usd:,.0f}\n"
        f"Trading halted. Delete KILL_SWITCH to resume."
    )


def notify_high_iv_warning(iv_rank: float, bot_name: str = "") -> None:
    name = bot_name or _bot_name()
    _send(
        f"📈 <b>High IV Alert — {name}</b>\n"
        f"IV rank: <b>{iv_rank:.1%}</b> — extreme volatility.\n"
        f"New positions capped at 1 leg until IV normalises."
    )
