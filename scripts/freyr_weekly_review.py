#!/usr/bin/env python3.11
"""
freyr_weekly_review.py — the auto-generated weekly Freyr system review.

Feeds the 📊 Review tab at bot.banksiaspringsfarm.com. Fires DAILY at 5 AM AEST via
launchd (`com.wheelbot.freyrreview`) but only writes a NEW review on Sundays (or with
--force). It reads the Freyr v0.1.1 production snapshot, the specialist event logs
(last 7 days, by file mtime), MISSION_CONTROL.md, and the deployment-sprint calendar,
then composes a plain-English markdown review and writes:

    ~/Documents/freyr/reviews/YYYY-MM-DD.md      (permanent record; the tab renders the latest)

…and sends Steven a Telegram info ping pointing at the Review tab.

Plain English on purpose — Steven isn't finance-literate and reads on his phone, so
jargon is glossed (leverage, drawdown, Sharpe, regime) the first time it appears.

Install once:
    cp scripts/com.wheelbot.freyrreview.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.wheelbot.freyrreview.plist
Generate now (first review / manual):
    python3.11 scripts/freyr_weekly_review.py --force
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))   # so `telegram_summary` (repo root) imports under launchd
try:
    from telegram_summary import send
except Exception:                                  # pragma: no cover - telegram optional
    def send(text: str) -> bool:
        print("[telegram unavailable] would send:\n", text[:300])
        return False

FREYR = Path("/Users/openclaw/Documents/freyr")
SNAPS = FREYR / "paper" / "snapshots"
EVENTS = FREYR / "paper" / "events"
REVIEWS = FREYR / "reviews"
MISSION = FREYR / "MISSION_CONTROL.md"

PROD = "v0.1.1"                 # the deployable production ensemble (DEPLOYMENT_PLAN.md)
ENSEMBLES = ["v0.1.1", "v0.2", "v0.3"]

# Deployment-sprint calendar — tracks DEPLOYMENT_PLAN.md (2026-06-09 → real money by
# 2026-06-30). Update these two dates if the plan's dates move.
SPRINT_START = date(2026, 6, 9)
SPRINT_DEADLINE = date(2026, 6, 30)
SPRINT_LEN = (SPRINT_DEADLINE - SPRINT_START).days   # 21


# ── data loaders ──────────────────────────────────────────────────────────────
def load_variant(variant: str):
    base = SNAPS / variant
    try:
        idx = json.loads((base / "index.json").read_text())
        latest = idx["latest"]
        snap = json.loads((base / f"{latest}.json").read_text())
        summ = next((s for s in idx.get("summary", []) if s.get("date") == latest), {})
        return snap, summ
    except Exception:
        return None, None


def recent_events(days: int = 7) -> dict[str, list[dict]]:
    """Specialist events from log files modified in the last `days` (the sim dates in
    the filenames are backtest dates, so we select by wall-clock mtime)."""
    out: dict[str, list[dict]] = {}
    if not EVENTS.exists():
        return out
    cutoff = datetime.now().timestamp() - days * 86400
    for botdir in sorted(EVENTS.iterdir()):
        if not botdir.is_dir():
            continue
        evs: list[dict] = []
        for f in botdir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    continue
                for ln in f.read_text().splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        evs.append(json.loads(ln))
                    except Exception:
                        continue
            except Exception:
                continue
        if evs:
            out[botdir.name] = evs
    return out


def mission_next() -> str:
    """The current 'Next milestone' line from MISSION_CONTROL, markdown stripped."""
    try:
        m = re.search(r"Next milestone:\*\*\s*(.+)", MISSION.read_text())
    except Exception:
        return ""
    if not m:
        return ""
    line = re.sub(r"\*\*|`", "", m.group(1)).strip()
    return (line[:340] + "…") if len(line) > 340 else line


# ── formatting helpers ────────────────────────────────────────────────────────
def pretty(key: str) -> str:
    return key.replace("_", " ").title()


def pct(x, dp: int = 2, sign: bool = True) -> str:
    fmt = f"{{:+.{dp}f}}%" if sign else f"{{:.{dp}f}}%"
    return fmt.format(x)


# ── the review composer ───────────────────────────────────────────────────────
def compose(today: date) -> str:
    snap, summ = load_variant(PROD)
    L: list[str] = []
    L.append(f"# Freyr weekly review — {today.isoformat()}")
    L.append("")
    L.append(f"*Auto-generated every Sunday 5 AM AEST. Production ensemble: **Freyr {PROD}** "
             "(the bundle headed for real money). All figures are paper — pretend money.*")
    L.append("")

    if not snap:
        L.append("## ⚠️ No snapshot")
        L.append(f"Couldn't read a `{PROD}` snapshot off disk. The paper tick writes one daily — "
                 "if this persists, the paper run has stalled.")
        return "\n".join(L)

    p = snap.get("portfolio", {})
    esc = snap.get("escape", {})
    books = snap.get("books", []) or []
    kill = (summ or {}).get("kill_status", "ARMED")
    snap_date = snap.get("date", "")

    regime = (p.get("regime") or "—")
    lev = p.get("leverage", 1.0)
    n_books, n_active = p.get("n_books", len(books)), p.get("n_books_active", 0)
    n_holding = p.get("n_books_holding", 0)
    dd = p.get("current_dd", 0.0) * 100
    sharpe = p.get("sharpe", 0.0)
    cagr = p.get("cagr", 0.0) * 100

    # ── What's running ──
    sleeves = sorted(books, key=lambda b: b.get("realized_weight", 0), reverse=True)[:5]
    sleeve_txt = ", ".join(f"{pretty(b.get('key','?'))} {b.get('realized_weight',0)*100:.0f}%"
                           for b in sleeves) or "—"
    L.append("## What's running")
    L.append(f"- **{n_active}/{n_books}** books are armed; **{n_holding}** are actually holding a position right now "
             "(the rest are flat, waiting for their signal).")
    L.append(f"- The system reads the market as **{regime}** "
             "*(its label for the current market mood)*.")
    L.append(f"- Running **{lev:.2f}× leverage** *(size per dollar of capital — {lev:.2f}× means "
             f"about ${lev:,.2f} of position for every $1)*.")
    L.append(f"- Biggest positions *(gross weight per book — sums above 100% because the ensemble "
             f"runs leveraged)*: {sleeve_txt}.")
    L.append("")

    # ── What's working ──
    # pnl_cum is each book's STANDALONE cumulative result over its own full track (its
    # proven edge), not the days-old paper period — labelled as such so the big numbers
    # aren't mistaken for paper P&L. Paired with how much of the book it's carrying now.
    ranked = sorted(books, key=lambda b: b.get("pnl_cum", 0), reverse=True)
    L.append("## What's working")
    L.append("*The books pulling the ensemble along — ranked by each book's standalone cumulative "
             "result (its own full track, i.e. what it's proven it can do), with the share it's "
             "carrying right now.*")
    for b in ranked[:3]:
        L.append(f"- **{pretty(b.get('key','?'))}** — {pct(b.get('pnl_cum',0)*100)} standalone · "
                 f"Sharpe {b.get('standalone_sharpe',0):.2f} *(smoothness of returns — higher is steadier)* · "
                 f"carrying {b.get('realized_weight',0)*100:.0f}% gross weight.")
    L.append("")

    # ── What's not ──
    L.append("## What's not")
    L.append("*Books standing aside or dragging — with why (live paper state).*")
    laggards = ranked[-3:][::-1]
    for b in laggards:
        st = b.get("activation_state", "—")
        why = []
        if not b.get("armed", True):
            why.append("disarmed by its own drawdown stop")
        if st == "dormant":
            why.append("dormant — its market signal isn't firing")
        bdd = b.get("book_dd", 0) * 100
        if bdd < -3:
            why.append(f"down {bdd:.1f}% from its own peak")
        reason = ("; ".join(why)) if why else "just trailing the pack"
        L.append(f"- **{pretty(b.get('key','?'))}** — {pct(b.get('pnl_cum',0)*100)} standalone · {reason}.")
    disarmed = [pretty(b.get("key", "?")) for b in books if not b.get("armed", True)]
    if disarmed:
        L.append(f"- **Disarmed books** *(stood down by their drawdown stop)*: {', '.join(disarmed)}.")
    L.append("")

    # ── What changed ──
    evmap = recent_events(7)
    L.append("## What changed")
    L.append("*Activity across the standalone specialists in the last 7 days.*")
    if evmap:
        for bot in sorted(evmap, key=lambda k: -len(evmap[k]))[:6]:
            evs = evmap[bot]
            kinds: dict[str, int] = {}
            for e in evs:
                k = (e.get("event") or "event").replace("_", " ")
                kinds[k] = kinds.get(k, 0) + 1
            summ_k = ", ".join(f"{v}× {k}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])[:3])
            L.append(f"- **{bot.title()}** — {len(evs)} events ({summ_k}).")
    else:
        L.append("- No new specialist events logged this week — the library has been quiet.")
    if esc.get("transitioned"):
        L.append(f"- **Escape governor moved**: {esc.get('reason','')}.")
    L.append("")

    # ── What the system thinks ──
    tier, tname = esc.get("tier", 0), esc.get("tier_name", "observe")
    killline = ("**ARMED** — the portfolio kill-switch is set and watching (it flattens everything if "
                "drawdown hits the floor); it has not tripped"
                if kill == "ARMED" else "**TRIPPED** — the kill-switch fired; the book has been flattened")
    L.append("## What the system thinks")
    L.append(f"- **Regime**: {regime} — the classifier's read on the market.")
    L.append(f"- **Escape tier**: T{tier} · {tname} — {esc.get('reason','')} "
             "*(T0 = calm/observe; higher tiers = the risk governor is actively de-risking)*.")
    L.append(f"- **Kill switch**: {killline}.")
    L.append(f"- **Headline (model track)**: CAGR {pct(cagr,1)} *(annual growth rate)* · "
             f"Sharpe {sharpe:.2f} · current drawdown {dd:.1f}% "
             "*(how far below its best the account has dipped)*.")
    L.append("")

    # ── What's next ──
    days_in = (today - SPRINT_START).days
    days_left = (SPRINT_DEADLINE - today).days
    sprint_line = (f"Deployment sprint **day {days_in}/{SPRINT_LEN}** "
                   f"({days_left} days to the 2026-06-30 real-money deadline)."
                   if 0 <= days_in <= SPRINT_LEN else
                   (f"Past the {SPRINT_DEADLINE.isoformat()} sprint deadline." if days_in > SPRINT_LEN
                    else f"Sprint starts {SPRINT_START.isoformat()}."))
    nxt = mission_next()
    spec_n = sum(1 for d in (EVENTS.iterdir() if EVENTS.exists() else []) if d.is_dir())
    L.append("## What's next")
    L.append(f"- {sprint_line}")
    if nxt:
        L.append(f"- **Open research**: {nxt}")
    L.append(f"- **The wider library**: {spec_n} standalone specialists in flight beyond the {len(ENSEMBLES)} "
             "ensemble profiles (browse them on the 🤖 Books tab).")
    L.append("- **Scheduled tasks**: daily farm summary 8 AM · this review Sunday 5 AM · "
             "weekly scorecard Sunday 5 PM (all AEST).")
    L.append("")
    L.append(f"---")
    L.append(f"*Snapshot as of {snap_date}. Generated {today.isoformat()}. "
             "Everything here is paper — no real money is at stake yet.*")
    return "\n".join(L)


def main() -> None:
    force = "--force" in sys.argv
    no_send = "--no-send" in sys.argv          # generate the file but don't ping Telegram
    today = datetime.now().date()
    if not force and today.weekday() != 6:        # 6 = Sunday
        print(f"{today} (weekday {today.weekday()}) is not Sunday and no --force — skipping.")
        return
    REVIEWS.mkdir(parents=True, exist_ok=True)
    md = compose(today)
    out = REVIEWS / f"{today.isoformat()}.md"
    out.write_text(md)
    print("wrote", out, f"({len(md)} chars)")
    if no_send:
        print("--no-send: skipping Telegram ping.")
        return
    try:
        send("📊 <b>Freyr weekly review ready</b> — what's running, working, and what's "
             "next for the week.\nCheck the Review tab → https://bot.banksiaspringsfarm.com/#review")
    except Exception as exc:
        print("telegram send failed:", exc)


if __name__ == "__main__":
    main()
