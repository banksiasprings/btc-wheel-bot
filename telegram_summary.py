#!/usr/bin/env python3.11
"""
telegram_summary.py — daily Telegram summary of the BTC bot farm.

Reads grid_farm/status.json and sends Steven a phone message: BTC price, the
leader in each tab, the overall best, the whole-farm total, and any blow-ups.
Reuses the existing Telegram creds in data/notifier_config.json.

    python3.11 telegram_summary.py          # build + send now (also used by the daily launchd job)
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "grid_farm" / "status.json"
CREDS = ROOT / "data" / "notifier_config.json"
PUBLIC_URL = "bot.banksiaspringsfarm.com"
LABELS = {"grid": "Grid", "funding": "Funding", "longvol": "Long-Vol",
          "premium": "Premium", "trend": "Trend", "stack": "Stack", "convex": "Convex"}
TAB_ORDER = ["grid", "funding", "longvol", "premium", "trend", "stack", "convex"]


def send(text: str) -> bool:
    c = json.loads(CREDS.read_text())
    token, chat = c.get("bot_token", ""), c.get("chat_id", "")
    if not token or not chat:
        print("no telegram creds in data/notifier_config.json")
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=10,
    )
    print("telegram:", r.status_code, r.text[:120] if not r.ok else "")
    return r.ok


def build() -> str:
    d = json.loads(STATUS.read_text())
    btc = d.get("btc_price", 0)
    variants = d.get("variants", [])
    by_tab = defaultdict(list)
    for v in variants:
        by_tab[v.get("tab", v.get("type", "grid"))].append(v)

    lines = ["📊 <b>BTC Bot Farm</b> — daily update",
             f"Bitcoin ${btc:,.0f} · {len(variants)} bots (pretend money)\n"]
    for t in TAB_ORDER:
        bots = by_tab.get(t, [])
        if not bots:
            continue
        lead = max(bots, key=lambda v: v["equity"])
        sign = "+" if lead["return_pct"] >= 0 else ""
        lines.append(f"<b>{LABELS.get(t, t)}</b>: {lead['name']} "
                     f"${lead['equity']:,.0f} ({sign}{lead['return_pct']:.1f}%)")

    best = max(variants, key=lambda v: v["return_pct"])
    bsign = "+" if best["return_pct"] >= 0 else ""
    total = sum(v["equity"] for v in variants)
    start = d.get("paper_capital", 10_000.0) * len(variants)
    tsign = "+" if total >= start else ""
    liq = [v["name"] for v in variants if "liquidated" in v.get("state", "").lower()]

    lines.append(f"\n🏆 Best: {best['name']} {bsign}{best['return_pct']:.1f}%")
    lines.append(f"Whole farm: ${total:,.0f} ({tsign}{(total / start - 1) * 100:.1f}%)")
    if liq:
        lines.append(f"💀 Wiped out: {', '.join(liq)}")
    lines.append(f"\nhttps://{PUBLIC_URL}")
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        send(build())
    except Exception as exc:
        send(f"⚠️ BTC bot farm summary failed: {exc}")
