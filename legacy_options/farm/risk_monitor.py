"""
risk_monitor.py — Real-time position risk monitor for the bot farm.
Runs as a background thread. Pushes ntfy.sh alerts when losses exceed thresholds.

Reads farm/status.json (written every 60 s by BotFarm.write_status).
Field names match what bot_farm.py writes into the "open_position" dict:
  premium_collected  — initial credit received (USD)
  pnl_usd            — current unrealized P&L (negative = losing)
"""
import threading
import time
import requests
import json
from pathlib import Path
from datetime import datetime

NTFY_TOPIC = "bsf-voice-tasks"
STATUS_JSON = Path(__file__).parent / "status.json"

WARN_THRESHOLD   = 0.30   # 30% of premium lost → warning
DANGER_THRESHOLD = 0.80   # 80% of premium lost → urgent


class RiskMonitor(threading.Thread):
    def __init__(self, interval_seconds=60):
        super().__init__(daemon=True)
        self.interval = interval_seconds
        self._alerted = {}  # bot_name → last alert level sent (1=warn, 2=danger)

    def run(self):
        while True:
            try:
                self._check()
            except Exception as e:
                print(f"[risk_monitor] Error: {e}")
            time.sleep(self.interval)

    def _check(self):
        if not STATUS_JSON.exists():
            return
        status = json.loads(STATUS_JSON.read_text())
        for bot in status.get("bots", []):
            name = bot.get("name", "unknown")
            # status.json stores open position under "open_position" key
            position = bot.get("open_position")
            if not position:
                self._alerted.pop(name, None)  # reset if no position
                continue

            # Field names as written by BotFarm.to_status_dict → open_position_summary
            premium = position.get("premium_collected", 0) or 0
            pnl     = position.get("pnl_usd", 0) or 0
            if premium <= 0:
                continue

            # loss_pct: fraction of premium that has been eroded
            # pnl_usd is negative when losing, so -pnl / premium gives the loss fraction
            loss_pct = max(0.0, -pnl / premium)

            prev_level = self._alerted.get(name, 0)

            if loss_pct >= DANGER_THRESHOLD and prev_level < 2:
                self._send_alert(name, position, loss_pct, priority="urgent")
                self._alerted[name] = 2
            elif loss_pct >= WARN_THRESHOLD and prev_level < 1:
                self._send_alert(name, position, loss_pct, priority="default")
                self._alerted[name] = 1

    def _send_alert(self, bot_name, position, loss_pct, priority="default"):
        instrument = position.get("instrument", "unknown")
        # Fall back to strike+type if instrument key absent (status.json uses type+strike)
        if instrument == "unknown":
            opt_type = position.get("type", "")
            strike   = position.get("strike", "")
            expiry   = position.get("expiry", "")
            instrument = f"{opt_type} {strike} exp {expiry}".strip() or "unknown"

        msg = (
            f"⚠️ {bot_name}: position at {loss_pct:.0%} loss\n"
            f"Instrument: {instrument}\n"
            f"Consider closing position to cut losses."
        )
        try:
            requests.post(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=msg.encode(),
                headers={
                    "Title":    f"BTC Bot Risk Alert — {bot_name}",
                    "Priority": priority,
                    "Tags":     "warning" if priority == "default" else "rotating_light",
                },
                timeout=5,
            )
        except Exception as e:
            print(f"[risk_monitor] ntfy send failed: {e}")
