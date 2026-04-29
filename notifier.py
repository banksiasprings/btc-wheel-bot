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
    name = _bot_name()
    _send(f"🟢 <b>{name}</b> started — mode: <code>{mode}</code>")


def notify_bot_stopped() -> None:
    name = _bot_name()
    _send(f"🔴 <b>{name}</b> stopped")


def notify_trade_opened(
    instrument: str,
    strike: float,
    premium_btc: float,
    dte: int,
    spot: float = 0.0,
    breakeven: float = 0.0,
) -> None:
    name = _bot_name()
    buf_pct = ((spot - strike) / spot * 100) if spot > 0 and strike > 0 else 0.0
    be_line = f"\nBreakeven: ${breakeven:,.0f}  •  Buffer to strike: {buf_pct:.1f}%" if breakeven > 0 and spot > 0 else ""
    _send(
        f"📥 <b>{name}</b> opened\n"
        f"<code>{instrument}</code>\n"
        f"Strike: ${strike:,.0f}  •  Premium: {premium_btc:.5f} BTC  •  DTE: {dte}"
        f"{be_line}\n"
        f"\n<i>✅ Win: BTC stays above ${strike:,.0f} at expiry → keep full premium\n"
        f"❌ Risk: BTC falls below ${strike:,.0f} → bot buys BTC at strike (assignment)</i>"
    )


def notify_trade_closed(instrument: str, pnl_usd: float, reason: str) -> None:
    name = _bot_name()
    sign = "+" if pnl_usd >= 0 else ""
    emoji = "✅" if pnl_usd >= 0 else "❌"
    reason_map = {
        "expiry_settlement": "option expired naturally",
        "delta_breach":      "delta limit breached — rolled/closed early to cap loss",
        "loss_breach":       "loss limit reached — closed early to protect equity",
        "manual":            "manually closed via app",
        "kill_switch":       "kill switch triggered",
        "roll":              "rolled to new position",
    }
    reason_plain = reason_map.get(reason, reason)
    outcome = "Full premium kept as profit." if pnl_usd >= 0 else "Assignment or early close — premium partially offset the loss."
    _send(
        f"{emoji} <b>{name}</b> closed\n"
        f"<code>{instrument}</code>\n"
        f"P&L: {sign}${pnl_usd:,.2f}  •  {reason_plain}\n"
        f"<i>{outcome}</i>"
    )


def notify_error(message: str) -> None:
    name = _bot_name()
    _send(f"⚠️ <b>{name}</b> error\n{message[:300]}")


def notify_drawdown_warning(drawdown_pct: float, equity_usd: float, bot_name: str = "") -> None:
    name = bot_name or _bot_name()
    _send(
        f"🚨 <b>Drawdown Warning — {name}</b>\n"
        f"Account is down <b>{drawdown_pct:.1%}</b> from its peak.\n"
        f"Equity: ${equity_usd:,.0f}\n\n"
        f"<i>The bot has halted new trades automatically to protect remaining capital. "
        f"Any open positions will still run to their natural expiry — no immediate action needed. "
        f"Once you've reviewed the situation, delete the KILL_SWITCH file (or use the app) to resume.</i>"
    )


def notify_high_iv_warning(iv_rank: float, bot_name: str = "") -> None:
    name = bot_name or _bot_name()
    _send(
        f"📈 <b>High IV Alert — {name}</b>\n"
        f"IV rank: <b>{iv_rank:.1%}</b> — extreme volatility detected.\n\n"
        f"<i>IV rank measures how high options premiums are compared to the past year. "
        f"At this level, premiums are very expensive — which is good for selling options, "
        f"but also signals the market expects large price moves. "
        f"The bot is capping new trades to 1 leg until IV settles back below 85%.</i>"
    )


def notify_position_risk(bot_name: str, risk_level: str, pos: dict) -> None:
    """
    Sent when a bot's position crosses into 'caution' or 'danger'.
    Only called once per risk-level transition to avoid spam.
    """
    strike    = pos.get("strike", 0)
    spot      = pos.get("current_spot", 0)
    pnl       = pos.get("unrealized_pnl_usd", 0)
    delta     = pos.get("current_delta")
    dte       = pos.get("dte") or pos.get("days_to_expiry", "?")
    opt       = (pos.get("type") or "option").replace("short_", "").upper()
    breakeven = pos.get("breakeven", 0)
    sign      = "+" if pnl >= 0 else ""
    pnl_str   = f"{sign}${pnl:,.0f}"

    buf_pct = ((spot - strike) / spot * 100) if spot > 0 and strike > 0 else 0.0
    buf_str = f"BTC is {buf_pct:.1f}% above the strike." if buf_pct > 0 else f"BTC is {abs(buf_pct):.1f}% BELOW the strike — already ITM."
    be_line = f"\nBreakeven: ${breakeven:,.0f}" if breakeven > 0 else ""

    if risk_level == "danger":
        emoji   = "🚨"
        heading = f"DANGER — {bot_name} position at serious risk"
        context = (
            f"The PUT option strike is ${strike:,.0f}. {buf_str}\n"
            f"If BTC stays below the strike at expiry, the bot will be assigned — forced to buy BTC at ${strike:,.0f}.{be_line}\n\n"
            f"<b>Your options:</b>\n"
            f"• Do nothing — the bot will manage the position automatically and may roll or close it\n"
            f"• Use Emergency Close in the app to close the position now and lock in the current loss\n"
            f"• Wait it out — BTC could recover before expiry (DTE: {dte} day{'s' if dte != 1 else ''})"
        )
    else:
        emoji   = "⚠️"
        heading = f"Caution — {bot_name} position approaching strike"
        context = (
            f"The PUT option strike is ${strike:,.0f}. {buf_str}\n"
            f"The option is still out of the money, but the buffer is shrinking.{be_line}\n\n"
            f"<b>What this means:</b> BTC needs to keep falling before any real loss occurs. "
            f"The bot is monitoring closely and will act if delta or loss thresholds are breached.\n\n"
            f"<b>Your options:</b>\n"
            f"• Do nothing — bot is monitoring and will act if needed\n"
            f"• Watch the price — if BTC drops further, escalate to danger level\n"
            f"• Use Emergency Close in the app if you want to exit now"
        )

    delta_str = f"  •  Δ {delta:.3f}" if delta is not None else ""
    _send(
        f"{emoji} <b>{heading}</b>\n"
        f"Short {opt} @ ${strike:,.0f}  •  Spot ${spot:,.0f}{delta_str}\n"
        f"Unrealised P&L: {pnl_str}  •  DTE: {dte}\n\n"
        f"{context}"
    )


def notify_expiry_approaching(
    bot_name: str,
    dte: int,
    instrument: str,
    strike: float,
    spot: float,
    breakeven: float = 0.0,
    unrealized_pnl_usd: float = 0.0,
) -> None:
    """Sent when DTE drops to a key threshold (3 days, 1 day)."""
    buf_pct = ((spot - strike) / spot * 100) if spot > 0 and strike > 0 else 0.0
    otm_or_itm = "above" if buf_pct >= 0 else "below"
    abs_buf = abs(buf_pct)

    sign = "+" if unrealized_pnl_usd >= 0 else ""
    pnl_str = f"{sign}${unrealized_pnl_usd:,.0f}"

    if dte <= 1:
        urgency = "🔴 <b>Expiring today</b>"
        outlook = (
            f"BTC is currently {abs_buf:.1f}% {otm_or_itm} the strike (${strike:,.0f}).\n"
            f"<b>Most likely outcome:</b> "
            + ("Option expires worthless — full premium kept ✅" if buf_pct >= 0
               else f"Option is ITM — assignment likely unless BTC recovers above ${strike:,.0f} ❌")
        )
    else:
        urgency = f"⏰ <b>{dte} days to expiry</b>"
        outlook = (
            f"BTC is {abs_buf:.1f}% {otm_or_itm} the strike (${strike:,.0f}).\n"
            + ("The position is currently profitable — BTC needs to hold above the strike to keep the premium." if buf_pct >= 0
               else f"BTC has fallen below the strike. It needs to recover above ${strike:,.0f} by expiry to avoid assignment.")
        )

    be_line = f"\nBreakeven: ${breakeven:,.0f}" if breakeven > 0 else ""
    _send(
        f"{urgency} — {bot_name}\n"
        f"<code>{instrument}</code>\n"
        f"Unrealised P&L: {pnl_str}{be_line}\n\n"
        f"{outlook}\n\n"
        f"<i>No action needed — the bot will handle expiry automatically. "
        f"Use Emergency Close in the app only if you want to exit early.</i>"
    )


def notify_farm_started(bot_count: int, live_bot_count: int = 0) -> None:
    """Sent by the API when the farm supervisor process is launched."""
    live_note = f"\n⚡ {live_bot_count} bot{'s' if live_bot_count != 1 else ''} ready for live trading" if live_bot_count else ""
    _send(
        f"🟢 <b>Bot Farm started</b>\n"
        f"{bot_count} bot{'s' if bot_count != 1 else ''} initialising{live_note}"
    )


def notify_farm_stopped(bot_count: int, open_positions: int = 0, manual: bool = True) -> None:
    """Sent by the API immediately before the farm supervisor process is killed."""
    trigger = "manually stopped" if manual else "stopped"
    pos_note = f"\n⚠️ {open_positions} open position{'s' if open_positions != 1 else ''} left unmanaged — check Deribit" if open_positions else "\nNo open positions."
    _send(
        f"🔴 <b>Bot Farm {trigger}</b>\n"
        f"{bot_count} bot{'s' if bot_count != 1 else ''} halted{pos_note}"
    )
