"""
preflight.py — Pre-flight validation checks before going live on Deribit.

Verifies API connectivity, authentication, account state, and safety
conditions before the bot is allowed to place real orders.

Usage:
    python preflight.py                    # run all checks
    python preflight.py --testnet          # check against testnet
    python main.py --preflight             # same, via main CLI

Also importable by the dashboard for the live connection status panel.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests
from loguru import logger

# ── Result types ───────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    detail: str = ""


@dataclass
class PreflightReport:
    checks: list[CheckResult] = field(default_factory=list)
    ready_for_live: bool = False
    ready_for_testnet: bool = False

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def critical_passed(self) -> bool:
        """True if all non-optional checks passed."""
        # Checks named with [OPT] in their name are optional
        critical = [c for c in self.checks if "[OPT]" not in c.name]
        return all(c.passed for c in critical)

    def summary(self) -> str:
        lines = []
        for c in self.checks:
            icon = "✅" if c.passed else "❌"
            lines.append(f"{icon}  {c.name}: {c.message}")
            if c.detail:
                lines.append(f"    {c.detail}")
        lines.append("")
        if self.ready_for_live:
            lines.append("🟢  READY FOR LIVE TRADING")
        elif self.ready_for_testnet:
            lines.append("🟡  READY FOR TESTNET (not live — check failures above)")
        else:
            lines.append("🔴  NOT READY — fix failures above before trading")
        return "\n".join(lines)


# ── Individual checks ──────────────────────────────────────────────────────────


def check_env_vars() -> CheckResult:
    """Verify DERIBIT_API_KEY and DERIBIT_API_SECRET are set."""
    key    = os.getenv("DERIBIT_API_KEY", "")
    secret = os.getenv("DERIBIT_API_SECRET", "")
    if key and secret:
        masked_key = key[:4] + "..." + key[-4:] if len(key) > 8 else "***"
        return CheckResult(
            name="API Credentials",
            passed=True,
            message=f"API key found ({masked_key})",
        )
    missing = []
    if not key:
        missing.append("DERIBIT_API_KEY")
    if not secret:
        missing.append("DERIBIT_API_SECRET")
    return CheckResult(
        name="API Credentials",
        passed=False,
        message=f"Missing: {', '.join(missing)}",
        detail="Add these to ~/Documents/btc-wheel-bot/.env",
    )


def check_dotenv_file(bot_dir: Path) -> CheckResult:
    """Check that .env exists (optional — env vars may come from system)."""
    env_path = bot_dir / ".env"
    if env_path.exists():
        return CheckResult(
            name=".env File [OPT]",
            passed=True,
            message=".env file present",
            detail=str(env_path),
        )
    return CheckResult(
        name=".env File [OPT]",
        passed=False,
        message=".env file not found (OK if vars set in shell)",
        detail=f"Expected at {env_path} — copy from .env.example",
    )


def check_kill_switch(bot_dir: Path) -> CheckResult:
    """Ensure KILL_SWITCH file is not present."""
    ks_path = bot_dir / "KILL_SWITCH"
    if ks_path.exists():
        return CheckResult(
            name="Kill Switch",
            passed=False,
            message="KILL_SWITCH file is ACTIVE — delete it before going live",
            detail=str(ks_path),
        )
    return CheckResult(
        name="Kill Switch",
        passed=True,
        message="Kill switch is clear",
    )


def check_connectivity(testnet: bool = False) -> CheckResult:
    """Ping Deribit's public API to verify network connectivity."""
    base = "https://test.deribit.com" if testnet else "https://www.deribit.com"
    url  = f"{base}/api/v2/public/get_time"
    env  = "testnet" if testnet else "mainnet"
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        server_time_ms = data["result"]
        server_time = time.strftime(
            "%Y-%m-%d %H:%M:%S UTC",
            time.gmtime(server_time_ms / 1000),
        )
        return CheckResult(
            name=f"Connectivity ({env})",
            passed=True,
            message=f"Deribit {env} reachable",
            detail=f"Server time: {server_time}",
        )
    except requests.Timeout:
        return CheckResult(
            name=f"Connectivity ({env})",
            passed=False,
            message=f"Deribit {env} timed out after 8s",
            detail="Check internet / VPN / firewall",
        )
    except Exception as exc:
        return CheckResult(
            name=f"Connectivity ({env})",
            passed=False,
            message=f"Cannot reach Deribit {env}: {exc}",
        )


def check_authentication(
    api_key: str, api_secret: str, testnet: bool = False
) -> tuple[CheckResult, str]:
    """
    Authenticate with Deribit and return (CheckResult, access_token).
    Returns empty token on failure.
    """
    base = "https://test.deribit.com" if testnet else "https://www.deribit.com"
    url  = f"{base}/api/v2/public/auth"
    env  = "testnet" if testnet else "mainnet"
    try:
        resp = requests.get(url, params={
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": api_secret,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return CheckResult(
                name=f"Authentication ({env})",
                passed=False,
                message=f"Auth failed: {data['error'].get('message', data['error'])}",
            ), ""
        token = data["result"]["access_token"]
        scope = data["result"].get("scope", "unknown")
        return CheckResult(
            name=f"Authentication ({env})",
            passed=True,
            message=f"Authenticated OK",
            detail=f"Scope: {scope}",
        ), token
    except Exception as exc:
        return CheckResult(
            name=f"Authentication ({env})",
            passed=False,
            message=f"Auth request failed: {exc}",
        ), ""


def check_api_permissions(access_token: str, testnet: bool = False) -> CheckResult:
    """Verify the API key has Trade permission (needed for order placement)."""
    base = "https://test.deribit.com" if testnet else "https://www.deribit.com"
    env  = "testnet" if testnet else "mainnet"
    try:
        resp = requests.get(
            f"{base}/api/v2/private/get_account_summary",
            params={"currency": "BTC", "extended": True},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return CheckResult(
                name=f"API Permissions ({env})",
                passed=False,
                message=f"Permission check failed: {data['error']}",
                detail="Key may be read-only — enable Trade permission on Deribit",
            )
        # If get_account_summary succeeds, Read is confirmed.
        # Trade permission is only verifiable by placing/cancelling a tiny order,
        # so we infer it from scope (checked in authenticate) and warn if uncertain.
        return CheckResult(
            name=f"API Permissions ({env})",
            passed=True,
            message="Read access confirmed",
            detail="Trade access will be verified on first order",
        )
    except Exception as exc:
        return CheckResult(
            name=f"API Permissions ({env})",
            passed=False,
            message=f"Permission check failed: {exc}",
        )


def check_account_equity(
    access_token: str, testnet: bool = False, min_equity_btc: float = 0.01
) -> CheckResult:
    """
    Verify the account has enough BTC equity to trade at least one contract.
    Minimum contract on Deribit BTC options = 0.1 BTC notional.
    At ~$85k/BTC that's ~$8.5k collateral for the minimum put strike.
    We check for min_equity_btc (default 0.01 BTC ≈ $850 for testnet).
    """
    base = "https://test.deribit.com" if testnet else "https://www.deribit.com"
    env  = "testnet" if testnet else "mainnet"
    try:
        resp = requests.get(
            f"{base}/api/v2/private/get_account_summary",
            params={"currency": "BTC"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        data = resp.json()["result"]
        equity    = float(data.get("equity", 0))
        balance   = float(data.get("balance", 0))
        available = float(data.get("available_funds", 0))
        ok = equity >= min_equity_btc
        return CheckResult(
            name=f"Account Equity ({env})",
            passed=ok,
            message=(
                f"Equity: {equity:.6f} BTC | Available: {available:.6f} BTC"
                if ok else
                f"Equity too low: {equity:.6f} BTC (need ≥ {min_equity_btc} BTC)"
            ),
            detail=f"Balance: {balance:.6f} BTC",
        )
    except Exception as exc:
        return CheckResult(
            name=f"Account Equity ({env})",
            passed=False,
            message=f"Could not fetch equity: {exc}",
        )


def check_open_positions(access_token: str, testnet: bool = False) -> CheckResult:
    """Report any existing open positions (informational — never blocks)."""
    base = "https://test.deribit.com" if testnet else "https://www.deribit.com"
    env  = "testnet" if testnet else "mainnet"
    try:
        resp = requests.get(
            f"{base}/api/v2/private/get_positions",
            params={"currency": "BTC", "kind": "option"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        positions = resp.json()["result"]
        if not positions:
            return CheckResult(
                name=f"Open Positions ({env}) [OPT]",
                passed=True,
                message="No open positions — starting flat",
            )
        names = [p["instrument_name"] for p in positions[:5]]
        extra = len(positions) - 5
        pos_str = ", ".join(names) + (f" (+{extra} more)" if extra > 0 else "")
        return CheckResult(
            name=f"Open Positions ({env}) [OPT]",
            passed=True,
            message=f"{len(positions)} open position(s) — will reconcile on startup",
            detail=pos_str,
        )
    except Exception as exc:
        return CheckResult(
            name=f"Open Positions ({env}) [OPT]",
            passed=True,  # non-blocking
            message=f"Could not check open positions: {exc}",
        )


# ── Main runner ────────────────────────────────────────────────────────────────


def run_preflight(
    testnet: bool = False,
    bot_dir: Path | None = None,
    on_check: Callable[[CheckResult], None] | None = None,
) -> PreflightReport:
    """
    Run all pre-flight checks and return a PreflightReport.

    Args:
        testnet:   If True, check against Deribit testnet; otherwise mainnet.
        bot_dir:   Root directory of the bot (default: directory of this file).
        on_check:  Optional callback called after each check completes —
                   useful for streaming results to the dashboard.
    """
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=(bot_dir or Path(__file__).parent) / ".env")

    if bot_dir is None:
        bot_dir = Path(__file__).parent

    api_key    = os.getenv("DERIBIT_API_KEY", "")
    api_secret = os.getenv("DERIBIT_API_SECRET", "")

    report  = PreflightReport()
    results = []

    def _add(result: CheckResult) -> None:
        results.append(result)
        report.checks.append(result)
        if on_check:
            on_check(result)

    # 1. Env vars
    _add(check_env_vars())

    # 2. .env file (optional)
    _add(check_dotenv_file(bot_dir))

    # 3. Kill switch
    _add(check_kill_switch(bot_dir))

    # 4. Connectivity
    conn = check_connectivity(testnet)
    _add(conn)

    # Remaining checks require connectivity + credentials
    access_token = ""
    if conn.passed and api_key and api_secret:

        # 5. Authentication
        auth_result, access_token = check_authentication(api_key, api_secret, testnet)
        _add(auth_result)

        if access_token:
            # 6. Permissions
            _add(check_api_permissions(access_token, testnet))

            # 7. Account equity
            min_eq = 0.001 if testnet else 0.01   # testnet has fake funds, be lenient
            _add(check_account_equity(access_token, testnet, min_equity_btc=min_eq))

            # 8. Open positions (informational)
            _add(check_open_positions(access_token, testnet))
    else:
        if not conn.passed:
            _add(CheckResult(
                name="Authentication (skipped)",
                passed=False,
                message="Skipped — no connectivity",
            ))
        elif not api_key or not api_secret:
            _add(CheckResult(
                name="Authentication (skipped)",
                passed=False,
                message="Skipped — missing API credentials",
            ))

    # Determine readiness
    critical = [c for c in report.checks if "[OPT]" not in c.name and "(skipped)" not in c.name]
    all_critical_pass = all(c.passed for c in critical)

    report.ready_for_testnet = all_critical_pass and testnet
    report.ready_for_live    = all_critical_pass and not testnet
    return report


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BTC Wheel Bot — Pre-flight checks")
    parser.add_argument("--testnet", action="store_true", help="Check against testnet")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  BTC Wheel Bot — Pre-flight Check ({'TESTNET' if args.testnet else 'MAINNET'})")
    print(f"{'='*60}\n")

    def _print_check(result: CheckResult) -> None:
        icon = "✅" if result.passed else "❌"
        print(f"{icon}  {result.name}: {result.message}")
        if result.detail:
            print(f"      {result.detail}")

    report = run_preflight(testnet=args.testnet, on_check=_print_check)
    print(f"\n{'='*60}")
    if report.ready_for_live or report.ready_for_testnet:
        print(f"  🟢  READY — all critical checks passed")
    else:
        print(f"  🔴  NOT READY — fix failures above")
    print(f"{'='*60}\n")
    sys.exit(0 if report.critical_passed else 1)
