"""
tests/test_preflight_scope.py — preflight must require trade scope.

The audit found that preflight passed read-only API keys with a green check,
then every order failed with "Invalid params". This test pins the new
behaviour: trade:read_write scope is required for the permissions check
to pass.
"""
from __future__ import annotations

from preflight import _scope_has_trade_write


def test_scope_with_trade_read_write_passes():
    scope = "account:read_write block_trade:read_write trade:read_write mainaccount"
    assert _scope_has_trade_write(scope) is True


def test_scope_with_only_read_fails():
    """Read-only key — exactly the case that's been failing in production."""
    scope = "account:read block_trade:read trade:read mainaccount"
    assert _scope_has_trade_write(scope) is False


def test_scope_empty_fails():
    assert _scope_has_trade_write("") is False


def test_scope_none_safe():
    """None should not raise — preflight may be called pre-auth."""
    assert _scope_has_trade_write(None) is False  # type: ignore[arg-type]


def test_scope_with_extra_qualifier_passes():
    """Some Deribit scope strings include qualifiers like ':*' or quals."""
    scope = "trade:read_write:1234 account:read_write"
    assert _scope_has_trade_write(scope) is True


def test_scope_partial_match_fails():
    """'trade:read' must NOT pass — substring check would falsely accept it."""
    scope = "trade:read account:read"
    assert _scope_has_trade_write(scope) is False
