"""
ai_overseer.py — LLM-based oversight agent for the BTC Wheel Bot.

The AI overseer is a safety layer, NOT a trader. It runs on a slow cadence
(configurable: hourly, or triggered by key events) and makes one binary
decision: CONTINUE or HALT. If it says HALT, the bot's KILL_SWITCH file is
written and all trading stops until a human reviews and removes it.

The LLM receives a structured market brief — it never has access to API keys
or order placement. It is a read-only auditor.

Supported LLM backends:
  - Google Gemini Flash (free tier) — recommended default
  - Anthropic Claude (via claude-haiku — fast, cheap)
  - OpenAI GPT-4o-mini

Configure in config.yaml under `overseer:` section, or leave disabled.

Key design decisions:
  - Deterministic rules (strategy.py / risk_manager.py) handle every trade
  - LLM only intervenes at the "manager" level — pattern recognition over time
  - All LLM decisions are logged with full reasoning
  - Conservative default: LLM can only HALT, never override risk_manager to ALLOW
  - If LLM errors or times out, bot continues (fail-open for uptime)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from loguru import logger

# ── Decision model ─────────────────────────────────────────────────────────────


Decision = Literal["CONTINUE", "HALT"]


@dataclass
class MarketBrief:
    """Structured summary passed to the LLM for its assessment."""
    timestamp_utc: str
    # Equity state
    starting_equity: float
    current_equity: float
    total_return_pct: float
    peak_equity: float
    current_drawdown_pct: float
    # Recent trades
    total_cycles: int
    win_rate_pct: float
    consecutive_losses: int
    last_5_pnls: list[float]        # most recent first
    # Market context
    current_btc_price: float
    btc_change_7d_pct: float
    current_iv: float
    iv_rank: float
    # Open position (if any)
    has_open_position: bool
    open_position_type: str          # "put" | "call" | "none"
    open_position_strike: float
    open_position_delta: float
    open_position_unrealised_pnl: float
    open_position_dte: int
    # Risk flags
    drawdown_warning: bool           # within 50% of max_drawdown limit
    consecutive_loss_warning: bool   # 3+ losses in a row
    iv_spike_warning: bool           # IV rank > 0.85
    low_capital_warning: bool        # free (unreserved) equity < min_free_equity_fraction
    free_equity_pct: float           # % of account currently unencumbered


@dataclass
class OverseerDecision:
    timestamp_utc: str
    decision: Decision
    confidence: str                  # "HIGH" | "MEDIUM" | "LOW"
    reasoning: str
    key_concerns: list[str]
    recommended_actions: list[str]
    backend_used: str


# ── LLM backends ───────────────────────────────────────────────────────────────


class GeminiBackend:
    """Google Gemini Flash backend (free tier, ~1M tokens/day)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._model = "gemini-1.5-flash"

    def complete(self, prompt: str) -> str:
        import requests
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent?key={self._api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,    # low temp for consistent structured output
                "maxOutputTokens": 512,
            },
        }
        resp = requests.post(url, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


class AnthropicBackend:
    """Anthropic Claude Haiku backend (fast, cheap, very reliable)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._model = "claude-haiku-4-5-20251001"

    def complete(self, prompt: str) -> str:
        import requests
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self._model,
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]


class OpenAIBackend:
    """OpenAI GPT-4o-mini backend."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def complete(self, prompt: str) -> str:
        import requests
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
            "temperature": 0.2,
        }
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ── Prompt builder ─────────────────────────────────────────────────────────────


def build_oversight_prompt(brief: MarketBrief) -> str:
    """
    Build the structured prompt sent to the LLM.

    The prompt is deliberately concise and forces structured JSON output
    so we can parse the decision reliably without natural language ambiguity.
    """
    brief_json = json.dumps(asdict(brief), indent=2)

    return f"""You are an AI trading overseer for a Bitcoin options wheel bot.
Your job is NOT to pick trades — the bot's deterministic rules do that.
Your job is to review the bot's current state and decide: should it CONTINUE trading, or should it HALT?

HALT means writing a kill switch file. This is a significant action.
Only HALT for serious, sustained problems — not normal volatility or a single bad week.

Good reasons to HALT:
- Drawdown is accelerating toward the limit (3+ consecutive losses AND drawdown > 7%)
- Extreme IV spike (rank > 0.90) combined with an open losing position
- Equity is below 85% of starting equity
- The strategy has stopped working: win rate below 50% over last 10 cycles
- low_capital_warning is true AND there is already an open losing position (double jeopardy)

Good reasons to CONTINUE:
- One or two assignments in a row (normal for a wheel strategy)
- IV is elevated but within normal bounds
- Equity is up overall, drawdown is manageable
- low_capital_warning is true but the open position is profitable (capital is productively deployed)

Current bot state:
{brief_json}

Respond ONLY with valid JSON in exactly this format:
{{
  "decision": "CONTINUE" or "HALT",
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "reasoning": "2-3 sentence explanation of your decision",
  "key_concerns": ["concern1", "concern2"],
  "recommended_actions": ["action1", "action2"]
}}

Do not include any text outside the JSON block."""


# ── Main overseer class ────────────────────────────────────────────────────────


class AIOverSeer:
    """
    Periodic LLM-based oversight agent.

    Usage in bot.py:
        overseer = AIOverSeer()
        if not overseer.check():       # returns False = HALT
            self._stop()
    """

    def __init__(self) -> None:
        self._backend = self._init_backend()
        self._decision_log: list[OverseerDecision] = []
        self._log_path = Path("logs/overseer_decisions.jsonl")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_backend(self):
        """Auto-detect which LLM backend to use based on available env vars."""
        if key := os.getenv("GEMINI_API_KEY"):
            logger.info("AI Overseer: using Google Gemini Flash backend")
            return GeminiBackend(key)
        elif key := os.getenv("ANTHROPIC_API_KEY"):
            logger.info("AI Overseer: using Anthropic Claude Haiku backend")
            return AnthropicBackend(key)
        elif key := os.getenv("OPENAI_API_KEY"):
            logger.info("AI Overseer: using OpenAI GPT-4o-mini backend")
            return OpenAIBackend(key)
        else:
            logger.warning(
                "AI Overseer: no LLM API key found "
                "(GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY). "
                "Overseer will be disabled — set one to enable."
            )
            return None

    def is_enabled(self) -> bool:
        return self._backend is not None

    def build_brief(
        self,
        equity_curve: list[float],
        trades: list[dict],
        current_btc_price: float,
        btc_change_7d_pct: float,
        current_iv: float,
        iv_rank: float,
        open_position: dict | None = None,
    ) -> MarketBrief:
        """Build a MarketBrief from bot state for LLM consumption."""
        starting_equity = equity_curve[0] if equity_curve else 10000.0
        current_equity = equity_curve[-1] if equity_curve else starting_equity
        peak_equity = max(equity_curve) if equity_curve else starting_equity
        total_return_pct = (current_equity - starting_equity) / starting_equity * 100
        drawdown_pct = (peak_equity - current_equity) / peak_equity * 100 if peak_equity > 0 else 0

        # Win rate and consecutive losses
        pnls = [t.get("pnl_usd", 0) for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        win_rate = (wins / len(pnls) * 100) if pnls else 100.0
        last_5 = pnls[-5:] if len(pnls) >= 5 else pnls

        consecutive_losses = 0
        for p in reversed(pnls):
            if p < 0:
                consecutive_losses += 1
            else:
                break

        from config import cfg

        # Free capital: equity minus notional collateral locked in open positions
        pos = open_position or {}
        reserved = 0.0
        if open_position:
            strike = pos.get("strike", 0)
            contracts = pos.get("contracts", 1)
            reserved = strike * contracts * cfg.sizing.contract_size_btc
        free_equity = max(current_equity - reserved, 0.0)
        free_equity_pct = (free_equity / current_equity * 100) if current_equity > 0 else 0.0
        low_capital = free_equity_pct < (cfg.sizing.min_free_equity_fraction * 100)

        return MarketBrief(
            timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
            starting_equity=starting_equity,
            current_equity=round(current_equity, 2),
            total_return_pct=round(total_return_pct, 2),
            peak_equity=round(peak_equity, 2),
            current_drawdown_pct=round(drawdown_pct, 2),
            total_cycles=len(trades),
            win_rate_pct=round(win_rate, 1),
            consecutive_losses=consecutive_losses,
            last_5_pnls=[round(p, 2) for p in last_5],
            current_btc_price=round(current_btc_price, 0),
            btc_change_7d_pct=round(btc_change_7d_pct, 2),
            current_iv=round(current_iv, 1),
            iv_rank=round(iv_rank, 3),
            has_open_position=open_position is not None,
            open_position_type=pos.get("option_type", "none"),
            open_position_strike=pos.get("strike", 0),
            open_position_delta=pos.get("delta", 0),
            open_position_unrealised_pnl=pos.get("unrealised_pnl", 0),
            open_position_dte=pos.get("dte", 0),
            drawdown_warning=drawdown_pct > (cfg.risk.max_daily_drawdown * 50),
            consecutive_loss_warning=consecutive_losses >= 3,
            iv_spike_warning=iv_rank > 0.85,
            low_capital_warning=low_capital,
            free_equity_pct=round(free_equity_pct, 1),
        )

    def check(self, brief: MarketBrief) -> bool:
        """
        Run LLM oversight check.

        Returns:
            True  = safe to continue trading
            False = LLM says HALT (kill switch will be written by caller)

        Fail-open: if LLM errors/times out, returns True (bot continues).
        This prevents a flaky API from permanently halting a working bot.
        """
        if not self.is_enabled():
            return True  # overseer disabled, always continue

        prompt = build_oversight_prompt(brief)

        try:
            raw = self._backend.complete(prompt)

            # Strip markdown code fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            parsed = json.loads(raw)
            decision_str: Decision = parsed.get("decision", "CONTINUE")

            decision = OverseerDecision(
                timestamp_utc=brief.timestamp_utc,
                decision=decision_str,
                confidence=parsed.get("confidence", "LOW"),
                reasoning=parsed.get("reasoning", ""),
                key_concerns=parsed.get("key_concerns", []),
                recommended_actions=parsed.get("recommended_actions", []),
                backend_used=type(self._backend).__name__,
            )
            self._log_decision(decision)

            if decision_str == "HALT":
                logger.critical(
                    f"AI OVERSEER DECISION: HALT | confidence={decision.confidence}\n"
                    f"Reasoning: {decision.reasoning}\n"
                    f"Concerns: {', '.join(decision.key_concerns)}"
                )
                self._write_kill_switch(decision.reasoning)
                return False
            else:
                logger.info(
                    f"AI Overseer: CONTINUE ({decision.confidence} confidence) — "
                    f"{decision.reasoning[:80]}..."
                )
                return True

        except json.JSONDecodeError as exc:
            logger.warning(f"AI Overseer: could not parse LLM response: {exc}. Continuing.")
            return True
        except Exception as exc:
            logger.warning(f"AI Overseer: LLM call failed: {exc}. Failing open (CONTINUE).")
            return True

    def _write_kill_switch(self, reason: str) -> None:
        """Write the KILL_SWITCH file with the LLM's reasoning."""
        from config import cfg
        path = Path(cfg.risk.kill_switch_file)
        path.write_text(
            f"AI Overseer halt at {datetime.now(tz=timezone.utc).isoformat()}\n"
            f"Reason: {reason}\n\n"
            "Delete this file to resume trading after human review."
        )
        logger.critical(f"KILL_SWITCH written to {path}")

    def _log_decision(self, decision: OverseerDecision) -> None:
        """Append decision to JSONL audit log."""
        with open(self._log_path, "a") as f:
            f.write(json.dumps(asdict(decision)) + "\n")
        self._decision_log.append(decision)
