# BTC Wheel Bot — Night Log

Autonomous overnight session started 2026-05-01 ~22:15 Brisbane / 12:15 UTC.

## Constraints (do not violate)

- Paper bot PID 19888 must keep running. **Do not kill it.**
- Three scheduled remote routines must remain enabled. **Do not delete them.**
- No config.yaml changes that alter strategy behaviour without explicit user approval.
- pytest must stay green after every commit.

## Round plan

| Round | Theme | Why |
|---|---|---|
| 1 | Surface capital-efficiency metrics in UI | User's explicit ask — backend has data, UI hides it |
| 2 | Honest fitness + hedge cost | Replace `capital_roi` scorer; triple hedge funding to match Deribit reality |
| 3 | Wipe stale optimizer cache | Pre-fix evolutions are invalid; document the wipe |
| 4 | Audit-class bug hunt in unread files (api.py, hedge_manager.py, ai_overseer.py, bot_farm.py) | Audit covered the core; these may hide more |
| 5 | Test coverage gap-fill (hedge, order_tracker slippage cap, stranded-position integration) | Lock in the audit fixes |
| 6 | Operations runbook (`OPERATIONS.md`) | Encode the lessons learned for future Claude/human readers |

Round budget: aim for 1.5–2 hours per round. Stop and write Checkpoint Summary at the end of each.

## Verification rule

Every commit must follow:
1. ✅ `python3.11 -m pytest tests/ -q` passes
2. ✅ Affected files import cleanly (`python3.11 -c "import <module>"`)
3. ✅ The change directly serves the round's theme — no scope creep

If a change requires user judgment (strategy params, irreversible ops, anything that could lose money), defer and log under "Deferred — needs user review."

---
