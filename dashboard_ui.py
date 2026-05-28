"""
dashboard_ui.py — BTC Grid Bot Farm dashboard (plain-English).

Watch the grid-bot variants compete on live Bitcoin prices with pretend money.
Reads grid_farm/status.json + per-variant equity.csv (written by grid_farm.py).

    streamlit run dashboard_ui.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
FARM = ROOT / "grid_farm"
STATUS = FARM / "status.json"
PAPER = 10_000.0

st.set_page_config(page_title="BTC Grid Bot Farm", page_icon="📈", layout="wide")


def load_status():
    if not STATUS.exists():
        return None
    try:
        return json.loads(STATUS.read_text())
    except Exception:
        return None


def age(iso):
    try:
        mins = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
        return f"{int(mins)} min ago" if mins < 90 else f"{int(mins / 60)} h ago"
    except Exception:
        return "—"


st.title("📈 BTC Grid Bot Farm")
st.caption("Different bot styles competing on **real live Bitcoin prices** with **pretend money** — "
           "so we can see which style works best before risking a real dollar.")

status = load_status()
if not status:
    st.warning("No data yet. Start the farm in a Terminal:\n\n"
               "`cd ~/Documents/btc-wheel-bot && caffeinate -s python3.11 grid_farm.py`\n\n"
               "Then click Refresh.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Bitcoin price", f"${status['btc_price']:,.0f}")
c2.metric("Bots competing", len(status["variants"]))
c3.metric("Each started with", f"${PAPER:,.0f} (pretend)")
c4.metric("Last update", age(status["updated"]))
st.button("🔄 Refresh")

rows = sorted(status["variants"], key=lambda r: r["equity"], reverse=True)

# ── Leaderboard ──────────────────────────────────────────────────────────────
st.subheader("🏆 Leaderboard")
disp = [{
    "#": i,
    "Bot": r["name"],
    "Style": r["style"],
    "Account now": f"${r['equity']:,.0f}",
    "Profit": f"${r['profit']:+,.0f}  ({r['return_pct']:+.1f}%)",
    "Worst dip": f"−{r['max_drawdown_pct']:.1f}%",
    "Trades": r["trades"],
    "Right now": r["state"],
    "Days": f"{r['days_running']:.1f}",
} for i, r in enumerate(rows, 1)]
st.dataframe(pd.DataFrame(disp), hide_index=True, use_container_width=True)
st.caption("**Profit** is on a pretend $10,000 each. **Worst dip** = the biggest temporary drop from a high "
           "point along the way (smaller = smoother ride). A bot makes money in up *and* down markets — "
           "it feeds on price wiggles, not direction.")

# ── Equity chart ─────────────────────────────────────────────────────────────
st.subheader("💰 Account value over time")
series = {}
for r in status["variants"]:
    ec = FARM / r["slug"] / "equity.csv"
    if ec.exists():
        try:
            df = pd.read_csv(ec, parse_dates=["timestamp"])
            if len(df):
                series[r["name"]] = df.set_index("timestamp")["equity"]
        except Exception:
            pass
if series and max(len(s) for s in series.values()) > 1:
    st.line_chart(pd.DataFrame(series).sort_index().ffill())
else:
    st.info("This chart fills in as the farm runs (it updates every hour). Check back soon.")

# ── Per-variant detail ───────────────────────────────────────────────────────
st.subheader("🔍 Each bot, explained")
for r in rows:
    with st.expander(f"{r['name']} — {r['style']}"):
        brake = "ON — steps aside (goes to cash) in a sustained downturn" if r["trend_stop"] \
            else "OFF — always trading, even in a crash"
        lev = "none — only your own money (can't be wiped out)" if r["leverage"] == 1 \
            else f"{r['leverage']:.0f}× borrowed — amplifies gains AND losses, can be wiped to $0"
        st.markdown(
            f"- **Trades when price wiggles about:** {r['spacing_pct']:.0f}%\n"
            f"- **Safety brake:** {brake}\n"
            f"- **Borrowing (leverage):** {lev}\n"
            f"- **Right now:** {r['state']}\n"
            f"- **Account:** ${r['equity']:,.0f} — profit ${r['profit']:+,.0f}, worst dip −{r['max_drawdown_pct']:.1f}%"
        )
        if r["leverage"] > 1:
            st.warning("⚠️ This is the 'for kicks' leveraged bot. It can multiply the gains, but a sharp "
                       "crash can wipe it to **$0**. This simulation is *optimistic* — it can't see flash "
                       "crashes between hourly checks — so treat its big numbers with suspicion. It's here "
                       "to show *why* your real money stays unleveraged.")

st.caption("The farm updates every hour. This is all pretend money — nothing real is at stake yet.")
