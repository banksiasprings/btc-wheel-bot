"""
dashboard_ui.py — Streamlit dashboard for BTC Wheel Bot.

Tabs
----
  📊 Backtest      — interactive parameter sliders, run backtest, equity chart
  📈 Paper Trading — live paper trading monitor with auto-refresh
  🧬 Optimizer     — sweep / evolve parameter search, view results
  ⚙️  Config        — view and edit config.yaml

Run
---
    streamlit run dashboard_ui.py

Mobile access: open the URL shown in the sidebar from any phone/tablet on
the same WiFi network. A QR code is shown for easy scanning.
"""

from __future__ import annotations

import copy
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

# ── Path setup ─────────────────────────────────────────────────────────────────

BOT_DIR = Path(__file__).parent
sys.path.insert(0, str(BOT_DIR))
PYTHON  = sys.executable   # same interpreter running Streamlit (3.11)

# ── Page config (must be first Streamlit call) ─────────────────────────────────

st.set_page_config(
    page_title="BTC Wheel Bot",
    page_icon="₿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Colour palette ─────────────────────────────────────────────────────────────

C_BG    = "#0d1117"
C_CARD  = "#161b22"
C_GRID  = "#21262d"
C_TEXT  = "#c9d1d9"
C_MUTED = "#8b949e"
C_BLUE  = "#58a6ff"
C_GREEN = "#3fb950"
C_RED   = "#f85149"
C_AMBER = "#d29922"

# ── Custom CSS ─────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .stApp { background-color: #0d1117; }
    .metric-card {
        background: #161b22;
        border: 1px solid #21262d;
        border-radius: 8px;
        padding: 14px 18px;
        text-align: center;
    }
    .metric-label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.08em; }
    .metric-value { font-size: 24px; font-weight: 700; color: #c9d1d9; margin-top: 4px; }
    .metric-value.green { color: #3fb950; }
    .metric-value.red   { color: #f85149; }
    .metric-value.amber { color: #d29922; }
    .status-dot-green { display:inline-block; width:10px; height:10px; border-radius:50%; background:#3fb950; margin-right:6px; }
    .status-dot-red   { display:inline-block; width:10px; height:10px; border-radius:50%; background:#f85149; margin-right:6px; }
    .status-dot-amber { display:inline-block; width:10px; height:10px; border-radius:50%; background:#d29922; margin-right:6px; }
    div[data-testid="stSidebarContent"] { background-color: #0d1117; }
    .block-container { padding-top: 1rem; }
</style>
""", unsafe_allow_html=True)

# ── Helper functions ───────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Get the machine's local network IP for phone access."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _qr_image(url: str):
    """Return a PIL image of a QR code for the given URL."""
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=4, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#58a6ff", back_color="#0d1117")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception:
        return None


def load_yaml() -> dict:
    with open(BOT_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def save_yaml(data: dict) -> None:
    with open(BOT_DIR / "config.yaml", "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def read_trades() -> pd.DataFrame:
    path = BOT_DIR / "data" / "trades.csv"
    if path.exists() and path.stat().st_size > 10:
        df = pd.read_csv(path)
        return df
    return pd.DataFrame()


def read_overseer_log() -> pd.DataFrame:
    path = BOT_DIR / "logs" / "overseer_decisions.jsonl"
    if not path.exists():
        return pd.DataFrame()
    rows = []
    with open(path) as f:
        for line in f:
            try:
                rows.append(json.loads(line.strip()))
            except Exception:
                pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def bot_running() -> bool:
    proc = st.session_state.get("bot_proc")
    return proc is not None and proc.poll() is None


def kill_switch_active() -> bool:
    return (BOT_DIR / "KILL_SWITCH").exists()


def clear_kill_switch() -> None:
    ks = BOT_DIR / "KILL_SWITCH"
    if ks.exists():
        ks.unlink()


def _color_pnl(val: float) -> str:
    if val > 0:
        return "green"
    if val < 0:
        return "red"
    return ""


# ── Plotly chart helpers ───────────────────────────────────────────────────────

def _dark_layout(title: str = "", height: int = 350) -> dict:
    return dict(
        title=dict(text=title, font=dict(color=C_TEXT, size=12)),
        paper_bgcolor=C_BG,
        plot_bgcolor=C_CARD,
        font=dict(color=C_TEXT, size=11),
        xaxis=dict(gridcolor=C_GRID, zerolinecolor=C_GRID, showgrid=True),
        yaxis=dict(gridcolor=C_GRID, zerolinecolor=C_GRID, showgrid=True),
        legend=dict(bgcolor=C_CARD, bordercolor=C_GRID),
        margin=dict(l=60, r=20, t=36, b=40),
        height=height,
    )


def make_equity_chart(dates, equity, start_eq: float, title="Equity Curve") -> go.Figure:
    eq   = np.array(equity, dtype=float)
    fig  = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=eq, name="Equity",
        line=dict(color=C_BLUE, width=2),
        fill="tozeroy",
        fillcolor="rgba(88,166,255,0.07)",
    ))
    fig.add_hline(
        y=start_eq,
        line=dict(color=C_MUTED, dash="dash", width=1),
        annotation_text=f"Start ${start_eq:,.0f}",
        annotation_font=dict(color=C_MUTED, size=9),
    )
    fig.update_layout(**_dark_layout(title, height=300))
    return fig


def make_drawdown_chart(dates, equity) -> go.Figure:
    eq   = np.array(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd   = (eq - peak) / peak * 100
    fig  = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=dd, name="Drawdown %",
        fill="tozeroy",
        fillcolor="rgba(248,81,73,0.25)",
        line=dict(color=C_RED, width=1),
    ))
    fig.update_layout(**_dark_layout("Drawdown %", height=160))
    return fig


def make_pnl_bar(trades_df: pd.DataFrame) -> go.Figure:
    colors = [C_GREEN if p >= 0 else C_RED for p in trades_df["pnl_usd"]]
    fig = go.Figure(go.Bar(
        x=list(range(1, len(trades_df) + 1)),
        y=trades_df["pnl_usd"],
        marker_color=colors,
        name="P&L per trade",
    ))
    fig.update_layout(**_dark_layout("Trade P&L (USD)", height=220))
    return fig


def make_sensitivity_chart(sweep_results: list[dict], param: str) -> go.Figure:
    df  = pd.DataFrame(sweep_results).sort_values(param)
    fig = go.Figure(go.Scatter(
        x=df[param], y=df["fitness"],
        mode="lines+markers",
        line=dict(color=C_BLUE, width=2),
        marker=dict(size=6, color=C_BLUE),
    ))
    fig.update_layout(**_dark_layout(f"Fitness vs {param}", height=280))
    return fig


# ── Metric card HTML ───────────────────────────────────────────────────────────

def metric_card(label: str, value: str, colour: str = "") -> None:
    cls = f"metric-value {colour}" if colour else "metric-value"
    st.markdown(
        f'<div class="metric-card">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="{cls}">{value}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Sidebar ────────────────────────────────────────────────────────────────────

def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## ₿ BTC Wheel Bot")
        st.divider()

        # Bot status
        if kill_switch_active():
            st.markdown('<span class="status-dot-red"></span>**KILL SWITCH ACTIVE**',
                        unsafe_allow_html=True)
            if st.button("🗑️ Clear Kill Switch", use_container_width=True):
                clear_kill_switch()
                st.rerun()
        elif bot_running():
            st.markdown('<span class="status-dot-green"></span>**Bot Running**',
                        unsafe_allow_html=True)
        else:
            st.markdown('<span class="status-dot-amber"></span>**Bot Stopped**',
                        unsafe_allow_html=True)

        st.divider()

        # Network access for phone
        ip   = _local_ip()
        port = 8501
        url  = f"http://{ip}:{port}"
        st.markdown("**📱 Mobile Access**")
        st.code(url, language=None)
        qr = _qr_image(url)
        if qr:
            st.image(qr, caption="Scan to open on phone", width=180)

        st.divider()
        st.markdown(
            f"<small style='color:{C_MUTED}'>Python {sys.version[:6]} · "
            f"Streamlit {st.__version__}</small>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — BACKTEST
# ══════════════════════════════════════════════════════════════════════════════

def tab_backtest() -> None:
    st.markdown("### 📊 Backtest — Interactive Parameter Explorer")
    st.caption("Adjust parameters, hit **Run Backtest**, and see the results instantly.")

    # ── Quick presets ──────────────────────────────────────────────────────────
    with st.expander("⚡ Quick Presets — click to load a starting configuration"):
        pcol1, pcol2, pcol3 = st.columns(3)
        with pcol1:
            st.markdown("**Conservative**")
            st.caption("Δ15-20% OTM, monthly options, very selective on IV. Fewer trades, lower risk.")
            if st.button("Load Conservative", use_container_width=True):
                st.session_state["preset"] = dict(
                    iv_rank_threshold=0.60, target_delta_min=0.15, target_delta_max=0.20,
                    min_dte=21, max_dte=35, max_equity_per_leg=0.80, min_free_equity_fraction=0.0,
                    lookback_months=18, starting_equity=10000,
                )
                st.rerun()
        with pcol2:
            st.markdown("**Balanced**")
            st.caption("Δ15-25% OTM, weekly options, moderate IV filter. Good starting point.")
            if st.button("Load Balanced", use_container_width=True):
                st.session_state["preset"] = dict(
                    iv_rank_threshold=0.50, target_delta_min=0.15, target_delta_max=0.25,
                    min_dte=5, max_dte=14, max_equity_per_leg=0.80, min_free_equity_fraction=0.0,
                    lookback_months=18, starting_equity=10000,
                )
                st.rerun()
        with pcol3:
            st.markdown("**Aggressive**")
            st.caption("Δ20-35% OTM, weekly, low IV filter. More trades, more risk.")
            if st.button("Load Aggressive", use_container_width=True):
                st.session_state["preset"] = dict(
                    iv_rank_threshold=0.30, target_delta_min=0.20, target_delta_max=0.35,
                    min_dte=5, max_dte=14, max_equity_per_leg=0.80, min_free_equity_fraction=0.0,
                    lookback_months=18, starting_equity=10000,
                )
                st.rerun()

    # Load preset into session if just clicked
    _preset = st.session_state.pop("preset", None)

    raw = load_yaml()
    s   = raw.get("strategy", {})
    sz  = raw.get("sizing", {})
    bt  = raw.get("backtest", {})

    # If a preset was just loaded, override the config defaults for this render
    if _preset:
        s  = {**s,  "iv_rank_threshold": _preset["iv_rank_threshold"],
                    "target_delta_min":  _preset["target_delta_min"],
                    "target_delta_max":  _preset["target_delta_max"],
                    "min_dte":           _preset["min_dte"],
                    "max_dte":           _preset["max_dte"]}
        sz = {**sz, "max_equity_per_leg":         _preset["max_equity_per_leg"],
                    "min_free_equity_fraction":   _preset["min_free_equity_fraction"]}
        bt = {**bt, "lookback_months":  _preset["lookback_months"],
                    "starting_equity":  _preset["starting_equity"]}

    col_params, col_results = st.columns([1, 2], gap="large")

    with col_params:
        st.markdown("#### Strategy Parameters")

        iv_thresh = st.slider(
            "IV Rank Threshold",
            min_value=20, max_value=80, step=5,
            value=int(float(s.get("iv_rank_threshold", 0.50)) * 100),
            format="%d%%",
            help="Only sell when IV rank exceeds this. 50% = only trade when volatility is above its 1-year median. Higher = fewer but better-timed trades.",
        ) / 100

        delta_min = st.slider(
            "Target Delta Min",
            min_value=10, max_value=25, step=2,
            value=int(float(s.get("target_delta_min", 0.15)) * 100),
            format="Δ%d%%",
            help="Lower bound of how far OTM your strike will be. Δ15% = very safely OTM, rarely assigned.",
        ) / 100

        delta_max = st.slider(
            "Target Delta Max",
            min_value=20, max_value=45, step=2,
            value=int(float(s.get("target_delta_max", 0.30)) * 100),
            format="Δ%d%%",
            help="Upper bound. Keep this below Δ30% for safety — higher means closer to the money, more premium but more assignments.",
        ) / 100

        min_dte = st.slider(
            "Min DTE (days to expiry)",
            min_value=2, max_value=14, step=1,
            value=int(s.get("min_dte", 5)),
            help="Don't open a new position with fewer than this many days to expiry.",
        )
        max_dte = st.slider(
            "Max DTE (days to expiry)",
            min_value=7, max_value=45, step=7,
            value=int(s.get("max_dte", 35)),
            help="7 = weekly options (small premium, fast decay). 28-35 = monthly (better premium/risk). Monthly recommended.",
        )

        st.markdown("#### Sizing & Risk")

        equity_frac_pct = st.slider(
            "Max Equity per Leg (%)",
            min_value=1, max_value=30, step=1,
            value=max(1, int(float(sz.get("max_equity_per_leg", 0.05)) * 100)),
            format="%d%%",
            help="Max % of your account used as collateral for one leg. With $10k account and BTC options, you need at least 70-80% because the minimum contract (0.1 BTC) costs ~$7-8k collateral. With larger accounts you can set this lower.",
        )
        equity_frac = equity_frac_pct / 100

        free_margin_pct = st.slider(
            "Min Free Capital Buffer (%)",
            min_value=0, max_value=50, step=5,
            value=int(float(sz.get("min_free_equity_fraction", 0.25)) * 100),
            format="%d%%",
            help="Always keep this % of account unencumbered as a safety buffer. Set to 0% on a small account (it may block every trade otherwise).",
        )
        free_margin = free_margin_pct / 100

        st.markdown("#### Backtest Settings")
        lookback = st.slider(
            "Lookback (months)",
            min_value=3, max_value=24, step=3,
            value=int(bt.get("lookback_months", 12)),
        )
        starting_eq = st.number_input(
            "Starting Equity (USD)",
            min_value=1000, max_value=1_000_000, step=1000,
            value=int(bt.get("starting_equity", 10000)),
        )

        run_btn = st.button("▶ Run Backtest", type="primary", use_container_width=True)

    with col_results:
        if run_btn:
            params = dict(
                iv_rank_threshold=iv_thresh,
                target_delta_min=delta_min,
                target_delta_max=delta_max,
                min_dte=min_dte,
                max_dte=max_dte,
                max_equity_per_leg=equity_frac,
                min_free_equity_fraction=free_margin,
                lookback_months=lookback,
                starting_equity=float(starting_eq),
            )
            with st.spinner("Fetching data from Deribit and running simulation…"):
                results, error = _run_backtest(params)
            if error:
                st.error(f"Backtest failed: {error}")
            else:
                st.session_state["last_bt_results"] = results
                st.session_state["last_bt_params"]  = params

        results = st.session_state.get("last_bt_results")
        params  = st.session_state.get("last_bt_params", {})

        if results is None:
            st.info("Set your parameters on the left and click **Run Backtest**.")
            return

        # ── Metrics row ───────────────────────────────────────────────────
        c1, c2, c3, c4, c5 = st.columns(5)
        ret_col  = "green" if results.total_return_pct >= 0 else "red"
        dd_col   = "red"   if results.max_drawdown_pct < -10 else "amber"
        with c1: metric_card("Total Return",   f"{results.total_return_pct:+.1f}%",  ret_col)
        with c2: metric_card("Ann. Return",    f"{results.annualized_return_pct:+.1f}%", ret_col)
        with c3: metric_card("Sharpe",         f"{results.sharpe_ratio:.2f}")
        with c4: metric_card("Max Drawdown",   f"{results.max_drawdown_pct:.1f}%",   dd_col)
        with c5: metric_card("Win Rate",        f"{results.win_rate_pct:.0f}%",
                              "green" if results.win_rate_pct >= 60 else "amber")

        st.markdown("")
        c6, c7, c8 = st.columns(3)
        with c6: metric_card("Trades",        str(results.num_cycles))
        with c7: metric_card("Ending Equity", f"${results.ending_equity:,.0f}")
        with c8: metric_card("Avg Premium Yield", f"{results.avg_premium_yield_pct:.2f}%/leg")

        st.markdown("")

        # ── Charts ───────────────────────────────────────────────────────
        if results.dates and results.equity_curve:
            st.plotly_chart(
                make_equity_chart(results.dates, results.equity_curve,
                                  params.get("starting_equity", 10000)),
                use_container_width=True,
            )
            st.plotly_chart(make_drawdown_chart(results.dates, results.equity_curve),
                            use_container_width=True)

        # ── Trades table ─────────────────────────────────────────────────
        if results.trades:
            df = pd.DataFrame([t.__dict__ for t in results.trades])
            show_cols = ["cycle_num", "open_date", "close_date", "option_type",
                         "strike", "spot_at_close", "pnl_usd", "equity_after",
                         "iv_rank", "rolled", "itm_at_expiry"]
            df = df[[c for c in show_cols if c in df.columns]]
            st.dataframe(
                df.tail(30).style.map(
                    lambda v: "color: #3fb950" if isinstance(v, (int, float)) and v > 0
                    else ("color: #f85149" if isinstance(v, (int, float)) and v < 0 else ""),
                    subset=["pnl_usd"] if "pnl_usd" in df.columns else [],
                ),
                use_container_width=True,
                height=280,
            )
            st.plotly_chart(make_pnl_bar(df), use_container_width=True)


def _run_backtest(params: dict):
    """Run a backtest with the given params. Returns (BacktestResults, error_str)."""
    try:
        from config import cfg as _base
        from backtester import Backtester

        custom = copy.deepcopy(_base)
        custom.strategy.iv_rank_threshold  = params["iv_rank_threshold"]
        custom.strategy.target_delta_min   = params["target_delta_min"]
        custom.strategy.target_delta_max   = params["target_delta_max"]
        custom.strategy.min_dte            = int(params["min_dte"])
        custom.strategy.max_dte            = int(params["max_dte"])
        custom.sizing.max_equity_per_leg   = params["max_equity_per_leg"]
        custom.sizing.min_free_equity_fraction = params["min_free_equity_fraction"]
        custom.backtest.lookback_months    = int(params["lookback_months"])
        custom.backtest.starting_equity    = float(params["starting_equity"])

        bt = Backtester(config=custom)
        return bt.run(), None
    except Exception as exc:
        return None, str(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — PAPER TRADING
# ══════════════════════════════════════════════════════════════════════════════

def tab_paper() -> None:
    st.markdown("### 📈 Paper Trading Monitor")

    # ── Controls ─────────────────────────────────────────────────────────────
    ctrl1, ctrl2, ctrl3 = st.columns([1, 1, 2])

    with ctrl1:
        if bot_running():
            if st.button("⏹ Stop Bot", type="secondary", use_container_width=True):
                proc = st.session_state.get("bot_proc")
                if proc:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                st.session_state["bot_proc"] = None
                st.rerun()
        else:
            if st.button("▶ Start Paper Trading", type="primary", use_container_width=True):
                if kill_switch_active():
                    st.error("Kill switch is active — clear it in the sidebar first.")
                else:
                    proc = subprocess.Popen(
                        [PYTHON, str(BOT_DIR / "main.py"), "--mode", "paper"],
                        cwd=str(BOT_DIR),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                    )
                    st.session_state["bot_proc"] = proc
                    st.session_state["bot_start_time"] = datetime.utcnow()
                    st.rerun()

    with ctrl2:
        if kill_switch_active():
            if st.button("🗑️ Clear Kill Switch", use_container_width=True):
                clear_kill_switch()
                st.rerun()
        elif bot_running():
            if st.button("🛑 Emergency Stop (Kill Switch)", use_container_width=True):
                (BOT_DIR / "KILL_SWITCH").write_text(
                    f"Manual kill from dashboard at {datetime.utcnow().isoformat()}\n"
                    "Delete this file to resume trading."
                )
                st.rerun()

    with ctrl3:
        auto_refresh = st.toggle("Auto-refresh (15s)", value=True)

    st.divider()

    # ── Status header ─────────────────────────────────────────────────────────
    if kill_switch_active():
        st.error("🛑 **KILL SWITCH ACTIVE** — Trading halted. Clear it in the sidebar to resume.")
    elif bot_running():
        start = st.session_state.get("bot_start_time")
        elapsed = ""
        if start:
            secs = int((datetime.utcnow() - start).total_seconds())
            elapsed = f" · running {secs // 60}m {secs % 60}s"
        st.success(f"✅ Bot is running in paper mode{elapsed}")
    else:
        st.warning("⚠️ Bot is not running — click **Start Paper Trading** above.")

    # ── Trade data ────────────────────────────────────────────────────────────
    trades_df = read_trades()

    if trades_df.empty:
        st.info("No trades recorded yet. The bot will record trades to `data/trades.csv` as they complete.")
        _render_log_tail()
    else:
        # Metrics
        raw = load_yaml()
        start_eq = float(raw.get("backtest", {}).get("starting_equity", 10000))
        end_eq   = float(trades_df["equity_after"].iloc[-1]) if "equity_after" in trades_df.columns else start_eq
        wins     = (trades_df["pnl_usd"] >= 0).sum() if "pnl_usd" in trades_df.columns else 0
        total    = len(trades_df)
        win_rate = wins / total * 100 if total else 0
        total_pnl = trades_df["pnl_usd"].sum() if "pnl_usd" in trades_df.columns else 0

        c1, c2, c3, c4 = st.columns(4)
        with c1: metric_card("Equity", f"${end_eq:,.0f}", "green" if end_eq >= start_eq else "red")
        with c2: metric_card("Total P&L", f"${total_pnl:+,.0f}", "green" if total_pnl >= 0 else "red")
        with c3: metric_card("Win Rate", f"{win_rate:.0f}%", "green" if win_rate >= 60 else "amber")
        with c4: metric_card("Trades", str(total))

        st.markdown("")

        # Equity curve
        if "equity_after" in trades_df.columns:
            eq_vals = [start_eq] + list(trades_df["equity_after"].values)
            x_vals  = list(range(len(eq_vals)))
            st.plotly_chart(
                make_equity_chart(x_vals, eq_vals, start_eq, "Paper Trading — Equity Curve"),
                use_container_width=True,
            )
            st.plotly_chart(make_drawdown_chart(x_vals, eq_vals), use_container_width=True)

        # P&L bar
        if "pnl_usd" in trades_df.columns:
            st.plotly_chart(make_pnl_bar(trades_df), use_container_width=True)

        # Recent trades
        st.markdown("#### Recent Trades")
        show = ["cycle_num", "open_date", "close_date", "option_type", "strike",
                "pnl_usd", "equity_after", "rolled", "itm_at_expiry"]
        show = [c for c in show if c in trades_df.columns]
        st.dataframe(trades_df[show].tail(20), use_container_width=True, height=260)

        # Overseer decisions
        ov_df = read_overseer_log()
        if not ov_df.empty:
            st.markdown("#### AI Overseer Decisions")
            st.dataframe(ov_df.tail(10)[["timestamp_utc", "decision", "confidence", "reasoning"]],
                         use_container_width=True, height=200)

        _render_log_tail()

    # ── Auto-refresh ──────────────────────────────────────────────────────────
    if auto_refresh and bot_running():
        time.sleep(15)
        st.rerun()


def _render_log_tail() -> None:
    """Show the last 40 lines of the most recent log file."""
    log_dir = BOT_DIR / "logs"
    logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True) if log_dir.exists() else []
    if logs:
        with st.expander("📋 Live Log (last 40 lines)", expanded=False):
            try:
                lines = logs[0].read_text().splitlines()[-40:]
                st.code("\n".join(lines), language=None)
            except Exception:
                st.caption("Could not read log file.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — OPTIMIZER
# ══════════════════════════════════════════════════════════════════════════════

def tab_optimizer() -> None:
    st.markdown("### 🧬 Parameter Optimizer")
    st.caption(
        "**Sweep mode** tests one parameter at a time to show sensitivity. "
        "**Evolve mode** runs a genetic algorithm to find the best combination."
    )

    mode = st.radio("Mode", ["Sweep (one param at a time)", "Evolve (genetic algorithm)"],
                    horizontal=True)
    is_sweep = "Sweep" in mode

    col_ctrl, col_res = st.columns([1, 2], gap="large")

    with col_ctrl:
        if is_sweep:
            st.markdown("#### Sweep Settings")
            param_choices = [
                "all (run all params)",
                "iv_rank_threshold", "target_delta_min", "target_delta_max",
                "approx_otm_offset", "max_dte", "min_dte",
                "max_equity_per_leg", "premium_fraction_of_spot", "iv_rank_window_days",
            ]
            sweep_param = st.selectbox("Parameter to sweep", param_choices)
        else:
            st.markdown("#### Evolution Settings")
            pop_size   = st.slider("Population size",   min_value=8,  max_value=50, step=4, value=20)
            generations = st.slider("Generations",       min_value=3,  max_value=20, step=1, value=8)
            elite_keep  = st.slider("Elite survivors",   min_value=2,  max_value=10, step=1, value=4)
            mut_rate    = st.slider("Mutation rate",     min_value=0.1, max_value=0.6, step=0.05, value=0.3)

        opt_running = st.session_state.get("opt_proc") is not None and \
                      st.session_state.get("opt_proc").poll() is None

        if opt_running:
            st.warning("Optimizer is running…")
            if st.button("⏹ Stop Optimizer", use_container_width=True):
                p = st.session_state.get("opt_proc")
                if p:
                    p.terminate()
                st.session_state["opt_proc"] = None
                st.rerun()
        else:
            if st.button("▶ Start Optimizer", type="primary", use_container_width=True):
                cmd = [PYTHON, str(BOT_DIR / "optimizer.py"), "--mode",
                       "sweep" if is_sweep else "evolve"]
                if is_sweep and sweep_param != "all (run all params)":
                    cmd += ["--param", sweep_param]
                if not is_sweep:
                    cmd += [
                        "--population", str(pop_size),
                        "--generations", str(generations),
                        "--elite", str(elite_keep),
                        "--mutation", str(mut_rate),
                    ]
                proc = subprocess.Popen(
                    cmd, cwd=str(BOT_DIR),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )
                st.session_state["opt_proc"] = proc
                st.session_state["opt_start"] = datetime.utcnow()
                st.rerun()

        if opt_running:
            st.markdown("")
            start = st.session_state.get("opt_start")
            if start:
                secs = int((datetime.utcnow() - start).total_seconds())
                st.caption(f"Running for {secs // 60}m {secs % 60}s")
            if st.button("🔄 Refresh Results", use_container_width=True):
                st.rerun()

    with col_res:
        _render_optimizer_results(is_sweep)

        # Auto-refresh while running
        if opt_running:
            time.sleep(10)
            st.rerun()


def _render_optimizer_results(is_sweep: bool) -> None:
    """Display optimizer outputs when available."""
    if is_sweep:
        img_path = BOT_DIR / "sweep_sensitivity.png"
        if img_path.exists():
            st.markdown("#### Sweep Sensitivity Chart")
            st.image(str(img_path), use_container_width=True)
        else:
            st.info("Sweep chart will appear here once the run completes.")
    else:
        leaderboard_path = BOT_DIR / "evolution_leaderboard.csv"
        evo_img          = BOT_DIR / "evolution_progress.png"
        best_path        = BOT_DIR / "best_genome.yaml"

        if evo_img.exists():
            st.markdown("#### Evolution Progress")
            st.image(str(evo_img), use_container_width=True)

        if best_path.exists():
            with open(best_path) as f:
                best = yaml.safe_load(f)
            st.markdown("#### 🏆 Best Genome Found")
            best_df = pd.DataFrame(list(best.items()), columns=["Parameter", "Value"])
            st.dataframe(best_df, use_container_width=True, height=200)
            if st.button("Apply Best Genome to Config", use_container_width=True):
                _apply_genome_to_config(best)
                st.success("Best genome applied to config.yaml! Re-run a backtest to verify.")

        if leaderboard_path.exists():
            st.markdown("#### Leaderboard (Top Genomes)")
            lb = pd.read_csv(leaderboard_path)
            st.dataframe(lb.head(10), use_container_width=True, height=240)

        if not evo_img.exists() and not leaderboard_path.exists():
            st.info("Evolution results will appear here once the run completes.")


def _apply_genome_to_config(genome: dict) -> None:
    """Apply a best_genome.yaml back to config.yaml."""
    raw = load_yaml()
    mapping = {
        "iv_rank_threshold":        ("strategy", "iv_rank_threshold"),
        "target_delta_min":         ("strategy", "target_delta_min"),
        "target_delta_max":         ("strategy", "target_delta_max"),
        "max_dte":                  ("strategy", "max_dte"),
        "min_dte":                  ("strategy", "min_dte"),
        "max_equity_per_leg":       ("sizing",   "max_equity_per_leg"),
        "approx_otm_offset":        ("backtest", "approx_otm_offset"),
        "premium_fraction_of_spot": ("backtest", "premium_fraction_of_spot"),
    }
    for key, (section, field) in mapping.items():
        if key in genome and section in raw:
            raw[section][field] = genome[key]
    save_yaml(raw)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — CONFIG
# ══════════════════════════════════════════════════════════════════════════════

def tab_config() -> None:
    st.markdown("### ⚙️ Configuration")
    st.caption("Changes are written directly to `config.yaml`. Restart the bot after saving.")

    raw = load_yaml()
    changed = {}

    def section(title: str) -> None:
        st.markdown(f"#### {title}")

    col1, col2 = st.columns(2, gap="large")

    with col1:
        section("Strategy")
        s = raw.get("strategy", {})
        changed.setdefault("strategy", {})
        changed["strategy"]["iv_rank_threshold"] = st.number_input(
            "IV Rank Threshold", value=float(s.get("iv_rank_threshold", 0.50)),
            min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        changed["strategy"]["target_delta_min"] = st.number_input(
            "Target Delta Min", value=float(s.get("target_delta_min", 0.15)),
            min_value=0.05, max_value=0.40, step=0.025, format="%.3f")
        changed["strategy"]["target_delta_max"] = st.number_input(
            "Target Delta Max", value=float(s.get("target_delta_max", 0.30)),
            min_value=0.10, max_value=0.50, step=0.025, format="%.3f")
        changed["strategy"]["min_dte"] = st.number_input(
            "Min DTE", value=int(s.get("min_dte", 5)), min_value=1, max_value=30)
        changed["strategy"]["max_dte"] = st.number_input(
            "Max DTE", value=int(s.get("max_dte", 35)), min_value=7, max_value=90)

        section("Risk")
        r = raw.get("risk", {})
        changed.setdefault("risk", {})
        changed["risk"]["max_adverse_delta"] = st.number_input(
            "Max Adverse Delta", value=float(r.get("max_adverse_delta", 0.40)),
            min_value=0.10, max_value=0.80, step=0.05, format="%.2f")
        changed["risk"]["max_loss_per_leg"] = st.number_input(
            "Max Loss per Leg", value=float(r.get("max_loss_per_leg", 0.02)),
            min_value=0.005, max_value=0.20, step=0.005, format="%.3f")
        changed["risk"]["max_daily_drawdown"] = st.number_input(
            "Max Daily Drawdown", value=float(r.get("max_daily_drawdown", 0.10)),
            min_value=0.02, max_value=0.30, step=0.01, format="%.2f")

    with col2:
        section("Sizing")
        sz = raw.get("sizing", {})
        changed.setdefault("sizing", {})
        changed["sizing"]["max_equity_per_leg"] = st.number_input(
            "Max Equity per Leg", value=float(sz.get("max_equity_per_leg", 0.05)),
            min_value=0.01, max_value=0.20, step=0.01, format="%.2f")
        changed["sizing"]["min_free_equity_fraction"] = st.number_input(
            "Min Free Equity Fraction", value=float(sz.get("min_free_equity_fraction", 0.25)),
            min_value=0.0, max_value=0.60, step=0.05, format="%.2f")
        changed["sizing"]["collateral_buffer"] = st.number_input(
            "Collateral Buffer", value=float(sz.get("collateral_buffer", 1.50)),
            min_value=1.0, max_value=3.0, step=0.10, format="%.2f")
        changed["sizing"]["max_open_legs"] = st.number_input(
            "Max Open Legs", value=int(sz.get("max_open_legs", 1)), min_value=1, max_value=5)

        section("AI Overseer")
        ov = raw.get("overseer", {})
        changed.setdefault("overseer", {})
        changed["overseer"]["enabled"] = st.toggle(
            "Overseer Enabled", value=bool(ov.get("enabled", True)))
        changed["overseer"]["check_interval_minutes"] = st.number_input(
            "Check Interval (minutes)", value=int(ov.get("check_interval_minutes", 60)),
            min_value=5, max_value=720)

        section("Backtest Defaults")
        bt = raw.get("backtest", {})
        changed.setdefault("backtest", {})
        changed["backtest"]["starting_equity"] = st.number_input(
            "Starting Equity (USD)", value=float(bt.get("starting_equity", 10000)),
            min_value=1000.0, max_value=1_000_000.0, step=1000.0)
        changed["backtest"]["lookback_months"] = st.number_input(
            "Lookback Months", value=int(bt.get("lookback_months", 12)),
            min_value=1, max_value=36)

    st.divider()

    if st.button("💾 Save Config", type="primary"):
        # Merge changes back into raw (preserve unchanged sections/keys)
        for section_key, section_vals in changed.items():
            if section_key not in raw:
                raw[section_key] = {}
            for k, v in section_vals.items():
                raw[section_key][k] = v
        save_yaml(raw)
        st.success("✅ config.yaml saved. Restart the bot for changes to take effect.")

    st.divider()
    st.markdown("#### Raw YAML Preview")
    with st.expander("View config.yaml", expanded=False):
        current = (BOT_DIR / "config.yaml").read_text()
        st.code(current, language="yaml")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    render_sidebar()

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Backtest",
        "📈 Paper Trading",
        "🧬 Optimizer",
        "⚙️ Config",
    ])

    with tab1:
        tab_backtest()
    with tab2:
        tab_paper()
    with tab3:
        tab_optimizer()
    with tab4:
        tab_config()


if __name__ == "__main__":
    main()
