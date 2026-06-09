"""
steven_portfolio.py — Steven's manual portfolio (the human-vs-algo tournament book).

Steven hand-picks bots from the whole universe (Freyr + farm + specialists) and
sets each to ON / OFF / AUTO. This module persists that choice server-side, logs
every flip to an audit trail, and steps a paper NAV ($10k start — the same notional
as every farm bot) forward each farm tick, so his pattern-matching can be scored
head-to-head against the Freyr auto-portfolios.

It is intentionally pure: it knows nothing about Deribit or Freyr's file layout.
api.py builds the bot "universe" (each bot's current equity + whether its own gate
is firing) and hands it in. That keeps this module testable and import-cycle-free.

Files (all runtime, regenerated live — gitignored under paper/):
  paper/steven_portfolio.json          config: included bots, overrides, weights
  paper/steven_portfolio_state.json    sim state: per-slice $ value, last equity, peak
  paper/steven_portfolio_equity.csv    NAV history (+ Freyr benchmarks, aligned from t0)
  paper/manual_overrides/steven_portfolio.jsonl   append-only audit log of every flip

Tick model (recompute-incremental — mirrors the per-bot state.json pattern):
  - Each included bot owns a dollar "slice". NAV = sum(slices).
  - Per tick a slice grows by its bot's realised per-tick return ONLY when active —
    active = override ON, or override AUTO and the bot's own gate is firing. An
    inactive slice holds flat (parked in cash). An honest paper sim of Steven's call
    "deploy this" vs "park this in cash".
  - On a membership change, rebalance to the configured weights (equal by default),
    preserving total NAV — funding a new pick carves equally from the rest; dropping
    one redistributes its value to the survivors.
  - The tick is idempotent per farm `updated` stamp, so it advances exactly once per
    farm tick no matter how many page loads or widget polls drive it.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PAPER = BASE_DIR / "paper"
CONFIG = PAPER / "steven_portfolio.json"
STATE = PAPER / "steven_portfolio_state.json"
EQUITY = PAPER / "steven_portfolio_equity.csv"
AUDIT = PAPER / "manual_overrides" / "steven_portfolio.jsonl"

INITIAL_NAV = 10_000.0          # same starting stake as every farm bot, for a fair race
VALID_OVERRIDES = {"ON", "OFF", "AUTO"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


# ── config (which bots are in, and each one's override) ───────────────────────

def load_config() -> dict:
    cfg = _read_json(CONFIG, None) or {}
    cfg.setdefault("initial_nav", INITIAL_NAV)
    cfg.setdefault("bots", {})        # key -> {"override","weight","name"}
    cfg.setdefault("updated", "")
    return cfg


def save_config(cfg: dict) -> dict:
    cfg["updated"] = _now_iso()
    PAPER.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    return cfg


def _audit(action: str, key: str, name: str, frm, to) -> None:
    """Append one line to the override audit log — the data we use later to judge
    whether Steven's manual gating actually beat the algorithmic gates."""
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a") as f:
        f.write(json.dumps({"ts": _now_iso(), "action": action, "bot": key,
                            "name": name, "from": frm, "to": to}) + "\n")


def add(key: str, name: str = "") -> dict:
    cfg = load_config()
    if key not in cfg["bots"]:
        cfg["bots"][key] = {"override": "AUTO", "weight": 1.0, "name": name}
        save_config(cfg)
        _audit("add", key, name, None, "AUTO")
    return cfg


def remove(key: str) -> dict:
    cfg = load_config()
    b = cfg["bots"].pop(key, None)
    if b is not None:
        save_config(cfg)
        _audit("remove", key, b.get("name", ""), b.get("override"), None)
    return cfg


def set_override(key: str, override: str, name: str = "") -> dict:
    override = (override or "").upper()
    if override not in VALID_OVERRIDES:
        raise ValueError(f"bad override {override!r}")
    cfg = load_config()
    b = cfg["bots"].get(key)
    frm = b["override"] if b else None
    if b is None:
        cfg["bots"][key] = {"override": override, "weight": 1.0, "name": name}
    else:
        b["override"] = override
        if name:
            b["name"] = name
    save_config(cfg)
    _audit("override", key, name, frm, override)
    return cfg


# ── the paper NAV simulation ──────────────────────────────────────────────────

def _blank_state(cfg: dict) -> dict:
    return {"slices": {}, "last_eq": {}, "peak": cfg["initial_nav"], "last_updated": ""}


def tick(universe: dict, btc_price: float, updated: str,
         benchmarks: dict | None = None) -> dict:
    """Advance Steven's NAV by one farm tick. Idempotent per `updated` stamp.

    universe: {key: {"equity": $, "active": bool, "name": str}} — the live roster.
    benchmarks: {col_name: equity} recorded as extra CSV columns so the head-to-head
                chart is aligned to Steven's curve from t0 (e.g. the Freyr variants).
    Returns the snapshot dict (also persisted)."""
    cfg = load_config()
    state = _read_json(STATE, None) or _blank_state(cfg)
    benchmarks = benchmarks or {}

    included = cfg["bots"]
    want = set(included.keys())
    have = set(state.get("slices", {}).keys())

    # 1. membership change → rebalance to weights, preserving total NAV. Done on
    #    EVERY call (not just per farm tick) so a freshly-added pick is funded the
    #    moment Steven adds it, rather than showing $0 until the next hourly tick.
    membership_changed = want != have
    if membership_changed:
        nav = sum(state["slices"].values()) if state["slices"] else cfg["initial_nav"]
        wsum = sum(max(included[k].get("weight", 1.0), 0.0) for k in want) or 1.0
        state["slices"] = {k: nav * max(included[k].get("weight", 1.0), 0.0) / wsum
                           for k in want}
        for k in want:                               # seed last-seen equity for new bots
            state["last_eq"].setdefault(k, (universe.get(k) or {}).get("equity", cfg["initial_nav"]))
        state["last_eq"] = {k: v for k, v in state["last_eq"].items() if k in want}
        PAPER.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=2))

    # Returns are stepped and a NAV point recorded at most once per farm tick (the
    # `updated` stamp). Re-stepping on every page load would double-count the hour.
    if not updated or updated == state.get("last_updated"):
        return snapshot(cfg, state, universe)

    # 2. step each slice by its bot's realised return — only while active
    for k in want:
        u = universe.get(k)
        if not u:                                    # bot missing this tick → hold flat
            continue
        eq = float(u.get("equity", 0.0) or 0.0)
        last = state["last_eq"].get(k, eq) or eq
        r = (eq / last) if last > 0 else 1.0
        ov = included[k].get("override", "AUTO")
        active = (ov == "ON") or (ov == "AUTO" and bool(u.get("active", False)))
        if active and r > 0:
            state["slices"][k] = state["slices"].get(k, 0.0) * r
        state["last_eq"][k] = eq

    nav = sum(state["slices"].values()) if want else cfg["initial_nav"]
    state["peak"] = max(state.get("peak", nav) or nav, nav)
    state["last_updated"] = updated or _now_iso()

    PAPER.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))

    dd = (nav / state["peak"] - 1) * 100 if state["peak"] > 0 else 0.0
    new_file = not EQUITY.exists()
    cols = ["timestamp", "btc_price", "equity"] + list(benchmarks.keys())
    with EQUITY.open("a", newline="") as f:
        wr = csv.writer(f)
        if new_file:
            wr.writerow(cols)
        wr.writerow([state["last_updated"], f"{btc_price:.2f}", f"{nav:.2f}",
                     *[f"{benchmarks[k]:.2f}" for k in benchmarks]])
    return snapshot(cfg, state, universe)


def snapshot(cfg: dict | None = None, state: dict | None = None,
             universe: dict | None = None) -> dict:
    """Point-in-time summary of Steven's portfolio for the UI (no stepping)."""
    cfg = cfg or load_config()
    state = state or _read_json(STATE, None) or _blank_state(cfg)
    universe = universe or {}
    slices = state.get("slices", {})
    nav = sum(slices.values()) if slices else cfg["initial_nav"]
    peak = max(state.get("peak", nav) or nav, nav)
    dd = (nav / peak - 1) * 100 if peak > 0 else 0.0

    bots, n_active = [], 0
    for k, b in cfg["bots"].items():
        u = universe.get(k, {})
        ov = b.get("override", "AUTO")
        gate = bool(u.get("active", False))
        active = (ov == "ON") or (ov == "AUTO" and gate)
        n_active += int(active)
        bots.append({"key": k, "name": b.get("name") or u.get("name") or k,
                     "override": ov, "gate_active": gate, "active": active,
                     "value": slices.get(k, 0.0)})
    return {"nav": nav, "initial_nav": cfg["initial_nav"],
            "return_pct": (nav / cfg["initial_nav"] - 1) * 100,
            "drawdown_pct": dd, "peak": peak,
            "n_bots": len(cfg["bots"]), "n_active": n_active,
            "bots": bots, "updated": state.get("last_updated", "")}


def equity_rows() -> list[tuple[datetime, float]]:
    """[(datetime, nav)] ascending — Steven's NAV history for the equity curve."""
    rows: list[tuple[datetime, float]] = []
    if not EQUITY.exists():
        return rows
    try:
        with EQUITY.open() as f:
            for r in csv.DictReader(f):
                try:
                    rows.append((datetime.fromisoformat(r["timestamp"]), float(r["equity"])))
                except Exception:
                    continue
    except Exception:
        pass
    return rows


def benchmark_series() -> dict[str, list[tuple[datetime, float]]]:
    """{column: [(datetime, equity)]} for every series in the equity CSV (Steven's
    own 'equity' plus each recorded Freyr benchmark) — aligned, for the overlay."""
    out: dict[str, list[tuple[datetime, float]]] = {}
    if not EQUITY.exists():
        return out
    try:
        with EQUITY.open() as f:
            rd = csv.DictReader(f)
            cols = [c for c in (rd.fieldnames or []) if c not in ("timestamp", "btc_price")]
            for c in cols:
                out[c] = []
            for r in rd:
                try:
                    t = datetime.fromisoformat(r["timestamp"])
                except Exception:
                    continue
                for c in cols:
                    try:
                        out[c].append((t, float(r[c])))
                    except Exception:
                        pass
    except Exception:
        return {}
    return out


if __name__ == "__main__":  # tiny self-test on a synthetic universe
    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp())
    globals().update(PAPER=tmp, CONFIG=tmp / "c.json", STATE=tmp / "s.json",
                     EQUITY=tmp / "e.csv", AUDIT=tmp / "m" / "a.jsonl")
    add("alpha", "Alpha"); set_override("alpha", "ON", "Alpha")
    uni = {"alpha": {"equity": 10000, "active": True, "name": "Alpha"}}
    tick(uni, 65000, "2026-06-09T00:00:00+00:00")
    uni["alpha"]["equity"] = 11000                      # +10%
    snap = tick(uni, 66000, "2026-06-09T01:00:00+00:00")
    assert abs(snap["nav"] - 11000) < 1, snap["nav"]
    set_override("alpha", "OFF")                        # park it
    uni["alpha"]["equity"] = 12000                      # bot keeps moving, but we're out
    snap = tick(uni, 67000, "2026-06-09T02:00:00+00:00")
    assert abs(snap["nav"] - 11000) < 1, snap["nav"]    # held flat in cash
    print("OK", snap["nav"], snap["return_pct"], "audit lines:",
          len((tmp / "m" / "a.jsonl").read_text().splitlines()))
    shutil.rmtree(tmp)
