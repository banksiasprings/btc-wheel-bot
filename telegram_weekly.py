#!/usr/bin/env python3.11
"""
telegram_weekly.py — weekly "scorecard" digest of the BTC bot farm.

Unlike the daily summary (a quick leaderboard), this is the survival-first review
we use to judge the bots over the multi-week paper bake-off:

  • Safest performers — UNLEVERAGED bots ranked by profit MINUS worst dip (the
    real-money lens: a smooth +5% beats a wild +12% that plunged 25%).
  • Biggest gainers   — everything ranked by raw weekly profit (incl. the leveraged
    "for kicks" bots).
  • Watch list        — anything wiped out (💀) or with a scary dip.
  • Data maturity      — how many days/weeks of history so far, so we don't over-read
    early noise (proper judgement comes at ~8–12 weeks).

Reads grid_farm/status.json (names/leverage) + each bot's grid_farm/<slug>/equity.csv
(for true 7-day change and the week's worst dip). Reuses the Telegram creds + send().

    python3.11 telegram_weekly.py        # build + send now (also the weekly launchd job)
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram_summary import PUBLIC_URL, send   # reuse creds + sender

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "grid_farm" / "status.json"
WINDOW_DAYS = 7
DIP_ALARM = 15.0          # weekly dip beyond this lands a bot on the watch list


def _equity_rows(slug: str):
    """[(dt, equity, btc_price, liquidated), ...] from grid_farm/<slug>/equity.csv."""
    path = ROOT / "grid_farm" / slug / "equity.csv"
    out = []
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    out.append((datetime.fromisoformat(r["timestamp"]), float(r["equity"]),
                                float(r["btc_price"]), int(r.get("liquidated", 0))))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return out


def _weekly(slug: str):
    """This week's return %, worst dip %, latest equity, and ever-liquidated flag."""
    rows = _equity_rows(slug)
    if len(rows) < 2:
        return None
    cutoff = rows[-1][0] - timedelta(days=WINDOW_DAYS)
    wk = [r for r in rows if r[0] >= cutoff] or rows
    e0, e1 = wk[0][1], wk[-1][1]
    ret = (e1 / e0 - 1) * 100 if e0 else 0.0
    peak, dip = float("-inf"), 0.0
    for _, e, _, _ in wk:
        peak = max(peak, e)
        if peak > 0:
            dip = max(dip, (peak - e) / peak * 100)
    return {"ret": ret, "dip": dip, "equity": e1, "liq": any(r[3] for r in rows)}


def _btc_weekly(variants):
    """(current BTC, weekly % change) from any bot's stored btc_price column."""
    for v in variants:
        rows = _equity_rows(v["slug"])
        if len(rows) >= 2:
            cutoff = rows[-1][0] - timedelta(days=WINDOW_DAYS)
            wk = [r for r in rows if r[0] >= cutoff] or rows
            b0, b1 = wk[0][2], wk[-1][2]
            return b1, ((b1 / b0 - 1) * 100 if b0 else 0.0)
    return 0.0, 0.0


def _days_of_data(variants):
    earliest = None
    for v in variants:
        rows = _equity_rows(v["slug"])
        if rows and (earliest is None or rows[0][0] < earliest):
            earliest = rows[0][0]
    if earliest is None:
        return 0.0
    return (datetime.now(timezone.utc) - earliest).total_seconds() / 86400.0


def build() -> str:
    d = json.loads(STATUS.read_text())
    variants = d.get("variants", [])
    # attach weekly stats
    stats = []
    for v in variants:
        wk = _weekly(v["slug"])
        if wk:
            stats.append({**v, **wk})
    if not stats:
        return ("📅 <b>BTC Bot Farm — week in review</b>\n\n"
                "Not enough history yet — the bots just started. Check back next week.")

    btc, btc_chg = _btc_weekly(variants)
    days = _days_of_data(variants)
    maturity = ("under 1 day of data" if days < 1
                else f"{days:.0f} days of data" if days < 14
                else f"{days / 7:.0f} weeks of data")

    def sgn(x):
        return f"+{x:.1f}" if x >= 0 else f"{x:.1f}"

    # Safest = unleveraged, ranked by profit minus worst dip (risk-adjusted)
    unlev = [s for s in stats if s.get("leverage", 1.0) <= 1.0 and not s["liq"]]
    safest = sorted(unlev, key=lambda s: s["ret"] - s["dip"], reverse=True)[:3]
    # Biggest gainers = everything by raw weekly return
    gainers = sorted(stats, key=lambda s: s["ret"], reverse=True)[:3]
    # Watch list
    watch = [s for s in stats if s["liq"] or s["dip"] > DIP_ALARM]

    bsign = "+" if btc_chg >= 0 else ""
    lines = ["📅 <b>BTC Bot Farm — week in review</b>",
             f"Bitcoin ${btc:,.0f} ({bsign}{btc_chg:.1f}% this week)",
             f"{len(variants)} bots · pretend money · {maturity}\n",
             "🛡️ <b>Safest performers</b> <i>(unleveraged — the real-money candidates)</i>"]
    for i, s in enumerate(safest, 1):
        lines.append(f"{i}. {s['name']} — {sgn(s['ret'])}% · worst dip −{s['dip']:.1f}%")

    lines.append("\n🚀 <b>Biggest gainers</b> <i>(incl. the leveraged 'for kicks' ones)</i>")
    for i, s in enumerate(gainers, 1):
        warn = " ⚠️" if (s.get("leverage", 1.0) > 1 and "⚠️" not in s["name"]) else ""
        lines.append(f"{i}. {s['name']}{warn} — {sgn(s['ret'])}%")

    if watch:
        lines.append("\n⚠️ <b>Watch list</b>")
        for s in watch:
            why = "💀 wiped out" if s["liq"] else f"big dip −{s['dip']:.1f}%"
            lines.append(f"• {s['name']} — {why}")
    else:
        lines.append("\n✅ No blow-ups — every bot survived the week.")

    note = ("\n<i>Still early — we judge properly at ~8–12 weeks, once a real market "
            "move has tested them.</i>" if days < 56 else "")
    lines.append(note)
    lines.append(f"https://{PUBLIC_URL}")
    return "\n".join(l for l in lines if l is not None)


if __name__ == "__main__":
    try:
        send(build())
    except Exception as exc:
        send(f"⚠️ BTC bot farm weekly digest failed: {exc}")
