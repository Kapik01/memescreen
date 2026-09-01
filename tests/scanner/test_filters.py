"""Tests for the strict safety/quality filter gate (spec §6). See
scanner/filters.py for the exact eight-filter order and fail-closed
semantics these exercise."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from memescreen import config
from memescreen.models import AnomalyHit, HolderData, MarketSnapshot, RiskData
from memescreen.providers.base import ProviderError
from memescreen.scanner import filters
from memescreen.scanner.store import Store

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _hit(**kw) -> AnomalyHit:
    base = dict(
        address="tok", detected_at=T0, spike_ratio=10.0, accel=12.0,
        buy_ratio=0.87, buys_m5=40, vol_m5=30000.0, vol_h1=180000.0,
        liq_now=50000.0, liq_change=1.0,
        first_seen_at=T0 - timedelta(minutes=90), anomaly_score=80,
    )
    base.update(kw)
    return AnomalyHit(**base)


def _snap(**kw) -> MarketSnapshot:
    base = dict(
        address="tok", fetched_at=T0, symbol="TOK",
        pair_created_at=T0 - timedelta(minutes=120), dex="raydium",
    )
    base.update(kw)
    return MarketSnapshot(**base)


def _risk(**kw) -> RiskData:
    base = dict(
        rugcheck_score=10, mint_authority_enabled=False,
        freeze_authority_enabled=False, lp_locked_pct=95.0, danger_flags=[],
    )
    base.update(kw)
    return RiskData(**base)


def _holders(**kw) -> HolderData:
    base = dict(holder_count=200, top10_pct=20.0, top20_pct=30.0, creator_pct=5.0)
    base.update(kw)
    return HolderData(**base)


class _RiskCacheStub:
    """Duck-typed stand-in for RiskCache -- just needs .get(address)."""

    def __init__(self, value):
        self._value = value

    def get(self, address):
        return self._value


def _spy_record_hit(monkeypatch, store):
    """Wraps store.record_hit so tests can assert it was called, while still
    performing the real write (so hit_context / hits-table behavior stays
    correct for tests that chain calls)."""
    calls = []
    real = store.record_hit

    def spy(hit, passed_gate, fail_reason):
        calls.append((hit.address, passed_gate, fail_reason))
        return real(hit, passed_gate, fail_reason)

    monkeypatch.setattr(store, "record_hit", spy)
    return calls


def _passing_setup(store, monkeypatch, holder_count=200, first_holder_count=100):
    """Seeds a holder-growth baseline and wires birdeye + risk cache so every
    filter but the one under test in a given case will pass."""
    store.ensure_hit_context("tok", T0 - timedelta(minutes=90), first_holder_count)
    monkeypatch.setattr(
        filters.birdeye, "fetch_holder_data",
        lambda addr: _holders(holder_count=holder_count),
    )
    return _RiskCacheStub(_risk())


def test_all_filters_pass(monkeypatch):
    with Store(":memory:") as store:
        risk_cache = _passing_setup(store, monkeypatch)
        calls = _spy_record_hit(monkeypatch, store)

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (True, "")
        assert ctx.risk is not None
        assert ctx.holders is not None
        assert ctx.holder_growth == 100  # 200 - 100
        assert ctx.age_min == pytest.approx(120.0)
        assert ctx.dex == "raydium"
        assert ctx.symbol is None  # Ruling B: caller fills this in later
        assert calls == [("tok", True, None)]


def test_cooldown_fails(monkeypatch):
    with Store(":memory:") as store:
        store.record_alert("tok", now=T0 - timedelta(hours=1), anomaly_score=80)
        risk_cache = _passing_setup(store, monkeypatch)
        calls = _spy_record_hit(monkeypatch, store)

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "cooldown")
        assert calls == [("tok", False, "cooldown")]


def test_liquidity_below_min_fails(monkeypatch):
    with Store(":memory:") as store:
        risk_cache = _passing_setup(store, monkeypatch)
        hit = _hit(liq_now=config.MIN_LIQUIDITY - 1)

        passed, reason, ctx = filters.evaluate(hit, store, risk_cache, _snap())

        assert (passed, reason) == (False, "liquidity <20k")


def test_age_unknown_when_pair_created_at_missing(monkeypatch):
    with Store(":memory:") as store:
        risk_cache = _passing_setup(store, monkeypatch)
        snap = _snap(pair_created_at=None)

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, snap)

        assert (passed, reason) == (False, "age unknown")
        assert ctx.age_min is None


def test_age_too_young_fails(monkeypatch):
    with Store(":memory:") as store:
        risk_cache = _passing_setup(store, monkeypatch)
        snap = _snap(pair_created_at=T0 - timedelta(minutes=30))

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, snap)

        assert (passed, reason) == (False, "age <60m")
        assert ctx.age_min == pytest.approx(30.0)


def test_risk_cache_returns_none_fails_closed(monkeypatch):
    with Store(":memory:") as store:
        _passing_setup(store, monkeypatch)
        risk_cache = _RiskCacheStub(None)

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "risk data unavailable")
        assert ctx.risk is None


def test_lp_not_locked_fails(monkeypatch):
    with Store(":memory:") as store:
        _passing_setup(store, monkeypatch)
        risk_cache = _RiskCacheStub(_risk(lp_locked_pct=50.0))

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "LP not locked")
        assert ctx.risk is not None


def test_lp_locked_pct_none_fails(monkeypatch):
    with Store(":memory:") as store:
        _passing_setup(store, monkeypatch)
        risk_cache = _RiskCacheStub(_risk(lp_locked_pct=None))

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "LP not locked")


def test_authority_live_mint_fails(monkeypatch):
    with Store(":memory:") as store:
        _passing_setup(store, monkeypatch)
        risk_cache = _RiskCacheStub(_risk(mint_authority_enabled=True))

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "authority live")


def test_authority_unknown_freeze_fails(monkeypatch):
    # None (unknown) must NOT pass -- only exactly False passes.
    with Store(":memory:") as store:
        _passing_setup(store, monkeypatch)
        risk_cache = _RiskCacheStub(_risk(freeze_authority_enabled=None))

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "authority live")


def test_rugcheck_danger_flags_fails(monkeypatch):
    with Store(":memory:") as store:
        _passing_setup(store, monkeypatch)
        risk_cache = _RiskCacheStub(_risk(danger_flags=["mint_authority_active"]))

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "rugcheck danger")


def test_rugcheck_score_above_max_fails(monkeypatch):
    with Store(":memory:") as store:
        _passing_setup(store, monkeypatch)
        risk_cache = _RiskCacheStub(_risk(rugcheck_score=config.RUGCHECK_DANGER_MAX + 1))

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "rugcheck danger")


def test_holder_data_none_fails_closed(monkeypatch):
    with Store(":memory:") as store:
        store.ensure_hit_context("tok", T0 - timedelta(minutes=90), 100)
        monkeypatch.setattr(filters.birdeye, "fetch_holder_data", lambda addr: None)
        risk_cache = _RiskCacheStub(_risk())

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "holder data unavailable")
        assert ctx.holders is None


def test_holder_data_provider_error_fails_closed(monkeypatch):
    with Store(":memory:") as store:
        store.ensure_hit_context("tok", T0 - timedelta(minutes=90), 100)

        def raise_error(addr):
            raise ProviderError("birdeye", "500 Internal Server Error")

        monkeypatch.setattr(filters.birdeye, "fetch_holder_data", raise_error)
        risk_cache = _RiskCacheStub(_risk())

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "holder data unavailable")
        assert ctx.holders is None


def test_holder_count_none_fails_closed(monkeypatch):
    with Store(":memory:") as store:
        store.ensure_hit_context("tok", T0 - timedelta(minutes=90), 100)
        monkeypatch.setattr(
            filters.birdeye, "fetch_holder_data",
            lambda addr: _holders(holder_count=None),
        )
        risk_cache = _RiskCacheStub(_risk())

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "holder data unavailable")


def test_first_ever_hit_records_baseline_and_fails(monkeypatch):
    with Store(":memory:") as store:
        assert store.hit_context("tok") is None
        monkeypatch.setattr(
            filters.birdeye, "fetch_holder_data",
            lambda addr: _holders(holder_count=150),
        )
        risk_cache = _RiskCacheStub(_risk())

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "no holder baseline yet")
        ctx_row = store.hit_context("tok")
        assert ctx_row is not None
        assert ctx_row["first_holder_count"] == 150
        assert ctx.holders is not None
        assert ctx.holder_growth is None  # not yet computed on this path


def test_holders_flat_fails(monkeypatch):
    with Store(":memory:") as store:
        store.ensure_hit_context("tok", T0 - timedelta(minutes=90), 100)
        # growth of 2 clears neither the absolute (25) nor relative (5%) bar
        monkeypatch.setattr(
            filters.birdeye, "fetch_holder_data",
            lambda addr: _holders(holder_count=102),
        )
        risk_cache = _RiskCacheStub(_risk())

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "holders flat")
        assert ctx.holder_growth == 2


def test_holders_pass_via_absolute_growth(monkeypatch):
    with Store(":memory:") as store:
        store.ensure_hit_context("tok", T0 - timedelta(minutes=90), 1000)
        # relative growth (25/1000 = 2.5%) is below HOLDER_GROWTH_MIN (5%),
        # but absolute growth (25) exactly meets HOLDER_GROWTH_ABS.
        monkeypatch.setattr(
            filters.birdeye, "fetch_holder_data",
            lambda addr: _holders(holder_count=1025),
        )
        risk_cache = _RiskCacheStub(_risk())

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (True, "")
        assert ctx.holder_growth == 25


def test_holders_pass_via_relative_growth(monkeypatch):
    with Store(":memory:") as store:
        store.ensure_hit_context("tok", T0 - timedelta(minutes=90), 100)
        # absolute growth (10) is below HOLDER_GROWTH_ABS (25), but relative
        # growth (10%) clears HOLDER_GROWTH_MIN (5%).
        monkeypatch.setattr(
            filters.birdeye, "fetch_holder_data",
            lambda addr: _holders(holder_count=110),
        )
        risk_cache = _RiskCacheStub(_risk())

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (True, "")
        assert ctx.holder_growth == 10


def test_every_evaluation_records_a_hits_row(monkeypatch):
    with Store(":memory:") as store:
        risk_cache = _passing_setup(store, monkeypatch)
        calls = _spy_record_hit(monkeypatch, store)

        filters.evaluate(_hit(), store, risk_cache, _snap())
        filters.evaluate(_hit(detected_at=T0 + timedelta(minutes=1)), store, risk_cache, _snap())

        assert len(calls) == 2
        assert all(passed_gate is True and reason is None for _, passed_gate, reason in calls)
