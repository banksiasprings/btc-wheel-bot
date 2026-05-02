# CONTEXT.md

> **Maintenance contract:** Read this first in any new session — it is the canonical anchor for what this project is and how it is shaped. Update it whenever the architecture, core strategy, or operating model changes meaningfully. **This file takes priority over README.md** when they disagree; the README is user-facing setup, this is the design rationale. If you make a structural change without updating CONTEXT.md, you have created doc rot.

## What this project is

A Bitcoin options **wheel-strategy** trading bot for Deribit. Sells short-dated OTM puts and calls to harvest theta and IV decay, alternating sides to stay roughly delta-neutral. The system runs a **farm of independently-configured bots** in parallel, each exploring a different parameterisation of the same core strategy. Currently in **paper-trading phase** — no real capital at risk.

The goal once live: **$5/day of net real profit, sustained**, before scaling. That number is a deliberate floor — small enough to prove the loop works, large enough that the cost structure (Deribit fees, slippage, infra) cannot eat it.

## Core strategy shape

Sell premium when implied vol is rich (IV rank above a threshold), pick a strike inside a delta band (~0.15–0.40), close or roll on adverse delta or unrealised loss, otherwise let it expire. Each bot is a different point in the parameter space (IV threshold, delta band, sizing, DTE preference, hedging policy). The farm exists because we don't know which configuration is best — we let many run, observe, and gate the best ones toward live.

## Key architectural decisions (and why)

**Three independent processes, file-based IPC.** Bots, the FastAPI server, and the dashboard each run standalone and coordinate by reading and writing files (heartbeat JSON, trades CSV, kill-switch sentinel files). No message queue, no pub/sub. *Why:* the system has very low coordination needs and crashes must not cascade — a missing file is a self-healing state, a broken queue is an outage. The simplicity is the feature.

**Kill switches are sentinel files, not RPC calls.** A global `KILL_SWITCH` halts the whole farm; per-bot `KILL_SWITCH` files in each bot's farm subdirectory pause individual bots. *Why:* file presence is unambiguous, survives restarts, and can be triggered by anything (including `touch` from a phone over SSH if the API is down).

**Each bot owns an isolated farm subdirectory.** Bots cannot read each other's data, configs, or state. *Why:* one buggy config must not corrupt another; readiness gating must operate on independent track records.

**Configs are first-class objects with a status lifecycle.** A config moves through draft → paper → ready → live (and back to archived). The farm supervisor discovers which bots to run by querying configs with `status=paper`. *Why:* promotion to live must be a deliberate, gated transition driven by an 8-check readiness validator, not an ad-hoc edit.

**Genetic optimiser searches the parameter space offline.** New configs are bred from backtest fitness, then dropped into the farm at paper status to validate forward. *Why:* backtest overfitting is real; paper trading is the second filter before any config touches real capital.

**FastAPI + mobile PWA over Cloudflare Tunnel.** The API exposes JSON endpoints and is consumed by a React PWA reachable at `bot.banksiaspringsfarm.com`. *Why:* Steven runs everything from his phone — every interaction, alert, and control surface must work on a small screen first. The desktop dashboard is secondary.

**Cross-file data contracts are written down, not inferred.** Heartbeat schema, trades.csv columns, config.yaml sections, and the Position dataclass are documented in SKILL.md because changes to one file silently break others. *Why:* this has already cost real debugging time. Treat the schema doc as load-bearing.

## Currently in progress

- Paper trading across the farm (~15 bot configs at varying parameter settings) — accumulating the trade history needed to satisfy the readiness gate.
- Mobile PWA polish — Trading tab, hedge sub-cards, P&L visualisation. The PWA is the primary operating surface.
- Optimiser-driven config breeding — new candidates land at paper status and run alongside hand-crafted configs.

## Out of scope (for now)

- **Live trading.** Not until at least one config clears the readiness gate and demonstrates the $5/day target on paper.
- **Multi-asset.** BTC only. ETH, SOL, etc. are not on the roadmap until BTC works.
- **ML-driven strategy decisions.** `ML_HOOK` stubs exist in the code but are intentionally inert — the rule-based strategy must work first.
- **Self-hosted infra beyond the Mac + Cloudflare Tunnel.** Cloud migration has been considered (see CLOUD_MIGRATION.md) but is not a current priority; the local-Mac-plus-tunnel setup is sufficient for paper.
- **Anything that hides risk.** Backwards-compatibility shims, fallback values for missing config keys, or silent error swallowing are not welcome — failures must be loud.
