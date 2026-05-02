"""
dashboard_ui.py — Streamlit dashboard for BTC Wheel Bot.

Tabs
----
  📊 Backtest      — interactive parameter sliders, run backtest, equity chart
  📈 Paper Trading — live paper trading monitor
  🧬 Optimizer     — sweep / evolve parameter search, view results
  ⚙️  Config        — view and edit config.yaml
  📋 Recommendations — batch backtest analysis
  🔧 Settings      — kill switch, logs, danger zone

Run
---
    streamlit run dashboard_ui.py
"""

from __future__ import annotations

import copy
import json
import os
import signal
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

# ── Theme ──────────────────────────────────────────────────────────────────────

_theme    = st.session_state.get("theme", "🌙 Dark")
_is_light = "☀️" in _theme

# ── Colour palette (switches with theme) ───────────────────────────────────────

if _is_light:
    C_BG    = "#f6f8fa"
    C_CARD  = "#ffffff"
    C_GRID  = "#d0d7de"
    C_TEXT  = "#24292f"
    C_MUTED = "#57606a"
    C_BLUE  = "#0969da"
    C_GREEN = "#1a7f37"
    C_RED   = "#cf222e"
    C_AMBER = "#9a6700"
else:
    C_BG    = "#0d1117"
    C_CARD  = "#161b22"
    C_GRID  = "#21262d"
    C_TEXT  = "#c9d1d9"
    C_MUTED = "#8b949e"
    C_BLUE  = "#58a6ff"
    C_GREEN = "#3fb950"
    C_RED   = "#f85149"
    C_AMBER = "#d29922"

# ── Custom CSS (dynamic) ───────────────────────────────────────────────────────

st.markdown(f"""
<style>
    /* App background */
    .stApp {{ background-color: {C_BG}; color: {C_TEXT}; }}

    /* Metric cards */
    .metric-card {{
        background: {C_CARD};
        border: 1px solid {C_GRID};
        border-radius: 8px;
        padding: 14px 18px;
        text-align: center;
    }}
    .metric-label {{ font-size: 11px; color: {C_MUTED}; text-transform: uppercase; letter-spacing: 0.08em; }}
    .metric-value {{ font-size: 19px; font-weight: 700; color: {C_TEXT}; margin-top: 4px; word-break: break-word; }}
    .metric-value.green {{ color: {C_GREEN}; }}
    .metric-value.red   {{ color: {C_RED}; }}
    .metric-value.amber {{ color: {C_AMBER}; }}

    /* Status dots */
    .status-dot-green {{ display:inline-block; width:10px; height:10px; border-radius:50%; background:{C_GREEN}; margin-right:6px; }}
    .status-dot-red   {{ display:inline-block; width:10px; height:10px; border-radius:50%; background:{C_RED};   margin-right:6px; }}
    .status-dot-amber {{ display:inline-block; width:10px; height:10px; border-radius:50%; background:{C_AMBER}; margin-right:6px; }}

    /* Sidebar */
    div[data-testid="stSidebarContent"] {{ background-color: {C_BG}; }}

    /* ── Pill-button tab bar (Chrome-style) ── */

    /* Tab bar background strip */
    .stTabs [data-baseweb="tab-list"] {{
        background: {C_BG} !important;
        border-bottom: 1px solid {C_GRID} !important;
        gap: 6px !important;
        padding: 8px 12px !important;
        align-items: center !important;
    }}

    /* Kill the sliding underline highlight bar */
    .stTabs [data-baseweb="tab-highlight"],
    .stTabs [data-baseweb="tab-border"] {{
        display: none !important;
        height: 0 !important;
        background: transparent !important;
    }}

    /* Inactive button */
    .stTabs [data-baseweb="tab"] {{
        background: {C_CARD} !important;
        border: 1px solid {C_GRID} !important;
        border-radius: 8px !important;
        color: {C_MUTED} !important;
        font-weight: 500 !important;
        font-size: 13px !important;
        padding: 6px 14px !important;
        margin: 0 !important;
        line-height: 1.4 !important;
        transition: background 0.15s, color 0.15s, border-color 0.15s !important;
    }}

    /* Active button */
    .stTabs [data-baseweb="tab"][aria-selected="true"] {{
        background: {C_BLUE} !important;
        border-color: {C_BLUE} !important;
        color: #ffffff !important;
        font-weight: 600 !important;
    }}

    /* Tighten page top padding */
    .block-container {{ padding-top: 1rem; }}
    .metric-card {{ border-top: 3px solid {C_BLUE} !important; }}
    section[data-testid="stSidebar"] {{ border-right: 1px solid {C_GRID}; }}
    div[data-testid="stMetric"] {{ background: {C_CARD}; border-radius: 8px; padding: 10px 14px; border-top: 3px solid {C_BLUE}; }}
    .stButton button {{ border-radius: 8px; font-weight: 600; }}
    h1, h2, h3 {{ color: {C_TEXT}; }}
</style>
""", unsafe_allow_html=True)

# ── Helper functions ───────────────────────────────────────────────────────────

def load_yaml() -> dict:
    with open(BOT_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def save_yaml(data: dict) -> None:
    with open(BOT_DIR / "config.yaml", "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def read_trades() -> pd.DataFrame:
    path = BOT_DIR / "data" / "trades.csv"
    if path.exists() and path.stat().st_size > 10:
        return pd.read_csv(path)
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
    # Check subprocess started by the dashboard
    proc = st.session_state.get("bot_proc")
    if proc is not None and proc.poll() is None:
        return True
    # Fallback: check heartbeat file written by bot every tick.
    # This detects bots launched externally (terminal, osascript, etc.).
    hb_path = BOT_DIR / "bot_heartbeat.json"
    if hb_path.exists():
        try:
            data = json.loads(hb_path.read_text())
            age_seconds = time.time() - data.get("timestamp", 0)
            return age_seconds < 120  # alive if heartbeat < 2 minutes old
        except Exception:
            pass
    return False


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


def _load_best_genome() -> dict | None:
    """Load best_genome.yaml if it exists (saved by optimizer to data/optimizer/)."""
    path = BOT_DIR / "data" / "optimizer" / "best_genome.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)


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
        fillcolor=f"rgba(88,166,255,0.07)",
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

        # ── Bot status ─────────────────────────────────────────────────────────
        if kill_switch_active():
            st.markdown(f'<span class="status-dot-red"></span>**TRADING PAUSED**',
                        unsafe_allow_html=True)
            if st.button("▶ Resume Trading", use_container_width=True):
                clear_kill_switch()
                st.rerun()
        elif bot_running():
            st.markdown(f'<span class="status-dot-green"></span>**Bot Running**',
                        unsafe_allow_html=True)
        else:
            st.markdown(f'<span class="status-dot-amber"></span>**Bot Stopped**',
                        unsafe_allow_html=True)

        st.divider()

        # ── Account equity ─────────────────────────────────────────────────
        try:
            from core.trades import read_trades
            trades = read_trades()
            if trades:
                import pandas as _pd
                df = _pd.DataFrame(trades)
                if "equity" in df.columns:
                    latest_eq = df["equity"].iloc[-1]
                    start_eq  = df["equity"].iloc[0]
                    pnl_pct   = (latest_eq - start_eq) / start_eq * 100 if start_eq else 0
                    pnl_col   = "🟢" if pnl_pct >= 0 else "🔴"
                    st.markdown("**💰 Account Equity**")
                    st.markdown(
                        f'<div style="font-size:22px;font-weight:700;">${{latest_eq:,.2f}}</div>'
                        f'<div style="font-size:12px;color:#888;">{pnl_col} {{pnl_pct:+.2f}}% all-time</div>',
                        unsafe_allow_html=True,
                    )
        except Exception:
            pass

        # ── Theme toggle ───────────────────────────────────────────────────────
        st.markdown("**🎨 Theme**")
        chosen_theme = st.radio(
            "Theme",
            ["🌙 Dark", "☀️ Light"],
            index=0 if st.session_state.get("theme", "🌙 Dark") == "🌙 Dark" else 1,
            horizontal=True,
            label_visibility="collapsed",
        )
        if chosen_theme != st.session_state.get("theme", "🌙 Dark"):
            st.session_state["theme"] = chosen_theme
            st.rerun()
        if chosen_theme == "☀️ Light":
            st.markdown("""
<style>
    .stApp { background-color: #f5f5f5 !important; color: #1a1a1a !important; }
    section[data-testid="stSidebar"] { background-color: #e8e8e8 !important; border-right: 1px solid #cccccc; }
    .metric-card { background: #ffffff !important; border: 1px solid #dddddd !important; }
    .metric-label { color: #555555 !important; }
    .metric-value { color: #1a1a1a !important; }
    .stTabs [data-baseweb="tab"] { color: #555555 !important; }
    .stTabs [data-baseweb="tab"][aria-selected="true"] { color: #0066cc !important; border-bottom: 2px solid #0066cc !important; }
    .stTabs [data-baseweb="tab-list"] { background: #ffffff !important; border-bottom: 1px solid #dddddd; }
    div[data-testid="stMetric"] { background: #ffffff !important; border-top: 3px solid #0066cc !important; }
    h1, h2, h3 { color: #1a1a1a !important; }
    .stButton button { border-radius: 8px; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

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
        pcol1, pcol2, pcol3, pcol4 = st.columns(4)
        with pcol1:
            st.markdown("**Conservative**")
            st.caption("Δ15-20% OTM, monthly, very selective on IV. Fewer trades, lower risk.")
            if st.button("Load Conservative", use_container_width=True):
                st.session_state["preset"] = dict(
                    iv_rank_threshold=0.60, target_delta_min=0.15, target_delta_max=0.20,
                    min_dte=21, max_dte=35, max_equity_per_leg=0.80, min_free_equity_fraction=0.0,
                    lookback_months=18, starting_equity=10000,
                )
                st.rerun()
        with pcol2:
            st.markdown("**Balanced**")
            st.caption("Δ15-25% OTM, weekly, moderate IV filter. Good starting point.")
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
        with pcol4:
            st.markdown("**⚡ Optimised**")
            st.caption("Best genome from the genetic optimizer. Requires a completed evolution run.")
            best = _load_best_genome()
            if best:
                if st.button("Load Optimised", use_container_width=True, type="primary"):
                    st.session_state["preset"] = dict(
                        iv_rank_threshold=float(best.get("iv_rank_threshold", 0.60)),
                        target_delta_min=float(best.get("target_delta_min", 0.10)),
                        target_delta_max=float(best.get("target_delta_max", 0.20)),
                        min_dte=int(best.get("min_dte", 5)),
                        max_dte=int(best.get("max_dte", 14)),
                        max_equity_per_leg=float(best.get("max_equity_per_leg", 0.40)),
                        min_free_equity_fraction=float(best.get("min_free_equity_fraction", 0.0)),
                        lookback_months=18,
                        starting_equity=10000,
                    )
                    st.rerun()
            else:
                st.caption(
                    f"<small style='color:{C_AMBER}'>Run the Optimizer (Evolve mode) first to generate best_genome.yaml.</small>",
                    unsafe_allow_html=True,
                )
                st.button("Load Optimised", use_container_width=True, disabled=True)

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
        # Standardised Pause / Resume verbiage to match the mobile app.
        # Mechanism is unchanged (KILL_SWITCH file blocks new trade entries
        # but doesn't kill the process); only the user-facing label moved
        # from "Kill Switch" to "Pause Trading" so both surfaces talk about
        # the same concept the same way. See CONSISTENCY.md Pass A.4.
        if kill_switch_active():
            if st.button("▶ Resume Trading (all bots)",
                         type="primary", use_container_width=True):
                clear_kill_switch()
                st.rerun()
        elif bot_running():
            if st.button("⏸ Pause Trading (all bots)",
                         use_container_width=True,
                         help="Blocks new trade entries on every bot. Open positions still settle naturally. The bot process keeps running."):
                (BOT_DIR / "KILL_SWITCH").write_text(
                    f"Paused from dashboard at {datetime.utcnow().isoformat()}\n"
                    "Delete this file to resume trading."
                )
                st.rerun()

    with ctrl3:
        st.toggle("Auto-refresh (15s)", value=False, disabled=True,
                  help="Use the Refresh button below instead — auto-sleep was removed to prevent UI hangs.")

    st.divider()

    # ── Status header ─────────────────────────────────────────────────────────
    if kill_switch_active():
        st.error("⏸ **TRADING PAUSED** — New entries blocked across all bots. Click Resume to continue.")
    elif bot_running():
        start = st.session_state.get("bot_start_time")
        elapsed = ""
        if start:
            secs = int((datetime.utcnow() - start).total_seconds())
            elapsed = f" · running {secs // 60}m {secs % 60}s"
        # Read mode from heartbeat instead of hardcoding "paper mode"
        hb_path = BOT_DIR / "bot_heartbeat.json"
        mode_label = "paper mode"
        if hb_path.exists():
            try:
                _hb = json.loads(hb_path.read_text())
                mode_label = _hb.get("mode", "paper") + " mode"
            except Exception:
                pass
        st.success(f"✅ Bot is running in {mode_label}{elapsed}")
    else:
        st.warning("⚠️ Bot is not running — click **Start Paper Trading** above.")

    # ── Live status card (reads heartbeat) ────────────────────────────────────
    hb_path = BOT_DIR / "bot_heartbeat.json"
    if hb_path.exists():
        try:
            hb = json.loads(hb_path.read_text())
            hb_age = time.time() - hb.get("timestamp", 0)
            if hb_age < 120:
                st.markdown("#### 📡 Live Status")
                btc_price  = hb.get("btc_price", 0)
                equity_usd = hb.get("equity_usd", 0)
                iv_rank    = hb.get("iv_rank")
                mode_str   = hb.get("mode", "—").upper()
                pos_data   = hb.get("position")  # dict or None

                # ── Capital buffer calculations ───────────────────────────────
                # collateral_locked = strike × contracts (USD notional per contract)
                # free_capital = equity - collateral_locked
                # strike_gap = how far BTC must fall before the put goes ITM (%)
                if pos_data and btc_price > 0:
                    collateral_locked = pos_data.get("strike", 0) * pos_data.get("contracts", 0)
                    free_capital_usd  = equity_usd - collateral_locked
                    free_capital_pct  = (free_capital_usd / equity_usd * 100) if equity_usd > 0 else 0
                    strike_gap_pct    = ((btc_price - pos_data.get("strike", btc_price)) / btc_price * 100)
                    # Colours: tighter buffer = more alarming
                    free_col  = "red" if free_capital_pct < 15 else ("amber" if free_capital_pct < 30 else "green")
                    gap_col   = "red" if strike_gap_pct < 5  else ("amber" if strike_gap_pct < 10 else "green")
                else:
                    free_capital_usd = equity_usd
                    free_capital_pct = 100.0
                    strike_gap_pct   = None
                    free_col  = "green"
                    gap_col   = ""

                # ── Row 1: account-level metrics ─────────────────────────────
                lc1, lc2, lc3, lc4, lc5, lc6 = st.columns(6)
                with lc1:
                    metric_card("BTC Price", f"${btc_price:,.0f}")
                with lc2:
                    raw = load_yaml()
                    cfg_start = float(raw.get("backtest", {}).get("starting_equity", 10_000))
                    eq_col = "green" if equity_usd >= cfg_start else "red"
                    metric_card("Account Equity", f"${equity_usd:,.0f}", eq_col)
                with lc3:
                    metric_card(
                        "Free Capital",
                        f"{free_capital_pct:.1f}%<br>"
                        f"<span style='font-size:13px;font-weight:400;opacity:0.75'>"
                        f"${free_capital_usd:,.0f} free</span>",
                        free_col,
                    )
                with lc4:
                    if strike_gap_pct is not None:
                        _opt_type_g = pos_data.get("option_type", "put") if pos_data else "put"
                        if _opt_type_g == "put":
                            # Positive gap = BTC above strike (OTM) → safe buffer
                            _gap_disp = f"{strike_gap_pct:.1f}% buffer" if strike_gap_pct >= 0 else f"ITM {abs(strike_gap_pct):.1f}%"
                        else:
                            # For calls: negative gap means BTC below strike (OTM) → safe
                            _gap_disp = f"{abs(strike_gap_pct):.1f}% buffer" if strike_gap_pct <= 0 else f"ITM {strike_gap_pct:.1f}%"
                        metric_card("Strike Gap", _gap_disp, gap_col)
                    else:
                        metric_card("Strike Gap", "—")
                with lc5:
                    iv_str = f"{iv_rank:.0%}" if iv_rank is not None else "—"
                    iv_col = "amber" if iv_rank is not None and iv_rank > 0.85 else ""
                    metric_card("IV Rank", iv_str, iv_col)
                with lc6:
                    metric_card("Mode", mode_str)

                st.markdown("")

                # ── Row 2: position-level metrics ─────────────────────────────
                if pos_data:
                    delta   = pos_data.get("delta", 0)
                    dte     = pos_data.get("dte", 0)
                    upnl    = pos_data.get("unrealized_pnl_usd", 0)
                    upnl_col  = "green" if upnl >= 0 else "red"
                    delta_col = "red" if delta > 0.35 else ("amber" if delta > 0.28 else "")
                    dte_col   = "red" if dte <= 2 else ("amber" if dte <= 4 else "")

                    # Annualised return on collateral:
                    #   premium_usd = entry_price × contracts × btc_price
                    #   collateral_usd = strike × contracts  (1 BTC notional per contract)
                    #   yield = premium / collateral × (365 / dte_at_entry)
                    dte_at_entry = pos_data.get("dte_at_entry", 0)
                    if dte_at_entry > 0:
                        prem_usd = pos_data.get("entry_price", 0) * pos_data.get("contracts", 0) * btc_price
                        coll_usd = pos_data.get("strike", 1) * pos_data.get("contracts", 0)
                        ann_pct  = (prem_usd / coll_usd) * (365 / dte_at_entry) * 100 if coll_usd > 0 else 0
                        ann_str  = f"{ann_pct:.1f}%"
                        ann_col  = "green" if ann_pct >= 10 else ("amber" if ann_pct >= 5 else "")
                    else:
                        ann_str = "N/A"  # reconciled position — entry DTE unknown
                        ann_col = ""

                    pc1, pc2, pc3, pc4, pc5, pc6 = st.columns(6)
                    with pc1:
                        metric_card(
                            "Position",
                            f"<span style='font-size:13px;letter-spacing:-0.02em'>"
                            f"{pos_data.get('name', '—')}</span>",
                        )
                    with pc2:
                        metric_card("Type", pos_data.get("option_type", "—").upper())
                    with pc3:
                        metric_card("Delta", f"{delta:.3f}", delta_col)
                    with pc4:
                        metric_card("DTE", f"{dte}d", dte_col)
                    with pc5:
                        metric_card("Unrealised P&L", f"${upnl:+,.0f}", upnl_col)
                    with pc6:
                        metric_card("Ann. Return", ann_str, ann_col)

                    # ── Expiry proximity educational banner ───────────────────
                    if dte <= 4:
                        _exp_strike    = pos_data.get("strike", 0)
                        _exp_strike_k  = f"${_exp_strike:,.0f}"
                        _exp_entry_px  = pos_data.get("entry_price", 0)
                        _exp_contracts = pos_data.get("contracts", 0)
                        _exp_be        = round(_exp_strike - _exp_entry_px * _exp_contracts, 0) if _exp_contracts > 0 else 0
                        _be_line       = f"\n\n**Break-even:** ${_exp_be:,.0f} — BTC needs to stay above this for the trade to be profitable." if _exp_be > 0 else ""
                        _buf_pct       = ((btc_price - _exp_strike) / btc_price * 100) if btc_price > 0 and _exp_strike > 0 else 0
                        _buf_line      = f"BTC is currently **{_buf_pct:.1f}% above** the strike." if _buf_pct >= 0 else f"⚠️ BTC is **{abs(_buf_pct):.1f}% below** the strike — the option is in the money."
                        if dte <= 1:
                            st.error(
                                f"🚨 **Expiring today or tomorrow** — {_buf_line}\n\n"
                                f"**If BTC stays above {_exp_strike_k} at expiry:** option expires worthless → full premium kept ✅\n\n"
                                f"**If BTC is below {_exp_strike_k} at expiry:** assignment — bot buys BTC at {_exp_strike_k} (loss partially offset by premium) ❌"
                                f"{_be_line}\n\n"
                                f"*The bot handles this automatically. No action needed unless you want to close early.*"
                            )
                        else:
                            st.warning(
                                f"⏰ **{dte} days to expiry** — {_buf_line}\n\n"
                                f"**Win scenario:** BTC stays above {_exp_strike_k} → option expires worthless, full premium profit ✅\n\n"
                                f"**Loss scenario:** BTC falls below {_exp_strike_k} → assignment (bot buys BTC at strike) ❌"
                                f"{_be_line}\n\n"
                                f"*No action required. The bot monitors delta and loss thresholds and will roll or close if needed.*"
                            )

                else:
                    st.info("📭 No open position — bot is flat, watching for signals.")

                wheel = hb.get("wheel", "")
                st.caption(
                    f"Heartbeat {int(hb_age)}s ago · PID {hb.get('pid', '?')} · Wheel: {wheel}"
                )

                # ── Black Swan Stress Test ────────────────────────────────────
                if pos_data:
                    with st.expander("⚡ Black Swan Stress Test", expanded=False):
                        _strike    = pos_data.get("strike", 0)
                        _contracts = pos_data.get("contracts", 0)
                        _entry_px  = pos_data.get("entry_price", 0)
                        _opt_type  = pos_data.get("option_type", "put")

                        # Premium received in USD (entry_price BTC × contracts × current spot)
                        _premium_usd = _entry_px * _contracts * btc_price

                        # Margin safety: equity / max theoretical loss
                        # Short put max loss (USD) = strike × contracts (if BTC → $0)
                        # Short call max loss is unbounded; use 10× spike as practical ceiling
                        if _opt_type == "put":
                            _max_loss_usd = _strike * _contracts
                            _scenario_label = "BTC crash scenarios (put risk)"
                            _moves  = [-0.05, -0.10, -0.20, -0.30, -0.50, -0.70, -1.00]
                            _labels = ["-5%", "-10%", "-20%", "-30%", "-50%", "-70%", "→ $0"]
                        else:  # call
                            _max_loss_usd = max(0, btc_price * 10 - _strike) * _contracts
                            _scenario_label = "BTC spike scenarios (call risk)"
                            _moves  = [+0.10, +0.20, +0.50, +1.00, +2.00, +5.00]
                            _labels = ["+10%", "+20%", "+50%", "+100%", "+200%", "+500%"]

                        _margin_safety = equity_usd / _max_loss_usd if _max_loss_usd > 0 else float("inf")
                        _ms_hex = C_GREEN if _margin_safety >= 2 else (C_AMBER if _margin_safety >= 1.2 else C_RED)

                        # Margin safety summary banner
                        _ms_inf = _margin_safety == float("inf")
                        _ms_val = "∞" if _ms_inf else f"{_margin_safety:.1f}×"
                        _ms_detail = (
                            f"Max loss if BTC → $0: ${_max_loss_usd:,.0f}" if _opt_type == "put"
                            else f"Practical ceiling (10× BTC spike): ${_max_loss_usd:,.0f}"
                        )
                        st.markdown(
                            f'<div style="padding:10px 16px;background:{C_CARD};border:1px solid {C_GRID};'
                            f'border-radius:6px;margin-bottom:12px;display:flex;align-items:baseline;gap:10px;">'
                            f'<span style="color:{C_MUTED};font-size:12px;">Margin Safety:</span>'
                            f'<span style="color:{_ms_hex};font-size:22px;font-weight:700;">{_ms_val}</span>'
                            f'<span style="color:{C_MUTED};font-size:12px;">'
                            f'equity covers theoretical max loss {_ms_val} &nbsp;·&nbsp; {_ms_detail}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                        # Scenario table
                        st.markdown(
                            f'<div style="color:{C_MUTED};font-size:12px;margin-bottom:6px;">'
                            f'📊 {_scenario_label}</div>',
                            unsafe_allow_html=True,
                        )
                        _rows = []
                        for _lbl, _move in zip(_labels, _moves):
                            _s_price = max(1.0, btc_price * (1.0 + _move))
                            if _opt_type == "put":
                                _intrinsic_usd = max(0.0, _strike - _s_price) * _contracts
                            else:
                                _intrinsic_usd = max(0.0, _s_price - _strike) * _contracts
                            _pnl_usd      = _premium_usd - _intrinsic_usd
                            _eq_after     = equity_usd + _pnl_usd
                            _loss_pct     = (_intrinsic_usd - _premium_usd) / equity_usd * 100
                            _loss_pct     = max(0.0, _loss_pct)   # positive = loss

                            if _eq_after <= 0:
                                _status = "❌ Liquidated"
                            elif _loss_pct > 30:
                                _status = "🔴 Critical"
                            elif _loss_pct > 10:
                                _status = "🟡 Warning"
                            else:
                                _status = "🟢 Safe"

                            _rows.append({
                                "Move":         _lbl,
                                "BTC Price":    f"${_s_price:,.0f}",
                                "Est. P&L":     f"${_pnl_usd:+,.0f}",
                                "Equity After": f"${_eq_after:,.0f}",
                                "Acct. Loss":   f"{_loss_pct:.1f}%",
                                "Status":       _status,
                            })

                        st.dataframe(
                            pd.DataFrame(_rows),
                            hide_index=True,
                            use_container_width=True,
                        )
                        st.caption(
                            "Estimates use intrinsic value only (no time value). "
                            "Real losses at intermediate DTEs will be smaller due to remaining theta. "
                            "Kill switch + drawdown checks monitor live for automatic halt."
                        )

                st.divider()
        except Exception:
            pass  # never let a bad heartbeat crash the tab

    # ── Trade data ────────────────────────────────────────────────────────────
    trades_df = read_trades()

    if trades_df.empty:
        st.info("No trades recorded yet. The bot will record trades to `data/trades.csv` as they complete.")
        _render_log_tail()
    else:
        raw = load_yaml()
        start_eq = float(raw.get("backtest", {}).get("starting_equity", 10000))
        end_eq   = float(trades_df["equity_after"].iloc[-1]) if "equity_after" in trades_df.columns else start_eq
        wins     = (trades_df["pnl_usd"] >= 0).sum() if "pnl_usd" in trades_df.columns else 0
        total    = len(trades_df)
        win_rate = wins / total * 100 if total else 0
        total_pnl = trades_df["pnl_usd"].sum() if "pnl_usd" in trades_df.columns else 0

        c1, c2, c3, c4 = st.columns(4)
        with c1: metric_card("Equity at Last Close", f"${end_eq:,.0f}", "green" if end_eq >= start_eq else "red")
        with c2: metric_card("Total Realised P&L", f"${total_pnl:+,.0f}", "green" if total_pnl >= 0 else "red")
        with c3: metric_card("Win Rate", f"{win_rate:.0f}%", "green" if win_rate >= 60 else "amber")
        with c4: metric_card("Closed Trades", str(total))

        st.markdown("")

        if "equity_after" in trades_df.columns:
            eq_vals = [start_eq] + list(trades_df["equity_after"].values)
            x_vals  = list(range(len(eq_vals)))
            st.plotly_chart(
                make_equity_chart(x_vals, eq_vals, start_eq, "Paper Trading — Equity Curve"),
                use_container_width=True,
            )
            st.plotly_chart(make_drawdown_chart(x_vals, eq_vals), use_container_width=True)

        if "pnl_usd" in trades_df.columns:
            st.plotly_chart(make_pnl_bar(trades_df), use_container_width=True)

        st.markdown("#### Recent Trades")
        show = ["timestamp", "instrument", "option_type", "strike",
                "pnl_usd", "equity_before", "equity_after",
                "dte_at_close", "reason", "mode"]
        show = [c for c in show if c in trades_df.columns]
        st.dataframe(trades_df[show].tail(20), use_container_width=True, height=260)

        ov_df = read_overseer_log()
        if not ov_df.empty:
            st.markdown("#### AI Overseer Decisions")
            st.dataframe(ov_df.tail(10)[["timestamp_utc", "decision", "confidence", "reasoning"]],
                         use_container_width=True, height=200)

        _render_log_tail()

    # ── Refresh controls ──────────────────────────────────────────────────────
    col_ref, col_ts = st.columns([1, 3])
    with col_ref:
        if st.button("🔄 Refresh", key="paper_refresh"):
            st.rerun()
    with col_ts:
        st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")


def _render_log_tail(n: int = 40, key: str = "log_main") -> None:
    """Show the last N lines of the most recent log file."""
    log_dir = BOT_DIR / "logs"
    logs = (
        sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if log_dir.exists() else []
    )
    if logs:
        with st.expander(f"📋 Live Log (last {n} lines — newest first)", expanded=False):
            try:
                lines = logs[0].read_text().splitlines()[-n:]
                st.code("\n".join(reversed(lines)), language=None)
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
                    horizontal=True, key="optimizer_mode")
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
            sweep_param = st.selectbox("Parameter to sweep", param_choices,
                                       key="optimizer_sweep_param")
        else:
            st.markdown("#### Evolution Settings")
            pop_size    = st.slider("Population size",  min_value=8,  max_value=50, step=4, value=20, key="optimizer_pop_size")
            generations = st.slider("Generations",      min_value=3,  max_value=20, step=1, value=8,  key="optimizer_generations")
            elite_keep  = st.slider("Elite survivors",  min_value=2,  max_value=10, step=1, value=4,  key="optimizer_elite_keep")
            mut_rate    = st.slider("Mutation rate",    min_value=0.1, max_value=0.6, step=0.05, value=0.3, key="optimizer_mut_rate")
            _sweep_results_exist = (OPT_DIR / "sweep_results.json").exists()
            seed_from_sweep = st.checkbox(
                "🌱 Seed initial population from sweep results",
                value=True,
                help="Uses sweep's best-per-parameter values as starting genes for 30% of generation 0. Much faster convergence.",
                key="optimizer_seed_from_sweep",
                disabled=not _sweep_results_exist,
            )
            if not _sweep_results_exist:
                st.caption("Run a sweep first to enable seeding.")

        opt_running = (
            st.session_state.get("opt_proc") is not None
            and st.session_state.get("opt_proc").poll() is None
        )

        if opt_running:
            st.warning("Optimizer is running…")
            if st.button("⏹ Stop Optimizer", use_container_width=True, key="optimizer_stop_btn"):
                p = st.session_state.get("opt_proc")
                if p:
                    p.terminate()
                st.session_state["opt_proc"] = None
                st.rerun()
        else:
            if st.button("▶ Start Optimizer", type="primary", use_container_width=True, key="optimizer_start_btn"):
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
                    if seed_from_sweep and _sweep_results_exist:
                        cmd += ["--seed-from-sweep"]
                try:
                    proc = subprocess.Popen(
                        cmd, cwd=str(BOT_DIR),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    )
                    st.session_state["opt_proc"] = proc
                    st.session_state["opt_start"] = datetime.utcnow()
                    st.rerun()
                except Exception as _exc:
                    st.error(f"Failed to start optimizer: {_exc}\nCmd: {' '.join(cmd)}")

        if opt_running:
            st.markdown("")
            start = st.session_state.get("opt_start")
            if start:
                secs = int((datetime.utcnow() - start).total_seconds())
                st.caption(f"Running for {secs // 60}m {secs % 60}s")
            if st.button("🔄 Refresh Results", use_container_width=True, key="optimizer_refresh_btn"):
                st.rerun()

    with col_res:
        _render_optimizer_results(is_sweep)

        if opt_running:
            time.sleep(10)
            st.rerun()


OPT_DIR = BOT_DIR / "data" / "optimizer"


def _render_optimizer_results(is_sweep: bool) -> None:
    """Display optimizer outputs when available."""
    if is_sweep:
        sweep_results_path = OPT_DIR / "sweep_results.json"
        if not sweep_results_path.exists():
            st.info("🔄 Sweep Sensitivity Chart will appear here once the run completes.")
            return

        try:
            with open(sweep_results_path) as _f:
                _sweep_data = json.load(_f)
        except Exception as _e:
            st.warning(f"Could not read sweep_results.json: {_e}")
            return

        if not _sweep_data:
            st.info("No sweep results yet.")
            return

        # ── Interactive per-parameter charts (one tab per param) ──────────
        st.markdown("#### 📈 Sweep Sensitivity Chart")
        st.caption("Hover over points to see exact values. The green dashed line marks the best value.")
        _params_done = list(_sweep_data.keys())
        if _params_done:
            _tabs = st.tabs(_params_done)
            for _tab, _pname in zip(_tabs, _params_done):
                with _tab:
                    _valid = [r for r in _sweep_data[_pname] if not r.get("error")]
                    if not _valid:
                        st.warning("No valid results for this parameter.")
                        continue
                    _xs = [r["params"][_pname] for r in _valid]
                    _ys = [r["fitness"] for r in _valid]
                    _best_idx = int(max(range(len(_ys)), key=lambda i: _ys[i]))
                    _best_x   = _xs[_best_idx]
                    _best_y   = _ys[_best_idx]

                    _fig = go.Figure()
                    _fig.add_trace(go.Scatter(
                        x=_xs, y=_ys, mode="lines+markers",
                        marker=dict(size=7, color="#58a6ff"),
                        line=dict(color="#58a6ff", width=2),
                        customdata=[[r["win_rate_pct"], r["total_return_pct"],
                                     r["sharpe_ratio"], r["num_cycles"]]
                                    for r in _valid],
                        hovertemplate=(
                            f"<b>{_pname}</b>: %{{x}}<br>"
                            "Fitness: %{y:.4f}<br>"
                            "Win rate: %{customdata[0]:.1f}%<br>"
                            "Return: %{customdata[1]:+.1f}%<br>"
                            "Sharpe: %{customdata[2]:.2f}<br>"
                            "Trades: %{customdata[3]}<extra></extra>"
                        ),
                        name="Fitness",
                    ))
                    _fig.add_vline(x=_best_x, line_dash="dash",
                                   line_color="#3fb950", annotation_text=f"best={_best_x}",
                                   annotation_font_color="#3fb950")
                    _fig.update_layout(
                        **_dark_layout(f"{_pname} — best={_best_x}  fitness={_best_y:.4f}", height=280),
                        xaxis_title=_pname, yaxis_title="Fitness Score",
                    )
                    st.plotly_chart(_fig, use_container_width=True,
                                    key=f"sweep_chart_{_pname}")

                    # Metrics row for this param
                    _mc1, _mc2, _mc3, _mc4 = st.columns(4)
                    _best_r = _valid[_best_idx]
                    _mc1.metric("Best Value",   f"{_best_x}")
                    _mc2.metric("Fitness",      f"{_best_y:.4f}")
                    _mc3.metric("Win Rate",     f"{_best_r['win_rate_pct']:.1f}%")
                    _mc4.metric("Return",       f"{_best_r['total_return_pct']:+.1f}%")

        # ── Summary table ──────────────────────────────────────────────────
        _table_rows = []
        for _pname, _presults in _sweep_data.items():
            _valid = [r for r in _presults if not r.get("error")]
            if _valid:
                _best = max(_valid, key=lambda r: r["fitness"])
                _table_rows.append({
                    "Parameter":       _pname,
                    "Best Value":      _best["params"][_pname],
                    "Fitness":         round(_best["fitness"], 4),
                    "Win Rate %":      round(_best["win_rate_pct"], 1),
                    "Return %":        round(_best["total_return_pct"], 1),
                    "Sharpe":          round(_best["sharpe_ratio"], 2),
                    "Max Drawdown %":  round(_best["max_drawdown_pct"], 1),
                    "Trades":          int(_best["num_cycles"]),
                })
        if _table_rows:
            st.markdown("#### 🗂️ Best Value per Parameter")
            st.caption("Each row shows the single value that maximised fitness for that parameter. Use these as a starting point for the Evolve run.")
            _summary_df = pd.DataFrame(_table_rows)
            st.dataframe(_summary_df, use_container_width=True,
                         height=min(60 + 35 * len(_table_rows), 420),
                         key="sweep_best_table")

            # Download button for sweep data
            _csv_buf = _summary_df.to_csv(index=False).encode()
            st.download_button(
                "⬇️ Download sweep summary (CSV)",
                data=_csv_buf,
                file_name="sweep_summary.csv",
                mime="text/csv",
                key="sweep_dl_btn",
            )

    else:
        leaderboard_path = OPT_DIR / "evolution_leaderboard.csv"
        best_path        = OPT_DIR / "best_genome.yaml"

        if not best_path.exists() and not leaderboard_path.exists():
            st.info("🔄 Evolution results will appear here once the run completes.")
            return

        # ── Best genome ────────────────────────────────────────────────────
        if best_path.exists():
            with open(best_path) as f:
                best = yaml.safe_load(f)
            st.markdown("#### 🏆 Best Genome Found")
            _bg_col1, _bg_col2 = st.columns([2, 1])
            with _bg_col1:
                best_df = pd.DataFrame(
                    [(k, str(round(v, 6)) if isinstance(v, float) else str(v))
                     for k, v in best.items()],
                    columns=["Parameter", "Optimal Value"]
                )
                st.dataframe(best_df, use_container_width=True,
                             height=min(60 + 35 * len(best), 340),
                             key="evo_best_genome_table")
            with _bg_col2:
                st.markdown("**Apply to Bot Config**")
                st.caption("This replaces the strategy parameters in config.yaml with the optimised values.")
                if st.button("⚙️ Apply to Config", use_container_width=True,
                             type="primary", key="optimizer_apply_genome_btn"):
                    _apply_genome_to_config(best)
                    st.success("✅ Applied! Restart the bot to use new parameters.")
                # Download YAML
                _yaml_str = "\n".join(f"{k}: {v}" for k, v in best.items())
                st.download_button(
                    "⬇️ Download best_genome.yaml",
                    data=_yaml_str,
                    file_name="best_genome.yaml",
                    mime="text/plain",
                    key="evo_dl_genome_btn",
                )

        # ── Leaderboard ────────────────────────────────────────────────────
        if leaderboard_path.exists():
            lb = pd.read_csv(leaderboard_path)
            # Show fitness + key metrics, drop raw param columns for readability
            _metric_cols = ["fitness", "win_rate_pct", "total_return_pct",
                            "sharpe_ratio", "max_drawdown_pct", "num_cycles"]
            _disp_cols = [c for c in _metric_cols if c in lb.columns]
            _param_cols = [c for c in lb.columns if c not in _disp_cols + ["bot_id", "error"]]

            st.markdown("#### 📊 Evolution Leaderboard")
            st.caption(f"{len(lb)} genome evaluations across all generations — sorted by fitness.")
            _lb_sorted = lb.sort_values("fitness", ascending=False).reset_index(drop=True)

            # Metrics display
            if len(_lb_sorted) > 0:
                _top = _lb_sorted.iloc[0]
                _m1, _m2, _m3, _m4 = st.columns(4)
                _m1.metric("Best Fitness",  f"{_top.get('fitness', 0):.4f}")
                _m2.metric("Win Rate",      f"{_top.get('win_rate_pct', 0):.1f}%")
                _m3.metric("Return",        f"{_top.get('total_return_pct', 0):+.1f}%")
                _m4.metric("Sharpe",        f"{_top.get('sharpe_ratio', 0):.2f}")

            # Full table — show metrics + params only, format floats as strings
            # to avoid Streamlit's heatmap colouring on numeric columns
            _show_cols = _disp_cols + _param_cols  # metrics first, then params
            _lb_display = _lb_sorted[_show_cols].head(20).copy()
            _fmt = {
                "fitness": "{:.4f}", "win_rate_pct": "{:.1f}%",
                "total_return_pct": "{:+.1f}%", "sharpe_ratio": "{:.2f}",
                "max_drawdown_pct": "{:+.1f}%", "num_cycles": "{:.0f}",
            }
            for _c, _f in _fmt.items():
                if _c in _lb_display.columns:
                    _lb_display[_c] = _lb_display[_c].apply(lambda v: _f.format(v))
            # Round param floats to 4dp
            for _c in _param_cols:
                if _lb_display[_c].dtype == float:
                    _lb_display[_c] = _lb_display[_c].apply(lambda v: f"{v:.4f}")
            st.dataframe(_lb_display, use_container_width=True,
                         height=320, key="evo_leaderboard_table")
            st.download_button(
                "⬇️ Download full leaderboard (CSV)",
                data=lb.to_csv(index=False).encode(),
                file_name="evolution_leaderboard.csv",
                mime="text/csv",
                key="evo_dl_leaderboard_btn",
            )

        # ── Evolution progress chart ───────────────────────────────────────
        evo_img = OPT_DIR / "evolution_progress.png"
        if evo_img.exists():
            st.markdown("#### 📈 Evolution Progress")
            st.image(str(evo_img), use_container_width=True)


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
        "min_free_equity_fraction": ("sizing",   "min_free_equity_fraction"),
        "approx_otm_offset":        ("backtest", "approx_otm_offset"),
        "premium_fraction_of_spot": ("backtest", "premium_fraction_of_spot"),
        "starting_equity":          ("backtest", "starting_equity"),
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
            "Collateral Buffer", value=float(sz.get("collateral_buffer", 1.00)),
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
# TAB 5 — RECOMMENDATIONS
# ══════════════════════════════════════════════════════════════════════════════

def tab_recommendations() -> None:
    st.markdown("### 📋 Recommendations")

    EXPERIENCE_PATH = BOT_DIR / "data" / "experience.jsonl"

    # ── Section 1: Experience Intelligence ────────────────────────────────────
    st.markdown("#### 🧠 Experience Intelligence")
    st.caption("Real trade results accumulated by the bot. The more trades recorded, the more the optimizer calibrates to actual market conditions rather than pure backtests.")

    try:
        from optimizer import summarise_experience as _summarise_exp
        _exp_summary = _summarise_exp(EXPERIENCE_PATH)
    except Exception:
        _exp_summary = {"total_trades": 0, "calibration_level": "none"}

    _n_trades   = _exp_summary.get("total_trades", 0)
    _cal_level  = _exp_summary.get("calibration_level", "none")
    _cal_badge  = {"none": "🔴 None", "low": "🟡 Low (5-14)", "medium": "🟢 Medium (15-29)", "high": "💎 High (30+)"}.get(_cal_level, "🔴 None")
    _exp_c1, _exp_c2, _exp_c3, _exp_c4 = st.columns(4)
    with _exp_c1:
        metric_card("Trades Learned", str(_n_trades))
    with _exp_c2:
        _wr_val = _exp_summary.get("win_rate", 0)
        _wr_str = f"{_wr_val * 100:.1f}%" if _n_trades > 0 else "—"
        _wr_col = "green" if _n_trades > 0 and _wr_val >= 0.6 else ("amber" if _n_trades > 0 else "")
        metric_card("Actual Win Rate", _wr_str, _wr_col)
    with _exp_c3:
        _pnl_val = _exp_summary.get("avg_pnl_usd", 0)
        _pnl_str = f"${_pnl_val:+,.0f}" if _n_trades > 0 else "—"
        _pnl_col = "green" if _n_trades > 0 and _pnl_val >= 0 else ("red" if _n_trades > 0 else "")
        metric_card("Avg P&L / Trade", _pnl_str, _pnl_col)
    with _exp_c4:
        metric_card("Calibration", _cal_badge)

    if _n_trades == 0:
        st.info("No live/paper trades recorded yet. Once the bot closes its first trade, experience data will accumulate here and the optimizer will automatically blend it into future runs.")
    elif _n_trades < 5:
        st.warning(f"Only {_n_trades} trade(s) so far — calibration activates at 5. Keep the bot running.")
    else:
        st.success(f"✅ Calibration active — optimizer is blending {_n_trades} real trades with backtest data. Experience weight: {'20%' if _n_trades < 10 else '40%' if _n_trades < 20 else '50%' if _n_trades < 30 else '70%'}.")

    # Recalibration banner
    if _n_trades >= 5:
        _genome_path = OPT_DIR / "best_genome.yaml"
        _exp_mtime   = EXPERIENCE_PATH.stat().st_mtime if EXPERIENCE_PATH.exists() else 0
        _genome_mtime = _genome_path.stat().st_mtime if _genome_path.exists() else 0
        if _exp_mtime > _genome_mtime:
            st.warning("⚡ New experience data recorded since your last optimizer run. Re-run **Evolve** (Optimizer tab, with 'Seed from Sweep' on) to incorporate real trade results into parameter selection.")

    st.divider()

    # ── Section 2: Backtest vs Reality (shown once 3+ trades exist) ───────────
    if _n_trades >= 3:
        st.markdown("#### 📊 Backtest Prediction vs 📈 Actual Results")
        st.caption("Where the backtest model is accurate — and where real trading diverges. Gaps narrow as experience grows.")

        _exp_records: list[dict] = []
        try:
            with open(EXPERIENCE_PATH) as _ef:
                for _line in _ef:
                    _line = _line.strip()
                    if _line:
                        _exp_records.append(json.loads(_line))
        except Exception:
            pass

        from collections import defaultdict as _dd
        _iv_actual: dict = _dd(list)
        for _r in _exp_records:
            _iv = _r.get("params", {}).get("iv_rank_threshold")
            _win = _r.get("outcome", {}).get("win", False)
            _pnl = _r.get("outcome", {}).get("pnl_pct", 0)
            if _iv is not None:
                _lbl = f">= {int(round(_iv * 100))}%"
                _iv_actual[_lbl].append({"win": _win, "pnl_pct": _pnl})

        _bt_iv_rows = [
            {"label": "IV rank >= 20%",  "sharpe":  0.19, "total_return":  8.52, "win_rate": 60.0},
            {"label": "IV rank >= 30%",  "sharpe":  0.19, "total_return":  8.52, "win_rate": 60.0},
            {"label": "IV rank >= 40%",  "sharpe": -0.70, "total_return": -3.67, "win_rate": 33.3},
            {"label": "IV rank >= 50%",  "sharpe": -0.70, "total_return": -3.67, "win_rate": 33.3},
            {"label": "IV rank >= 60%",  "sharpe":  1.40, "total_return": 53.24, "win_rate": 80.0},
            {"label": "IV rank >= 70%",  "sharpe":  1.29, "total_return": 51.08, "win_rate": 58.3},
        ]
        _compare_rows = []
        for _bt in _bt_iv_rows:
            _lbl = _bt["label"].replace("IV rank ", "")
            _actual = _iv_actual.get(_bt["label"].replace("IV rank ", "").strip())
            if _actual and len(_actual) >= 2:
                _awr  = f"{sum(1 for t in _actual if t['win']) / len(_actual) * 100:.0f}%"
                _aret = f"{sum(t['pnl_pct'] for t in _actual) / len(_actual) * 100:+.2f}%"
                _an   = str(len(_actual))
            else:
                _awr, _aret, _an = "—", "—", "0"
            _compare_rows.append({
                "IV Threshold": _lbl,
                "BT Win Rate":  f"{_bt['win_rate']:.0f}%",
                "BT Return":    f"{_bt['total_return']:+.1f}%",
                "BT Sharpe":    f"{_bt['sharpe']:.2f}",
                "Live Win Rate": _awr,
                "Live Return":   _aret,
                "Live Trades":   _an,
            })
        if _compare_rows:
            st.dataframe(pd.DataFrame(_compare_rows), use_container_width=True,
                         hide_index=True, key="reco_bt_vs_live_table")
        st.divider()

    # ── Section 3: Optimizer Best Genome ─────────────────────────────────────
    _best_genome_path = OPT_DIR / "best_genome.yaml"
    if _best_genome_path.exists():
        try:
            with open(_best_genome_path) as _f:
                _best_genome = yaml.safe_load(_f)

            # Pull top-row metrics from leaderboard CSV if available
            _leaderboard_path = OPT_DIR / "evolution_leaderboard.csv"
            _top_metrics: dict = {}
            if _leaderboard_path.exists():
                try:
                    _lb = pd.read_csv(_leaderboard_path)
                    if not _lb.empty:
                        _top = _lb.iloc[0]
                        _top_metrics = {
                            "fitness": round(float(_top.get("fitness", 0)), 3),
                            "total_return_pct": round(float(_top.get("total_return_pct", 0)), 1),
                            "sharpe_ratio": round(float(_top.get("sharpe_ratio", 0)), 2),
                            "max_drawdown_pct": round(float(_top.get("max_drawdown_pct", 0)), 1),
                            "win_rate_pct": round(float(_top.get("win_rate_pct", 0)), 1),
                        }
                except Exception:
                    pass

            st.markdown("#### 🏆 Optimizer Best Genome")
            st.caption("These are live results from your optimizer run — more reliable than the static baseline below.")

            if _top_metrics:
                _m1, _m2, _m3, _m4 = st.columns(4)
                _m1.metric("Fitness", f"{_top_metrics['fitness']:.3f}")
                _m2.metric("Return", f"{_top_metrics['total_return_pct']:+.1f}%")
                _m3.metric("Sharpe", f"{_top_metrics['sharpe_ratio']:.2f}")
                _m4.metric("Win Rate", f"{_top_metrics['win_rate_pct']:.1f}%")

            _genome_df = pd.DataFrame(
                list(_best_genome.items()), columns=["Parameter", "Optimal Value"]
            )
            st.dataframe(_genome_df, use_container_width=True,
                         height=min(60 + 35 * len(_best_genome), 360),
                         key="reco_best_genome_table")

            if st.button("✅ Apply Best Genome to Config", type="primary",
                         use_container_width=True, key="reco_apply_genome_btn"):
                _apply_genome_to_config(_best_genome)
                st.success("Best genome applied to config.yaml! Re-run a backtest to verify.")

            st.divider()
        except Exception as _e:
            st.warning(f"Could not load best_genome.yaml: {_e}")

    # ── Section 4: Historical Baseline (Static) ───────────────────────────────
    st.markdown("#### 📋 Historical Baseline Analysis (Static)")
    st.caption("Results from 36 backtests across 7 parameter groups from an earlier manual sweep. Used as a starting reference — live experience data above supersedes this over time.")

    BACKTEST_RESULTS = [
        {"label": "IV rank >= 20%",   "group": "iv_rank_threshold", "sharpe":  0.19, "total_return":  8.52, "max_dd": -11.75, "win_rate": 60.0, "trades": 5,  "avg_yield": 0.86},
        {"label": "IV rank >= 30%",   "group": "iv_rank_threshold", "sharpe":  0.19, "total_return":  8.52, "max_dd": -11.75, "win_rate": 60.0, "trades": 5,  "avg_yield": 0.86},
        {"label": "IV rank >= 40%",   "group": "iv_rank_threshold", "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "IV rank >= 50%",   "group": "iv_rank_threshold", "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "IV rank >= 60%",   "group": "iv_rank_threshold", "sharpe":  1.40, "total_return": 53.24, "max_dd": -10.21, "win_rate": 80.0, "trades": 10, "avg_yield": 0.93},
        {"label": "IV rank >= 70%",   "group": "iv_rank_threshold", "sharpe":  1.29, "total_return": 51.08, "max_dd":  -7.56, "win_rate": 58.3, "trades": 12, "avg_yield": 1.06},
        {"label": "Delta 10-15%",     "group": "delta",             "sharpe": -0.07, "total_return": -1.29, "max_dd": -25.26, "win_rate": 69.2, "trades": 13, "avg_yield": 0.50},
        {"label": "Delta 15-20%",     "group": "delta",             "sharpe": -0.26, "total_return": -3.47, "max_dd": -17.94, "win_rate": 50.0, "trades": 10, "avg_yield": 0.75},
        {"label": "Delta 15-25%",     "group": "delta",             "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Delta 20-30%",     "group": "delta",             "sharpe": -1.56, "total_return":-14.13, "max_dd": -14.49, "win_rate": 33.3, "trades": 3,  "avg_yield": 1.16},
        {"label": "Delta 25-35%",     "group": "delta",             "sharpe": -1.36, "total_return":-13.30, "max_dd": -13.30, "win_rate":  0.0, "trades": 2,  "avg_yield": 1.42},
        {"label": "Delta 30-40%",     "group": "delta",             "sharpe": -1.36, "total_return":-18.35, "max_dd": -18.35, "win_rate":  0.0, "trades": 2,  "avg_yield": 1.75},
        {"label": "Weekly (7 DTE)",   "group": "dte",               "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Monthly (28 DTE)", "group": "dte",               "sharpe": -1.64, "total_return":-13.99, "max_dd": -13.99, "win_rate":  0.0, "trades": 2,  "avg_yield": 1.66},
        {"label": "Max 40%/leg",      "group": "equity_per_leg",    "sharpe": -0.53, "total_return":-10.54, "max_dd": -17.44, "win_rate": 53.3, "trades": 15, "avg_yield": 0.90},
        {"label": "Max 50%/leg",      "group": "equity_per_leg",    "sharpe": -1.38, "total_return": -6.94, "max_dd": -11.07, "win_rate": 20.0, "trades": 5,  "avg_yield": 0.88},
        {"label": "Max 60%/leg",      "group": "equity_per_leg",    "sharpe": -1.19, "total_return": -6.49, "max_dd": -11.43, "win_rate": 20.0, "trades": 5,  "avg_yield": 0.88},
        {"label": "Max 70%/leg",      "group": "equity_per_leg",    "sharpe": -0.94, "total_return": -5.32, "max_dd": -11.10, "win_rate": 25.0, "trades": 4,  "avg_yield": 0.85},
        {"label": "Max 80%/leg",      "group": "equity_per_leg",    "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Max 90%/leg",      "group": "equity_per_leg",    "sharpe": -0.65, "total_return": -4.13, "max_dd": -11.53, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Max 100%/leg",     "group": "equity_per_leg",    "sharpe": -0.61, "total_return": -4.59, "max_dd": -12.71, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Buffer 0%",        "group": "free_margin",       "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Buffer 10%",       "group": "free_margin",       "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Buffer 20%",       "group": "free_margin",       "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Buffer 30%",       "group": "free_margin",       "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Start $10,000",    "group": "starting_equity",   "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Start $25,000",    "group": "starting_equity",   "sharpe":  0.01, "total_return":  5.16, "max_dd": -10.15, "win_rate": 76.9, "trades": 13, "avg_yield": 0.90},
        {"label": "Start $50,000",    "group": "starting_equity",   "sharpe": -0.02, "total_return":  4.57, "max_dd": -10.20, "win_rate": 76.9, "trades": 13, "avg_yield": 0.90},
        {"label": "Start $100,000",   "group": "starting_equity",   "sharpe":  0.01, "total_return":  5.08, "max_dd": -10.16, "win_rate": 76.9, "trades": 13, "avg_yield": 0.90},
        {"label": "Conservative $10k",  "group": "combo", "sharpe": -1.05, "total_return":-21.56, "max_dd": -21.56, "win_rate":  0.0, "trades": 1,  "avg_yield": 1.81},
        {"label": "Balanced $10k",       "group": "combo", "sharpe": -0.70, "total_return": -3.67, "max_dd": -10.34, "win_rate": 33.3, "trades": 3,  "avg_yield": 0.87},
        {"label": "Aggressive $10k",     "group": "combo", "sharpe":  0.24, "total_return": 10.12, "max_dd": -15.76, "win_rate": 60.0, "trades": 5,  "avg_yield": 1.14},
        {"label": "Monthly+LowIV $10k",  "group": "combo", "sharpe": -1.64, "total_return":-13.99, "max_dd": -13.99, "win_rate":  0.0, "trades": 2,  "avg_yield": 1.66},
        {"label": "Conservative $50k",   "group": "combo", "sharpe": -1.73, "total_return":-10.53, "max_dd": -10.53, "win_rate":  0.0, "trades": 2,  "avg_yield": 1.50},
        {"label": "Balanced $50k",        "group": "combo", "sharpe": -0.02, "total_return":  4.57, "max_dd": -10.20, "win_rate": 76.9, "trades": 13, "avg_yield": 0.90},
        {"label": "Aggressive $50k",      "group": "combo", "sharpe":  0.03, "total_return":  5.53, "max_dd": -11.14, "win_rate": 64.3, "trades": 14, "avg_yield": 1.14},
    ]

    GROUPS = [
        {"key": "iv_rank_threshold", "title": "IV Rank Threshold",
         "winner": "IV rank >= 60%", "winner_val": ">= 60%",
         "runner_up": "IV rank >= 70% (Sharpe 1.29, +51.1%)",
         "reasoning": ("Waiting for very high IV (>=60%) is the most impactful setting. "
                       "Sharpe jumps to 1.40 with +53% annualised return and only a 10% max drawdown. "
                       "Selling when volatility is richest means you collect significantly more per contract, "
                       "more than compensating for the fewer trade opportunities.")},
        {"key": "delta", "title": "Strike Delta",
         "winner": "Delta 10-15%", "winner_val": "Δ10-15% (deep OTM)",
         "runner_up": "Delta 15-20% (Sharpe -0.26, -3.5%)",
         "reasoning": ("Deep OTM strikes (Δ10-15%) produce the best risk-adjusted returns. "
                       "While premium per trade is lower (~0.5%), the 69% win rate and avoidance of large "
                       "assignment losses keeps Sharpe near zero, vastly better than closer-to-money strikes.")},
        {"key": "dte", "title": "Days to Expiry (DTE)",
         "winner": "Weekly (7 DTE)", "winner_val": "7 DTE (weekly)",
         "runner_up": "Monthly (28 DTE) — Sharpe -1.64, -14.0%",
         "reasoning": ("Weekly options outperform monthly (Sharpe -0.70 vs. -1.64). "
                       "Shorter-dated contracts allow faster capital recycling and quicker exit "
                       "when trades move against you, reducing severity of losing cycles.")},
        {"key": "equity_per_leg", "title": "Max Equity per Leg",
         "winner": "Max 40%/leg", "winner_val": "40% of equity",
         "runner_up": "Max 100%/leg (Sharpe -0.61, -4.6%)",
         "reasoning": ("Allocating only 40% per leg (Sharpe -0.53) enables multiple concurrent positions "
                       "and spreads risk. Note: on a $10k account this may conflict with minimum BTC "
                       "option contract sizes (~$7-8k collateral required).")},
        {"key": "free_margin", "title": "Free Capital Buffer",
         "winner": "Buffer 0%", "winner_val": "0% (no buffer required)",
         "runner_up": "Buffer 10-30% (all identical results)",
         "reasoning": ("All buffer settings produced identical results — the constraint was never binding. "
                       "With a small $10k account a required buffer often blocks all trades. "
                       "Setting to 0% is safest for small accounts; larger accounts may want 10-20%.")},
        {"key": "starting_equity", "title": "Starting Equity",
         "winner": "Start $25,000", "winner_val": "$25,000",
         "runner_up": "Start $100,000 (Sharpe 0.01, +5.1%)",
         "reasoning": ("$25k is the sweet spot — enough collateral for multiple BTC option contracts "
                       "(min ~$7-8k each), unlocking a 76.9% win rate and positive Sharpe. "
                       "The $10k account is too small to size positions meaningfully.")},
        {"key": "combo", "title": "Combined Strategy Preset",
         "winner": "Aggressive $10k", "winner_val": "Aggressive preset ($10k)",
         "runner_up": "Aggressive $50k (Sharpe 0.03, +5.5%)",
         "reasoning": ("The Aggressive $10k preset is the only $10k combo with positive Sharpe (0.24) "
                       "and positive return (+10.1%). At $50k, both Balanced and Aggressive turn "
                       "profitable, with Aggressive $50k narrowly ahead.")},
    ]

    # Summary box
    st.markdown(
        f'<div style="background:{C_CARD};border:1px solid {C_GRID};border-radius:10px;'
        f'padding:20px 24px;margin-bottom:20px;">'
        f'<h4 style="color:{C_BLUE};margin:0 0 12px 0;">Optimal Settings (36 Backtests)</h4>'
        f'<p style="color:{C_TEXT};margin:0 0 10px 0;font-size:15px;line-height:1.8;">'
        f'<strong style="color:{C_GREEN};">IV Rank &gt;= 60%</strong> &middot; '
        f'<strong style="color:{C_GREEN};">Delta Δ10-15% (deep OTM)</strong> &middot; '
        f'<strong style="color:{C_GREEN};">7 DTE (weekly)</strong> &middot; '
        f'<strong style="color:{C_GREEN};">Max 40% equity/leg</strong> &middot; '
        f'<strong style="color:{C_GREEN};">0% free buffer</strong> &middot; '
        f'<strong style="color:{C_GREEN};">$25k+ starting equity</strong></p>'
        f'<p style="color:{C_MUTED};margin:0;font-size:13px;line-height:1.6;">'
        f'Best overall preset: <strong style="color:{C_GREEN};">Aggressive $10k</strong> (Sharpe 0.24, +10.1%). '
        f'For best risk-adjusted performance: IV &gt;= 60% + deep OTM on $25k+ account '
        f'(Sharpe 1.40, +53% annualised).</p></div>',
        unsafe_allow_html=True,
    )

    with st.expander("Full Results Table — all 36 runs", expanded=False):
        df_all = pd.DataFrame(BACKTEST_RESULTS)
        disp = df_all[["label", "group", "sharpe", "total_return", "max_dd", "win_rate", "trades", "avg_yield"]].copy()
        disp.columns = ["Setting", "Group", "Sharpe", "Return %", "Max DD %", "Win Rate %", "Trades", "Avg Yield %"]

        def _cs(v):
            if not isinstance(v, (int, float)): return ""
            return "color: #3fb950" if v > 0 else ("color: #d29922" if v > -0.5 else "color: #f85149")

        def _cr(v):
            if not isinstance(v, (int, float)): return ""
            return "color: #3fb950" if v > 0 else "color: #f85149"

        st.dataframe(
            disp.style.map(_cs, subset=["Sharpe"]).map(_cr, subset=["Return %"]),
            use_container_width=True, height=420,
        )

    st.divider()
    st.markdown("### Parameter Group Analysis")

    for meta in GROUPS:
        group_data   = [r for r in BACKTEST_RESULTS if r["group"] == meta["key"]]
        winner_label = meta["winner"]
        st.markdown(f"#### {meta['title']}")
        chart_col, card_col = st.columns([2, 1], gap="large")

        with chart_col:
            labels  = [r["label"]  for r in group_data]
            sharpes = [r["sharpe"] for r in group_data]
            bar_colors = [C_GREEN if r["label"] == winner_label else C_BLUE for r in group_data]
            fig = go.Figure(go.Bar(
                x=labels, y=sharpes, marker_color=bar_colors,
                text=[f"{s:+.2f}" for s in sharpes],
                textposition="outside", textfont=dict(color=C_TEXT, size=10),
            ))
            fig.add_hline(y=0, line=dict(color=C_MUTED, dash="dash", width=1))
            fig.update_layout(
                title=dict(text=f"Sharpe by {meta['title']}", font=dict(color=C_TEXT, size=12)),
                paper_bgcolor=C_CARD, plot_bgcolor=C_CARD,
                font=dict(color=C_TEXT, size=11),
                xaxis=dict(gridcolor=C_GRID, zerolinecolor=C_GRID, tickangle=-25, tickfont=dict(size=9)),
                yaxis=dict(gridcolor=C_GRID, zerolinecolor=C_GRID),
                margin=dict(l=50, r=20, t=36, b=70), height=270,
            )
            st.plotly_chart(fig, use_container_width=True, key=f"reco_chart_{meta['key']}")

        with card_col:
            wd    = next((r for r in group_data if r["label"] == winner_label), group_data[0])
            s_col = C_GREEN if wd["sharpe"] > 0 else (C_AMBER if wd["sharpe"] > -0.5 else C_RED)
            r_col = C_GREEN if wd["total_return"] > 0 else C_RED
            w_col = C_GREEN if wd["win_rate"] >= 60 else (C_AMBER if wd["win_rate"] >= 40 else C_RED)
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_GRID};border-radius:8px;padding:16px 18px;">'
                f'<div style="color:{C_BLUE};font-size:10px;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">Winner</div>'
                f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;margin-bottom:12px;">{meta["winner_val"]}</div>'
                f'<div style="display:flex;gap:18px;margin-bottom:12px;">'
                f'<div><div style="color:{C_MUTED};font-size:10px;">Sharpe</div>'
                f'<div style="color:{s_col};font-size:20px;font-weight:700;">{wd["sharpe"]:+.2f}</div></div>'
                f'<div><div style="color:{C_MUTED};font-size:10px;">Return</div>'
                f'<div style="color:{r_col};font-size:20px;font-weight:700;">{wd["total_return"]:+.1f}%</div></div>'
                f'<div><div style="color:{C_MUTED};font-size:10px;">Win Rate</div>'
                f'<div style="color:{w_col};font-size:20px;font-weight:700;">{wd["win_rate"]:.0f}%</div></div>'
                f'</div>'
                f'<div style="color:{C_MUTED};font-size:11px;line-height:1.55;margin-bottom:10px;">{meta["reasoning"]}</div>'
                f'<div style="border-top:1px solid {C_GRID};padding-top:8px;color:{C_MUTED};font-size:10px;">Runner-up: {meta["runner_up"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

def tab_fleet() -> None:
    """
    Cross-bot fleet view. Answers the user's question: "how is the system
    working visually + ROI per bot, capital tied up, returns?"

    Reads:
      - farm/status.json (supervisor's per-bot snapshot, refreshed every 60s)
      - farm/<slug>/data/current_position.json (live open-position detail)
      - farm/<slug>/data/trades.csv (closed-trade ledger)
      - farm/<slug>/bot_heartbeat.json (latest tick timestamp)

    Renders:
      1. Aggregate cards (fleet equity, margin deployed, open positions, etc.)
      2. Per-bot leaderboard table sorted by ROI
      3. Open-positions live snapshot
      4. Equity-curve comparison chart (one normalized line per bot)
      5. Capital-efficiency scatter (return % vs avg margin util %)
    """
    st.markdown("### 🛰 Fleet Monitor")
    st.caption(
        "Live view of every paper bot. Each row is one config running in "
        "`farm/<slug>/`. Sorted by capital efficiency — the top-left of the "
        "scatter (high return, low margin used) is the thesis you're hunting for."
    )

    # ── Refresh + global pause state ────────────────────────────────────────
    # Manual-only by design: the previous auto-refresh attempt (tab_paper)
    # caused UI hangs because it re-ran the whole script. A future enhancement
    # could use st.fragment(run_every=...) for in-place refresh, but that's
    # parked for now — manual refresh is reliable.
    rcol1, rcol2, rcol3 = st.columns([1, 1, 3])
    with rcol1:
        if st.button("🔄 Refresh", key="fleet_refresh_btn", use_container_width=True):
            st.rerun()
    with rcol2:
        # Global Pause/Resume pill+button (matches mobile Farm tab's verbiage).
        if kill_switch_active():
            if st.button("▶ Resume", key="fleet_resume_btn",
                         type="primary", use_container_width=True,
                         help="Resume new trade entries across all bots."):
                clear_kill_switch()
                st.rerun()
        else:
            if st.button("⏸ Pause all", key="fleet_pause_btn",
                         use_container_width=True,
                         help="Block new trade entries on every bot. Open positions still settle."):
                (BOT_DIR / "KILL_SWITCH").write_text(
                    f"Paused from Fleet tab at {datetime.utcnow().isoformat()}\n"
                    "Delete this file to resume trading."
                )
                st.rerun()
    with rcol3:
        paused = kill_switch_active()
        pill_bg = C_AMBER if paused else C_GREEN
        pill_label = "⏸ TRADING PAUSED" if paused else "🟢 TRADING ACTIVE"
        st.markdown(
            f'<div style="display:flex;align-items:center;height:38px;'
            f'gap:12px;margin-top:0;">'
            f'<span style="background:{pill_bg};color:#0d1117;font-weight:600;'
            f'font-size:11px;padding:4px 10px;border-radius:999px;'
            f'letter-spacing:0.5px;">{pill_label}</span>'
            f'<span style="color:{C_MUTED};font-size:11px;">'
            f'Loaded {datetime.utcnow().strftime("%H:%M:%S")} UTC · '
            f'farm/status.json refreshes every 60s'
            f'</span></div>',
            unsafe_allow_html=True,
        )

    farm_dir = BOT_DIR / "farm"
    status_path = farm_dir / "status.json"
    if not status_path.exists():
        st.info(
            "No `farm/status.json` yet. Start the farm with "
            "`python3.11 bot_farm.py` to populate this view."
        )
        return

    try:
        farm_status = json.loads(status_path.read_text())
    except Exception as exc:
        st.error(f"Could not read farm/status.json: {exc}")
        return

    bots_meta = farm_status.get("bots", []) or []
    if not bots_meta:
        st.info("Farm supervisor is up but no bots are running.")
        return

    # ── Why-not-trading diagnostic helper ────────────────────────────────────
    # The /farm/bot/{id}/why_not_trading endpoint (added by the parallel
    # dispatch session) gives a plain-English reason each bot is or isn't
    # eligible to enter a new trade right now. Reusing it here means the
    # leaderboard "Reason" column matches what the mobile app shows.
    def _bot_diag(bot_id: str) -> dict:
        try:
            from api import get_bot_why_not_trading
            return get_bot_why_not_trading(bot_id)
        except Exception:
            return {"ready": None, "reason": "—", "checks": {}}

    # ── Augment each bot row with live data from per-bot files ───────────────
    rows: list[dict] = []
    diags: dict[str, dict] = {}    # bot_id → diag dict, used in drill-down too
    now = time.time()
    for b in bots_meta:
        bot_id = b.get("id", "?")
        slug_dir = farm_dir / bot_id
        m = b.get("metrics", {}) or {}
        cs = b.get("config_summary", {}) or {}

        starting_equity = float(m.get("starting_equity") or 0)
        current_equity  = float(m.get("current_equity")  or starting_equity)
        roi_pct         = ((current_equity - starting_equity) / starting_equity * 100.0
                           if starting_equity > 0 else 0.0)

        # Open position detail
        cp_path = slug_dir / "data" / "current_position.json"
        pos_open = False
        pos_inst = "—"
        pos_unreal = 0.0
        pos_dte = None
        pos_delta = None
        margin_used_usd = 0.0
        if cp_path.exists():
            try:
                cp = json.loads(cp_path.read_text())
                if cp.get("open"):
                    pos_open = True
                    pos_inst = cp.get("instrument_name", "?")
                    pos_unreal = float(cp.get("unrealized_pnl_usd") or 0)
                    pos_dte = cp.get("days_to_expiry")
                    pos_delta = cp.get("current_delta")
                    # Cash-secured collateral = strike × contracts (BTC of underlying)
                    strike = float(cp.get("strike") or 0)
                    contracts = float(cp.get("contracts") or 0)
                    margin_used_usd = strike * contracts
            except Exception:
                pass

        # Heartbeat freshness
        hb_path = slug_dir / "bot_heartbeat.json"
        last_tick_s = None
        if hb_path.exists():
            try:
                hb = json.loads(hb_path.read_text())
                last_tick_s = max(0, int(now - hb.get("timestamp", 0)))
            except Exception:
                pass

        # Diagnostic: ready / waiting on what?
        diag = _bot_diag(bot_id)
        diags[bot_id] = diag
        # Compact reason for the leaderboard row.
        reason_short = (diag.get("reason") or "—")
        if len(reason_short) > 38:
            reason_short = reason_short[:35] + "…"
        if diag.get("ready") is True:
            reason_short = "🟢 " + reason_short
        elif diag.get("ready") is False:
            reason_short = "🟡 " + reason_short

        rows.append({
            "Bot": bot_id,
            "Status": b.get("status", "?"),
            "Equity $": current_equity,
            "ROI %": roi_pct,
            "Open Pos": pos_inst,
            "Unrealized $": pos_unreal,
            "Margin Used $": margin_used_usd,
            "Util %": (margin_used_usd / current_equity * 100.0)
                      if (pos_open and current_equity > 0) else 0.0,
            "Trades": int(m.get("num_trades") or 0),
            "Win %": float(m.get("win_rate") or 0) * 100.0,
            "Sharpe": float(m.get("sharpe") or 0),
            "Max DD %": float(m.get("max_drawdown") or 0) * 100.0,
            "DTE": pos_dte if pos_open else None,
            "Δ": round(pos_delta, 3) if pos_delta is not None else None,
            "Last tick": (f"{last_tick_s}s" if last_tick_s is not None and last_tick_s < 600
                          else (f"{last_tick_s // 60}m" if last_tick_s is not None else "—")),
            "Why not trading": reason_short,
            "IV thresh": cs.get("iv_rank_threshold"),
            "Start $": starting_equity,
        })

    df = pd.DataFrame(rows)

    # ── Aggregate cards ──────────────────────────────────────────────────────
    n_running = sum(1 for b in bots_meta if b.get("status") == "running")
    n_open = int(df["Open Pos"].apply(lambda v: v != "—").sum())
    fleet_equity = float(df["Equity $"].sum())
    fleet_start  = float(df["Start $"].sum())
    fleet_unreal = float(df["Unrealized $"].sum())
    fleet_margin = float(df["Margin Used $"].sum())
    fleet_trades = int(df["Trades"].sum())
    util_pct = (fleet_margin / fleet_equity * 100.0) if fleet_equity > 0 else 0.0
    pnl_pct = ((fleet_equity - fleet_start) / fleet_start * 100.0) if fleet_start > 0 else 0.0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        metric_card("Bots Running", f"{n_running} / {len(bots_meta)}")
    with c2:
        metric_card("Fleet Equity", f"${fleet_equity:,.0f}",
                    "green" if fleet_equity >= fleet_start else "red")
    with c3:
        metric_card("Fleet ROI", f"{pnl_pct:+.2f}%",
                    "green" if pnl_pct >= 0 else "red")
    with c4:
        metric_card("Open Positions", f"{n_open}")
    with c5:
        # Compact "$Xk" representation prevents the value wrapping mid-number
        # in the narrow card column (the previous "$15,300" got broken to
        # "$15,30\n0" on Streamlit's default card width at 6-col layout).
        if fleet_margin >= 1_000_000:
            margin_short = f"${fleet_margin/1_000_000:.1f}M"
        elif fleet_margin >= 10_000:
            margin_short = f"${fleet_margin/1_000:.0f}k"
        else:
            margin_short = f"${fleet_margin:,.0f}"
        metric_card(
            "Margin Deployed",
            f"{margin_short}<br>"
            f"<span style='font-size:13px;font-weight:400;opacity:0.75'>"
            f"{util_pct:.1f}% of fleet</span>",
            "amber" if util_pct > 50 else "",
        )
    with c6:
        metric_card("Closed Trades", f"{fleet_trades}")

    if abs(fleet_unreal) > 0.01:
        st.caption(
            f"Unrealized fleet P&L: **${fleet_unreal:+,.2f}** "
            f"(across {n_open} open positions)"
        )

    st.divider()

    # ── Per-bot leaderboard ──────────────────────────────────────────────────
    st.markdown("#### 📊 Per-bot leaderboard")
    st.caption("Sorted by ROI %. Click any column header to re-sort.")

    # ── Build display dataframe ──────────────────────────────────────────────
    # Keep the column set small enough that each column gets >= 70px in the
    # ~780px content area. Anything above ~10 columns truncates the leftmost
    # to 4 characters and renders the rest nearly invisible. Power-user
    # columns (Sharpe, MaxDD, IV thresh, Last tick) live in the drill-down.
    df_view = df.sort_values("ROI %", ascending=False).reset_index(drop=True).copy()
    df_view["Bot"] = df_view["Bot"].apply(lambda s: s if len(s) <= 22 else s[:19] + "…")

    df_pretty = pd.DataFrame({
        "Bot":      df_view["Bot"],
        "ROI":      df_view["ROI %"].apply(lambda v: f"{v:+.2f}%"),
        "Equity":   df_view["Equity $"].apply(lambda v: f"${v:,.0f}"),
        "Open Pos": df_view["Open Pos"].apply(
            lambda s: s if (s == "—" or len(s) <= 18) else s[:15] + "…"
        ),
        "Unreal":   df_view["Unrealized $"].apply(lambda v: f"${v:+,.0f}" if v else "—"),
        "Margin":   df_view["Margin Used $"].apply(
            lambda v: f"${v/1000:.1f}k" if v >= 10_000 else (f"${v:,.0f}" if v else "—")
        ),
        "Util":     df_view["Util %"].apply(lambda v: f"{v:.1f}%" if v else "—"),
        "Trades":   df_view["Trades"].astype(str),
        "Win%":     df_view["Win %"].apply(lambda v: f"{v:.0f}%"),
        "Status":   df_view["Why not trading"].apply(
            lambda s: s if len(s) <= 32 else s[:29] + "…"
        ),
    })
    st.dataframe(df_pretty, use_container_width=True, hide_index=True, height=560)

    st.divider()

    # ── Open positions live snapshot ────────────────────────────────────────
    open_rows = df[df["Open Pos"] != "—"].copy()
    if len(open_rows) > 0:
        st.markdown(f"#### 🎯 Open positions ({len(open_rows)})")
        st.caption("What's currently at risk across the fleet.")
        open_sorted = open_rows.sort_values("Unrealized $", ascending=False).reset_index(drop=True)
        # Use st.table here — st.dataframe was overlaying a translucent red
        # tint on the cells (Streamlit's automatic conditional highlight on
        # small dataframes with mixed string/number columns). st.table
        # renders plain HTML, no canvas, no surprise colours.
        open_pretty = pd.DataFrame({
            "Bot":        open_sorted["Bot"],
            "Open Pos":   open_sorted["Open Pos"],
            "DTE":        open_sorted["DTE"].apply(
                lambda v: f"{int(v)}" if pd.notna(v) else "—"
            ),
            "Delta":      open_sorted["Δ"].apply(
                lambda v: f"{v:.3f}" if pd.notna(v) else "—"
            ),
            "Collateral": open_sorted["Margin Used $"].apply(lambda v: f"${v:,.0f}"),
            "Unreal P&L": open_sorted["Unrealized $"].apply(lambda v: f"${v:+,.0f}"),
        })
        st.table(open_pretty)

    st.divider()

    # ── Capital-efficiency scatter ──────────────────────────────────────────
    st.markdown("#### 🎯 Capital efficiency map")
    st.caption(
        "Top-left = high return on low margin (the small-capital × high-ROI thesis). "
        "Each dot is one bot. Bots without open positions cluster at Util=0."
    )
    try:
        import plotly.graph_objects as go
        active_df = df[df["Trades"] > 0].copy()
        if len(active_df) == 0:
            st.info("No bot has closed trades yet — chart populates when trades start firing.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=active_df["Util %"],
                y=active_df["ROI %"],
                mode="markers+text",
                text=active_df["Bot"],
                textposition="top center",
                marker=dict(
                    size=14,
                    color=active_df["ROI %"],
                    colorscale="RdYlGn",
                    cmid=0,
                    line=dict(width=1, color="#666"),
                ),
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "ROI: %{y:+.2f}%<br>"
                    "Margin util: %{x:.1f}%<extra></extra>"
                ),
            ))
            fig.add_hline(y=0, line=dict(color=C_GRID, width=1, dash="dot"))
            fig.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor=C_BG,
                plot_bgcolor=C_CARD,
                font=dict(color=C_TEXT, size=11),
                xaxis=dict(
                    title="Avg margin utilization (%)",
                    gridcolor=C_GRID,
                    zerolinecolor=C_GRID,
                ),
                yaxis=dict(
                    title="ROI (%)",
                    gridcolor=C_GRID,
                    zerolinecolor=C_GRID,
                ),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        st.warning(f"Couldn't render capital-efficiency chart: {exc}")

    st.divider()

    # ── Equity-curve comparison ─────────────────────────────────────────────
    st.markdown("#### 📈 Equity curves (normalized to 100%)")
    st.caption(
        "Each line is one bot's equity over time, scaled so 100% = starting "
        "equity. Diverging lines show which thesis is winning."
    )
    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        plotted = 0
        for b in bots_meta:
            slug = b.get("id", "")
            ec_path = farm_dir / slug / "data" / "equity_curve.json"
            if not ec_path.exists():
                continue
            try:
                ec = json.loads(ec_path.read_text())
                dates = ec.get("dates", [])
                equity = ec.get("equity", [])
                start_eq = float(ec.get("starting_equity") or (equity[0] if equity else 1))
                if not dates or not equity or start_eq <= 0:
                    continue
                normalized = [v / start_eq * 100.0 for v in equity]
                fig.add_trace(go.Scatter(
                    x=dates, y=normalized, mode="lines",
                    name=slug, line=dict(width=1.5),
                    hovertemplate=f"<b>{slug}</b><br>%{{x}}<br>%{{y:.2f}}%<extra></extra>",
                ))
                plotted += 1
            except Exception:
                continue
        if plotted == 0:
            st.info(
                "No `equity_curve.json` files yet — these are written when a bot "
                "closes its first trade. Once trades start firing, the curves populate."
            )
        else:
            fig.add_hline(y=100, line=dict(color=C_GRID, width=1, dash="dot"))
            fig.update_layout(
                height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor=C_BG,
                plot_bgcolor=C_CARD,
                font=dict(color=C_TEXT, size=11),
                xaxis=dict(title="Date", gridcolor=C_GRID),
                yaxis=dict(title="Equity (% of start)", gridcolor=C_GRID),
                legend=dict(
                    orientation="h",
                    bgcolor="rgba(0,0,0,0)",
                    font=dict(size=10),
                ),
            )
            st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        st.warning(f"Couldn't render equity curves: {exc}")

    st.divider()

    # ── Trades timeline ─────────────────────────────────────────────────────
    # Strip plot — when did each bot fire trades, how big was each fill?
    # Lets the user spot herd behaviour (everyone trades the same day) vs.
    # each thesis catching different setups.
    st.markdown("#### ⏱ Trade timeline (last 30 days)")
    st.caption(
        "One dot per closed trade. X = close time, Y = bot, dot size = |P&L|, "
        "colour = green/red for win/loss. Cluster of dots on the same day = "
        "fleet-wide signal; isolated dots = thesis-specific catches."
    )
    try:
        import plotly.graph_objects as go
        from datetime import datetime as _dt, timedelta as _td

        cutoff = datetime.utcnow() - _td(days=30)
        events: list[dict] = []
        for b in bots_meta:
            slug = b.get("id", "")
            tcsv = farm_dir / slug / "data" / "trades.csv"
            if not tcsv.exists():
                continue
            try:
                import csv as _csv
                with open(tcsv, newline="") as f:
                    for row in _csv.DictReader(f):
                        try:
                            ts = _dt.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                            ts_naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
                            if ts_naive < cutoff:
                                continue
                            pnl = float(row.get("pnl_usd") or 0)
                            events.append({
                                "bot": slug,
                                "ts": ts_naive,
                                "pnl": pnl,
                                "instr": row.get("instrument", "?"),
                                "reason": row.get("reason", ""),
                            })
                        except (ValueError, KeyError, TypeError):
                            continue
            except Exception:
                continue

        if not events:
            st.info("No trades closed in the last 30 days yet — populates as bots fire.")
        else:
            # Sort bots so that y-axis is consistent (most-active first)
            from collections import Counter
            bot_counts = Counter(e["bot"] for e in events)
            bot_order = [b for b, _ in bot_counts.most_common()]

            fig = go.Figure()
            for sign, name, color in [(1, "Win",  C_GREEN), (-1, "Loss", C_RED)]:
                evs = [e for e in events if (e["pnl"] >= 0) == (sign > 0)]
                if not evs:
                    continue
                fig.add_trace(go.Scatter(
                    x=[e["ts"] for e in evs],
                    y=[e["bot"] for e in evs],
                    mode="markers",
                    name=name,
                    marker=dict(
                        size=[max(8, min(30, abs(e["pnl"]) / 5)) for e in evs],
                        color=color,
                        opacity=0.65,
                        line=dict(width=1, color=C_GRID),
                    ),
                    customdata=[(e["instr"], e["pnl"], e["reason"]) for e in evs],
                    hovertemplate=(
                        "<b>%{y}</b><br>%{x}<br>"
                        "%{customdata[0]}<br>P&L $%{customdata[1]:+,.2f}<br>"
                        "<i>%{customdata[2]}</i><extra></extra>"
                    ),
                ))
            fig.update_layout(
                height=max(220, 30 * len(bot_order) + 80),
                margin=dict(l=10, r=10, t=20, b=10),
                paper_bgcolor=C_BG,
                plot_bgcolor=C_CARD,
                font=dict(color=C_TEXT, size=11),
                xaxis=dict(title="", gridcolor=C_GRID),
                yaxis=dict(
                    title="",
                    categoryorder="array",
                    categoryarray=bot_order,
                    gridcolor=C_GRID,
                ),
                showlegend=True,
                legend=dict(orientation="h", bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                f"{len(events)} trades closed across {len(bot_order)} bot(s) in the last 30 days."
            )
    except Exception as exc:
        st.warning(f"Couldn't render trade timeline: {exc}")

    st.divider()

    # ── Variance budget ─────────────────────────────────────────────────────
    # Which bots are contributing most to (or absorbing most of) the fleet's
    # equity volatility? Sums each bot's pnl over the trading window, then
    # ranks by absolute contribution. The user can spot which thesis is
    # carrying the fleet, and which is bleeding.
    st.markdown("#### 📉 Variance budget — who moves the fleet")
    st.caption(
        "Each bar = one bot's net P&L contribution (sum of pnl_usd). "
        "Right (green) = profit contribution to fleet; left (red) = drag. "
        "If one bot dominates, the fleet's results are really just that bot."
    )
    try:
        import plotly.graph_objects as go
        contributions: list[tuple[str, float, int]] = []
        for b in bots_meta:
            slug = b.get("id", "")
            tcsv = farm_dir / slug / "data" / "trades.csv"
            if not tcsv.exists():
                continue
            try:
                import csv as _csv
                pnls = []
                with open(tcsv, newline="") as f:
                    for row in _csv.DictReader(f):
                        try:
                            pnls.append(float(row.get("pnl_usd") or 0))
                        except (ValueError, TypeError):
                            continue
                if pnls:
                    contributions.append((slug, sum(pnls), len(pnls)))
            except Exception:
                continue

        if not contributions:
            st.info(
                "No closed trades yet — variance budget is empty. "
                "Will populate as bots accumulate fills."
            )
        else:
            contributions.sort(key=lambda x: x[1])
            fig = go.Figure(go.Bar(
                x=[c[1] for c in contributions],
                y=[c[0] for c in contributions],
                orientation="h",
                marker=dict(
                    color=[C_GREEN if c[1] >= 0 else C_RED for c in contributions],
                ),
                text=[f"${c[1]:+,.0f} ({c[2]} trades)" for c in contributions],
                textposition="outside",
                hovertemplate="<b>%{y}</b><br>P&L $%{x:+,.2f}<extra></extra>",
            ))
            total = sum(c[1] for c in contributions)
            fig.add_vline(x=0, line=dict(color=C_GRID, width=1))
            fig.update_layout(
                height=max(220, 26 * len(contributions) + 80),
                margin=dict(l=10, r=80, t=20, b=20),
                paper_bgcolor=C_BG,
                plot_bgcolor=C_CARD,
                font=dict(color=C_TEXT, size=11),
                xaxis=dict(title="Net P&L contribution ($)", gridcolor=C_GRID),
                yaxis=dict(title="", gridcolor=C_GRID, automargin=True),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                f"Fleet aggregate net P&L: **${total:+,.2f}** across "
                f"{sum(c[2] for c in contributions)} closed trades."
            )
    except Exception as exc:
        st.warning(f"Couldn't render variance budget: {exc}")

    st.divider()

    # ── Per-bot drill-down ──────────────────────────────────────────────────
    # Lets the user pick any bot from the leaderboard and inspect that one's
    # config, recent trades, log tail, and equity curve in isolation. Without
    # this, the only way to drill into a single bot is to manually `cd
    # farm/<slug>/` and tail logs.
    st.markdown("#### 🔬 Per-bot drill-down")
    bot_options = [r["Bot"] for r in rows]
    if not bot_options:
        st.info("No bots in fleet to drill into.")
    else:
        selected_bot = st.selectbox(
            "Pick a bot to inspect",
            bot_options,
            key="fleet_drilldown_select",
        )
        if selected_bot:
            slug = selected_bot
            slug_dir = farm_dir / slug
            sel_meta = next((b for b in bots_meta if b.get("id") == slug), {})
            sel_metrics = sel_meta.get("metrics", {}) or {}
            sel_cfg = sel_meta.get("config_summary", {}) or {}

            # Header card
            roi = ((sel_metrics.get("current_equity", 0) - sel_metrics.get("starting_equity", 0))
                   / sel_metrics.get("starting_equity", 1) * 100.0
                   if sel_metrics.get("starting_equity") else 0.0)
            roi_col = C_GREEN if roi >= 0 else C_RED
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_GRID};'
                f'border-radius:8px;padding:14px 18px;margin-bottom:12px;">'
                f'<div style="color:{C_TEXT};font-weight:600;font-size:15px;">{slug}</div>'
                f'<div style="color:{C_MUTED};font-size:12px;margin-top:4px;">'
                f'status: <span style="color:{C_TEXT}">{sel_meta.get("status","?")}</span> · '
                f'pid: <span style="color:{C_TEXT}">{sel_meta.get("pid","?")}</span> · '
                f'uptime: <span style="color:{C_TEXT}">{sel_meta.get("uptime_hours",0):.1f}h</span> · '
                f'equity: <span style="color:{C_TEXT}">${sel_metrics.get("current_equity",0):,.0f}</span> · '
                f'ROI: <span style="color:{roi_col};font-weight:600;">{roi:+.2f}%</span> · '
                f'trades: <span style="color:{C_TEXT}">{sel_metrics.get("num_trades",0)}</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

            # ── Why-not-trading diagnostic ────────────────────────────────────
            # Reuses the /farm/bot/{id}/why_not_trading endpoint that the
            # mobile app uses, so the dashboard stays in sync with what the
            # PWA shows. Per-check breakdown lives in an expander.
            sel_diag = diags.get(slug, {}) or {}
            ready = sel_diag.get("ready")
            reason = sel_diag.get("reason", "—")
            if ready is True:
                badge_colour = C_GREEN
                badge_icon = "🟢"
                badge_label = "READY"
            elif ready is False:
                badge_colour = C_AMBER
                badge_icon = "🟡"
                badge_label = "NOT TRADING"
            else:
                badge_colour = C_MUTED
                badge_icon = "⚫"
                badge_label = "UNKNOWN"
            st.markdown(
                f'<div style="background:{C_CARD};border-left:4px solid {badge_colour};'
                f'border-radius:4px;padding:10px 14px;margin-bottom:12px;">'
                f'<div style="color:{badge_colour};font-size:11px;font-weight:600;'
                f'letter-spacing:0.5px;text-transform:uppercase;">'
                f'{badge_icon} {badge_label}</div>'
                f'<div style="color:{C_TEXT};font-size:13px;margin-top:4px;">{reason}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            with st.expander("Per-check breakdown", expanded=False):
                checks = sel_diag.get("checks") or {}
                if checks:
                    st.json(checks, expanded=True)
                else:
                    st.caption("No per-check data available.")

            dcol1, dcol2 = st.columns(2)

            # Config summary
            with dcol1:
                st.markdown("**Config summary**")
                st.json(sel_cfg, expanded=False)

            # Open position (if any)
            with dcol2:
                st.markdown("**Open position**")
                cp = slug_dir / "data" / "current_position.json"
                if cp.exists():
                    try:
                        cp_data = json.loads(cp.read_text())
                        if cp_data.get("open"):
                            st.json({
                                "instrument":      cp_data.get("instrument_name"),
                                "strike":          cp_data.get("strike"),
                                "contracts":       cp_data.get("contracts"),
                                "entry_price_btc": cp_data.get("entry_price_btc"),
                                "current_delta":   cp_data.get("current_delta"),
                                "DTE":             cp_data.get("days_to_expiry"),
                                "unrealized_usd":  cp_data.get("unrealized_pnl_usd"),
                                "premium_usd":     cp_data.get("premium_collected"),
                            }, expanded=False)
                        else:
                            st.caption("Currently flat.")
                    except Exception as exc:
                        st.warning(f"Couldn't parse current_position.json: {exc}")
                else:
                    st.caption("No current_position.json yet.")

            # Recent trades
            st.markdown("**Recent trades (last 15)**")
            tcsv_path = slug_dir / "data" / "trades.csv"
            if tcsv_path.exists():
                try:
                    tdf = pd.read_csv(tcsv_path)
                    if len(tdf) > 0:
                        cols = [c for c in [
                            "timestamp", "instrument", "option_type", "strike",
                            "contracts", "pnl_usd", "equity_after", "reason",
                        ] if c in tdf.columns]
                        st.dataframe(
                            tdf[cols].tail(15),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.caption("No trades closed yet.")
                except Exception as exc:
                    st.warning(f"Couldn't read trades.csv: {exc}")
            else:
                st.caption("No trades.csv yet — bot hasn't closed a trade.")

            # Log tail
            st.markdown("**Log tail (last 30 lines)**")
            log_path = slug_dir / "logs" / "bot.log"
            if log_path.exists():
                try:
                    lines = log_path.read_text().splitlines()[-30:]
                    # Strip ANSI colour codes for clean display
                    import re as _re
                    ansi_re = _re.compile(r"\x1b\[[0-9;]*m|\[[0-9]+m")
                    cleaned = "\n".join(ansi_re.sub("", L) for L in lines)
                    st.code(cleaned, language=None)
                except Exception as exc:
                    st.warning(f"Couldn't read bot.log: {exc}")
            else:
                st.caption("No bot.log yet for this bot.")


def tab_forecasts() -> None:
    """
    Out-of-sample forecast validation tab.

    Shows the snapshots produced by `forecast_validator.py create`, their
    pending/due/validated status, and the forecast-vs-actual comparison
    once a snapshot's horizon has elapsed. The whole point: surface drift
    between what the backtest predicted and what the bot actually delivered
    over the same window.
    """
    st.markdown("### 📊 Forecast Validation")
    st.caption(
        "Each snapshot freezes the backtest's forecast at a point in time. "
        "Once the horizon elapses, the validator compares the forecast to "
        "actual trades.csv outcomes during the window — divergences here are "
        "the early-warning signal that the backtest is mispricing reality."
    )

    # Lazy import: forecast_validator depends on backtester which pulls in
    # matplotlib, scipy, etc. — keep dashboard startup fast if unused.
    try:
        from forecast_validator import (
            list_snapshots,
            create_snapshot,
            validate_all_due,
        )
    except Exception as exc:
        st.error(f"forecast_validator module failed to import: {exc}")
        return

    # ── Action row: create + validate ─────────────────────────────────────────
    col_create, col_validate, col_refresh = st.columns([2, 1, 1])

    with col_create.expander("➕ Create new snapshot", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            horizon = st.number_input(
                "Horizon (days)", min_value=7, max_value=180, value=30, step=7,
                key="forecast_horizon_days",
            )
        with c2:
            equity_override = st.number_input(
                "Starting equity (USD)",
                min_value=0.0, max_value=10_000_000.0, value=100_000.0, step=10_000.0,
                key="forecast_equity_override",
                help="Overrides config.yaml. Use your live account equity for a "
                     "realistic forecast (config default is $1k which is below "
                     "the minimum lot collateral on most strikes).",
            )
        note = st.text_input(
            "Note (optional)", value="",
            key="forecast_note",
            placeholder="e.g. after parameter change",
        )
        # Capture current market context if the heartbeat has it
        btc_now = 0.0
        iv_now = 0.0
        _hb_path = BOT_DIR / "bot_heartbeat.json"
        if _hb_path.exists():
            try:
                _hb = json.loads(_hb_path.read_text())
                btc_now = float(_hb.get("btc_price", 0.0))
                iv_now = float(_hb.get("iv_rank", 0.0))
            except Exception:
                pass
        if st.button("Run backtest + create snapshot", key="forecast_create_btn",
                     use_container_width=True):
            try:
                with st.spinner("Running backtester and capturing forecast…"):
                    out = create_snapshot(
                        horizon_days=int(horizon),
                        btc_price_now=btc_now,
                        iv_rank_now=iv_now,
                        note=note,
                        starting_equity_override=float(equity_override) if equity_override > 0 else None,
                    )
                st.success(f"Snapshot saved: `{out.name}`")
                st.rerun()
            except Exception as exc:
                st.error(f"Snapshot creation failed: {exc}")

    with col_validate:
        st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
        if st.button("🔍 Validate due", key="forecast_validate_btn",
                     use_container_width=True,
                     help="Compare forecast vs actual for any snapshot whose horizon has elapsed."):
            try:
                with st.spinner("Validating snapshots…"):
                    results = validate_all_due()
                if not results:
                    st.info("Nothing due to validate.")
                else:
                    fail_n = sum(1 for r in results if r["overall_status"] == "fail")
                    warn_n = sum(1 for r in results if r["overall_status"] == "warning")
                    pass_n = sum(1 for r in results if r["overall_status"] == "pass")
                    st.success(
                        f"Validated {len(results)} snapshot(s) — "
                        f"✅ {pass_n} pass · ⚠️ {warn_n} warning · 🔴 {fail_n} fail"
                    )
                    st.rerun()
            except Exception as exc:
                st.error(f"Validation failed: {exc}")

    with col_refresh:
        st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
        if st.button("🔄 Refresh", key="forecast_refresh_btn",
                     use_container_width=True):
            st.rerun()

    st.divider()

    # ── List existing snapshots ───────────────────────────────────────────────
    snaps = list_snapshots()
    if not snaps:
        st.info(
            "No snapshots yet. Create one above (or via "
            "`python forecast_validator.py create --horizon-days 30`) "
            "to start the validation loop."
        )
        return

    st.caption(f"{len(snaps)} snapshot(s) in `data/forecasts/`")

    _STATUS_BADGE = {
        "pending":  ("🕐", C_MUTED, "Pending — horizon hasn't elapsed yet"),
        "due":      ("⏰", C_AMBER, "Due — run validate to compare"),
        "pass":     ("🟢", C_GREEN, "Pass — actual within forecast envelope"),
        "warning":  ("🟡", C_AMBER, "Warning — partial drift, investigate"),
        "fail":     ("🔴", C_RED,   "FAIL — actual materially off forecast"),
    }

    for snap_meta in reversed(snaps):   # newest first
        try:
            snap = json.loads(Path(snap_meta["path"]).read_text())
        except Exception as exc:
            st.warning(f"Couldn't read {snap_meta['path']}: {exc}")
            continue

        status = snap_meta["status"]
        emoji, colour, hint = _STATUS_BADGE.get(status, ("?", C_MUTED, ""))

        # ── Header card ────────────────────────────────────────────────────────
        snap_id = snap.get("snapshot_id", "?")
        created = snap.get("created_at", "")[:19].replace("T", " ")
        horizon_d = snap.get("horizon_days", "?")
        validate_at = snap.get("validate_after", "")[:19].replace("T", " ")
        note_txt = snap.get("note") or ""
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {colour};'
            f'border-radius:8px;padding:14px 18px;margin-bottom:8px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<div><span style="font-size:18px;">{emoji}</span> '
            f'<strong style="color:{C_TEXT};font-size:14px;">{snap_id}</strong>'
            f' <span style="color:{C_MUTED};font-size:12px;">· '
            f'created {created} · {horizon_d}-day horizon · validate after {validate_at}'
            f'</span></div>'
            f'<div style="color:{colour};font-size:12px;font-weight:600;">{status.upper()}</div>'
            f'</div>'
            + (f'<div style="color:{C_MUTED};font-size:11px;margin-top:6px;font-style:italic;">'
               f'note: {note_txt}</div>' if note_txt else "")
            + f'<div style="color:{C_MUTED};font-size:11px;margin-top:4px;">{hint}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Forecast preview (always shown) ────────────────────────────────────
        forecast = snap.get("forecast", {}) or {}
        bt_summary = snap.get("backtest_summary", {}) or {}
        starting_equity = bt_summary.get("starting_equity", 0.0)
        config_snap = snap.get("config", {}) or {}

        with st.expander(
            f"📋 Forecast snapshot details — "
            f"$ {starting_equity:,.0f} starting equity",
            expanded=(status in ("warning", "fail", "due")),
        ):
            # Forecast-only view (when not yet validated)
            forecast_rows = [
                {
                    "Metric":        "Total return %",
                    "Forecast (mid)":  f"{forecast.get('expected_total_return_pct', 0.0):+.2f}%",
                    "5/95 CI":         f"{forecast.get('return_pct_ci', [0,0])[0]:+.2f}% to {forecast.get('return_pct_ci', [0,0])[1]:+.2f}%",
                },
                {
                    "Metric":        "Max drawdown %",
                    "Forecast (mid)":  f"{forecast.get('expected_max_drawdown_pct', 0.0):.2f}%",
                    "5/95 CI":         f"{forecast.get('drawdown_pct_ci', [0,0])[0]:.2f}% to {forecast.get('drawdown_pct_ci', [0,0])[1]:.2f}%",
                },
                {
                    "Metric":        "Trades count",
                    "Forecast (mid)":  f"{forecast.get('expected_trades_count', 0.0):.1f}",
                    "5/95 CI":         f"{forecast.get('trades_count_ci', [0,0])[0]:.0f} to {forecast.get('trades_count_ci', [0,0])[1]:.0f}",
                },
                {
                    "Metric":        "Win rate %",
                    "Forecast (mid)":  f"{forecast.get('expected_win_rate_pct', 0.0):.1f}%",
                    "5/95 CI":         "—",
                },
                {
                    "Metric":        "Avg premium yield %",
                    "Forecast (mid)":  f"{forecast.get('expected_avg_premium_yield_pct', 0.0):.3f}%",
                    "5/95 CI":         "—",
                },
                {
                    "Metric":        "Avg P&L / trade $",
                    "Forecast (mid)":  f"${forecast.get('expected_avg_pnl_per_trade_usd', 0.0):+,.2f}",
                    "5/95 CI":         "—",
                },
            ]

            validation = snap.get("validation")
            if validation:
                # ── Validated: add Actual + severity columns ───────────────────
                actual = validation.get("actual", {}) or {}
                findings_by_metric = {
                    f["metric"]: f for f in validation.get("findings", [])
                }
                actual_strs = {
                    "total_return_pct":      f"{actual.get('total_return_pct', 0.0):+.2f}%",
                    "max_drawdown_pct":      f"{actual.get('max_drawdown_pct', 0.0):.2f}%",
                    "trades_count":          f"{actual.get('trades_count', 0)}",
                    "win_rate_pct":          f"{actual.get('win_rate_pct', 0.0):.1f}%",
                    "avg_premium_yield_pct": f"{actual.get('avg_premium_yield_pct', 0.0):.3f}%",
                    "avg_pnl_per_trade_usd": f"${actual.get('avg_pnl_per_trade_usd', 0.0):+,.2f}",
                }
                metric_keys = [
                    "total_return_pct", "max_drawdown_pct", "trades_count",
                    "win_rate_pct", "avg_premium_yield_pct", "avg_pnl_per_trade_usd",
                ]
                for row, key in zip(forecast_rows, metric_keys):
                    row["Actual"] = actual_strs.get(key, "—")
                    finding = findings_by_metric.get(key)
                    if finding:
                        sev = finding["severity"]
                        sev_icon = {"pass": "✅", "warning": "⚠️", "fail": "🔴"}.get(sev, "")
                        row["Δ"] = f"{sev_icon} {sev}"
                    else:
                        row["Δ"] = "—"

                df = pd.DataFrame(forecast_rows)
                st.dataframe(df, use_container_width=True, hide_index=True)

                # Findings list (warning + fail only)
                bad = [
                    f for f in validation.get("findings", [])
                    if f["severity"] in ("warning", "fail")
                ]
                if bad:
                    st.markdown("**Findings (warning/fail only):**")
                    for f in bad:
                        sev = f["severity"]
                        c = C_AMBER if sev == "warning" else C_RED
                        sev_icon = {"warning": "⚠️", "fail": "🔴"}[sev]
                        st.markdown(
                            f'<div style="background:{C_CARD};border-left:3px solid {c};'
                            f'padding:8px 12px;margin-bottom:4px;border-radius:4px;">'
                            f'{sev_icon} <strong style="color:{C_TEXT};font-size:13px;">{f["metric"]}</strong> '
                            f'<span style="color:{C_MUTED};font-size:11px;">[{sev}]</span><br>'
                            f'<span style="color:{C_MUTED};font-size:12px;">{f["message"]}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                else:
                    st.success(
                        "All metrics inside forecast envelope — "
                        "the backtest was a faithful predictor over this window."
                    )

                # Sample size + validated-at footer
                paper_n = actual.get("paper_trades", 0)
                real_n  = actual.get("real_trades", 0)
                ending  = actual.get("ending_equity", starting_equity)
                validated_at = validation.get("validated_at", "")[:19].replace("T", " ")
                st.caption(
                    f"Validated {validated_at} · "
                    f"{paper_n} paper · {real_n} live/testnet · "
                    f"ending equity ${ending:,.2f}"
                )
            else:
                # Not yet validated — show forecast only
                df = pd.DataFrame(forecast_rows)
                st.dataframe(df, use_container_width=True, hide_index=True)
                if status == "due":
                    st.info(
                        "Horizon has elapsed — click **Validate due** above "
                        "(or run `python forecast_validator.py validate`) to "
                        "compare actuals."
                    )
                else:
                    # Show how much time remains
                    try:
                        from datetime import datetime as _dt, timezone as _tz
                        va = _dt.fromisoformat(snap.get("validate_after", ""))
                        if va.tzinfo is None:
                            va = va.replace(tzinfo=_tz.utc)
                        remaining = va - _dt.now(_tz.utc)
                        days_left = remaining.days
                        st.caption(f"⏳ Validates in ~{max(days_left, 0)} day(s).")
                    except Exception:
                        pass

            # ── Capital efficiency context ────────────────────────────────────
            # The backtest_summary now carries margin-ROI / min-viable-capital
            # / premium-on-margin / margin-utilization (added 2026-05-01 in
            # forecast_validator.create_snapshot). These answer the user's
            # "small capital × high ROI" thesis, so surface them as a small
            # info row beneath the forecast vs actual table.
            cap_metrics = {
                "Min viable capital": (
                    f"${bt_summary.get('min_viable_capital', 0.0):,.0f}"
                    if bt_summary.get('min_viable_capital') else "—"
                ),
                "Margin ROI / yr": (
                    f"{bt_summary.get('annualised_margin_roi', 0.0) * 100:+.0f}%"
                    if bt_summary.get('annualised_margin_roi') else "—"
                ),
                "Premium / margin": (
                    f"{bt_summary.get('premium_on_margin', 0.0) * 100:.1f}%"
                    if bt_summary.get('premium_on_margin') else "—"
                ),
                "Avg margin util": (
                    f"{bt_summary.get('avg_margin_utilization', 0.0) * 100:.0f}%"
                    if bt_summary.get('avg_margin_utilization') else "—"
                ),
            }
            if any(v != "—" for v in cap_metrics.values()):
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_GRID};'
                    f'border-radius:6px;padding:10px 14px;margin-top:8px;">'
                    f'<div style="color:{C_MUTED};font-size:11px;'
                    f'text-transform:uppercase;letter-spacing:0.5px;'
                    f'margin-bottom:6px;">Capital efficiency (from backtest)</div>'
                    + "".join(
                        f'<div style="display:inline-block;margin-right:24px;">'
                        f'<span style="color:{C_MUTED};font-size:11px;">{k}: </span>'
                        f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;">{v}</span>'
                        f'</div>'
                        for k, v in cap_metrics.items()
                    )
                    + '</div>',
                    unsafe_allow_html=True,
                )

            # Config snapshot (for drift detection)
            if config_snap:
                with st.expander("⚙️ Config when snapshot was taken", expanded=False):
                    st.json(config_snap)


def tab_settings() -> None:
    st.markdown("### 🔧 Settings")

    # ── Live Connection ────────────────────────────────────────────────────────
    st.markdown("#### 🔌 Live Connection")

    col_env, col_btn = st.columns([1, 1])
    with col_env:
        conn_env = st.radio(
            "Environment", ["Mainnet", "Testnet"],
            horizontal=True, key="preflight_env",
        )
    use_testnet = conn_env == "Testnet"

    with col_btn:
        st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
        run_checks = st.button("🔍 Run Pre-flight Checks", use_container_width=True)

    if run_checks:
        try:
            from preflight import run_preflight
            check_results: list = []

            def _on_check(result) -> None:
                check_results.append(result)

            with st.spinner("Checking connectivity and credentials…"):
                report = run_preflight(
                    testnet=use_testnet, bot_dir=BOT_DIR, on_check=_on_check
                )

            for result in report.checks:
                icon   = "✅" if result.passed else "❌"
                border = C_GREEN if result.passed else C_RED
                detail_html = (
                    f'<br><span style="color:{C_MUTED};font-size:11px;">{result.detail}</span>'
                    if result.detail else ""
                )
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {border};'
                    f'border-radius:6px;padding:10px 14px;margin-bottom:6px;">'
                    f'{icon} <strong style="color:{C_TEXT};font-size:14px;">{result.name}</strong>'
                    f'<br><span style="color:{C_MUTED};font-size:12px;">{result.message}</span>'
                    f'{detail_html}</div>',
                    unsafe_allow_html=True,
                )

            if report.ready_for_live or report.ready_for_testnet:
                env_label = "testnet" if use_testnet else "live"
                st.success(f"🟢 Ready for {env_label} trading — all critical checks passed.")
                st.code(
                    f"python main.py --mode={'testnet' if use_testnet else 'live'}",
                    language="bash",
                )
            else:
                st.error("🔴 Not ready — fix the failures above before trading.")
        except ImportError:
            st.warning("preflight.py not found in bot directory.")
        except Exception as exc:
            st.error(f"Pre-flight check error: {exc}")

    st.divider()

    # ── Pause Trading ──────────────────────────────────────────────────────────
    # Renamed from "Kill Switch" 2026-05-03 to match the mobile app's
    # Pause/Resume verbiage. Mechanism unchanged (KILL_SWITCH file blocks new
    # trade entries on every bot; bot processes keep running).
    st.markdown("#### Pause Trading (all bots)")
    if kill_switch_active():
        ks_path = BOT_DIR / "KILL_SWITCH"
        try:
            ks_msg = ks_path.read_text().strip()
        except Exception:
            ks_msg = "(no message)"
        st.error(f"⏸ **Trading is PAUSED**\n\n```\n{ks_msg}\n```")
        st.caption("Open positions still settle naturally. Only new entries are blocked.")
        if st.button("▶ Resume Trading", type="primary", use_container_width=True):
            clear_kill_switch()
            st.success("Trading resumed. Bots can open new positions.")
            st.rerun()
    else:
        st.success("✅ Trading is active across all bots.")
        if st.button("⏸ Pause Trading", type="secondary", use_container_width=True,
                     help="Blocks new trade entries on every bot. Existing positions still settle. Bot processes keep running."):
            (BOT_DIR / "KILL_SWITCH").write_text(
                f"Paused from Settings tab at {datetime.utcnow().isoformat()}\n"
                "Delete this file to resume trading."
            )
            st.rerun()

    st.divider()

    # ── Log Viewer ─────────────────────────────────────────────────────────────
    st.markdown("#### Log Viewer")
    log_dir = BOT_DIR / "logs"
    logs = (
        sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if log_dir.exists() else []
    )
    if logs:
        log_names = [p.name for p in logs]
        selected_log = st.selectbox("Select log file", log_names, key="settings_log_file")
        log_path = log_dir / selected_log
        n_lines = st.slider("Lines to show", min_value=20, max_value=200, step=20, value=50, key="settings_log_lines")
        try:
            lines = log_path.read_text().splitlines()[-n_lines:]
            st.code("\n".join(lines), language=None)
        except Exception as e:
            st.error(f"Could not read log: {e}")
    else:
        st.info("No log files found yet. Start the bot to generate logs.")

    st.divider()

    # ── Trades CSV ─────────────────────────────────────────────────────────────
    st.markdown("#### Trades Data")
    trades_df = read_trades()
    if trades_df.empty:
        st.info("No trades recorded yet.")
    else:
        st.success(f"{len(trades_df)} trades in `data/trades.csv`")
        csv_bytes = trades_df.to_csv(index=False).encode()
        st.download_button(
            label="⬇️ Download trades.csv",
            data=csv_bytes,
            file_name=f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        with st.expander("⚠️ Clear Trades (Danger Zone)", expanded=False):
            st.warning("This will permanently delete `data/trades.csv`. This cannot be undone.")
            confirm = st.checkbox("I understand — delete all trade history")
            if confirm:
                if st.button("🗑️ Delete trades.csv", type="secondary", use_container_width=True):
                    csv_path = BOT_DIR / "data" / "trades.csv"
                    if csv_path.exists():
                        csv_path.unlink()
                    st.success("trades.csv deleted.")
                    st.rerun()

    st.divider()

    # ── About ──────────────────────────────────────────────────────────────────
    st.markdown("#### About")
    st.markdown(
        f'<div style="background:{C_CARD};border:1px solid {C_GRID};border-radius:8px;padding:16px 18px;">'
        f'<p style="color:{C_TEXT};margin:0 0 8px 0;font-size:14px;"><strong>BTC Wheel Bot</strong> — '
        f'Automated options wheel strategy on Deribit</p>'
        f'<p style="color:{C_MUTED};margin:0 0 8px 0;font-size:12px;line-height:1.6;">'
        f'Alternates between selling OTM puts and covered calls on BTC perpetuals. '
        f'Uses IV rank filtering, Black-Scholes delta calculation, and a genetic optimizer '
        f'to continuously improve parameters.</p>'
        f'<p style="color:{C_MUTED};margin:0;font-size:12px;">'
        f'Python {sys.version[:6]} · Streamlit {st.__version__} · '
        f'<a href="https://github.com/banksiasprings/btc-wheel-bot" '
        f'style="color:{C_BLUE};">GitHub ↗</a>'
        f'</p></div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    render_sidebar()

    # ── JS patch: BaseWeb hard-codes overflow-y:hidden on the tab-list which clips
    # the button tops.  We override it after each render via a MutationObserver so
    # it re-applies whenever Streamlit re-renders the component.
    st.markdown("""
<script>
(function patchTabOverflow() {
    function fix() {
        const tl = document.querySelector('[data-baseweb="tab-list"]');
        if (tl) {
            tl.style.setProperty('overflow', 'visible', 'important');
            tl.style.setProperty('padding-top', '10px', 'important');
        }
    }
    fix();
    new MutationObserver(fix).observe(document.body, { childList: true, subtree: true });
})();
</script>
""", unsafe_allow_html=True)

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "📊 Backtest",
        "📈 Paper Trading",
        "🛰 Fleet",
        "🧬 Optimizer",
        "📋 Recommendations",
        "📊 Forecasts",
        "⚙️ Config",
        "🔧 Settings",
    ])
    with tab1:
        tab_backtest()
    with tab2:
        tab_paper()
    with tab3:
        tab_fleet()
    with tab4:
        tab_optimizer()
    with tab5:
        tab_recommendations()
    with tab6:
        tab_forecasts()
    with tab7:
        tab_config()
    with tab8:
        tab_settings()


if __name__ == "__main__":
    main()
