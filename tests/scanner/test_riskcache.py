from datetime import UTC, datetime, timedelta

import pytest

from memescreen.models import RiskData
from memescreen.providers.base import ProviderError
from memescreen.scanner.riskcache import RiskCache

_RD = RiskData(rugcheck_score=5, mint_authority_enabled=False,
               freeze_authority_enabled=False, lp_locked_pct=100.0)


class _Clock:
    def __init__(self): self.now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    def __call__(self): return self.now
    def advance(self, **kw): self.now += timedelta(**kw)


def test_second_call_within_ttl_is_cached():
    calls = []
    cache = RiskCache(fetch=lambda a: calls.append(a) or _RD, ttl_h=6.0, clock=_Clock())
    assert cache.get("m").lp_locked_pct == 100.0
    assert cache.get("m").lp_locked_pct == 100.0
    assert calls == ["m"]
    assert cache.stats() == (1, 1)


def test_refetches_after_ttl():
    clk = _Clock()
    calls = []
    cache = RiskCache(fetch=lambda a: calls.append(a) or _RD, ttl_h=6.0, clock=clk)
    cache.get("m")
    clk.advance(hours=7)
    cache.get("m")
    assert calls == ["m", "m"]


def test_provider_error_with_no_prior_entry_returns_none():
    def boom(a): raise ProviderError("rugcheck", "500 err")
    cache = RiskCache(fetch=boom, ttl_h=6.0, clock=_Clock())
    assert cache.get("m") is None


def test_provider_error_falls_back_to_stale_entry():
    clk = _Clock()
    state = {"ok": True}
    def fetch(a):
        if state["ok"]:
            return _RD
        raise ProviderError("rugcheck", "500 err")
    cache = RiskCache(fetch=fetch, ttl_h=6.0, clock=clk)
    cache.get("m")
    state["ok"] = False
    clk.advance(hours=7)
    assert cache.get("m") is _RD          # stale served
