from datetime import UTC, datetime

import pytest

from memescreen import orchestrator
from memescreen.models import HolderData, MarketData, RiskData
from memescreen.providers.base import ProviderError


def _market(**kw):
    base = dict(
        name="dogwifhat", symbol="WIF", price_usd=0.001, liquidity_usd=250_000.0,
        volume_24h_usd=1_200_000.0, market_cap_usd=1_000_000.0,
        pair_created_at=datetime(2026, 8, 1, tzinfo=UTC), dex="raydium",
    )
    base.update(kw)
    return MarketData(**base)


def test_happy_path_assembles_and_evaluates(monkeypatch):
    monkeypatch.setattr(orchestrator.dexscreener, "fetch_market_data", lambda a: _market())
    monkeypatch.setattr(
        orchestrator.rugcheck, "fetch_risk_data",
        lambda a: RiskData(1, False, False, 100.0, [], []),
    )
    monkeypatch.setattr(
        orchestrator.birdeye, "fetch_holder_data",
        lambda a: HolderData(210000, 18.0, 22.0, None),
    )
    monkeypatch.setattr(orchestrator.helius, "fetch_metadata_fallback", lambda a: None)

    r = orchestrator.screen("mint111")
    assert r.provider_errors == {}
    assert r.market.symbol == "WIF"
    assert r.verdict in {"TRADE", "CAUTION", "AVOID"}
    assert r.score is not None
    assert r.fetched_at.tzinfo is not None


def test_provider_failure_is_recorded_not_raised(monkeypatch):
    def boom(a):
        raise ProviderError("rugcheck", "500 Server Error")

    monkeypatch.setattr(orchestrator.dexscreener, "fetch_market_data", lambda a: _market())
    monkeypatch.setattr(orchestrator.rugcheck, "fetch_risk_data", boom)
    monkeypatch.setattr(orchestrator.birdeye, "fetch_holder_data", lambda a: None)
    monkeypatch.setattr(orchestrator.helius, "fetch_metadata_fallback", lambda a: None)

    r = orchestrator.screen("mint111")
    assert r.risk is None
    assert r.provider_errors["rugcheck"] == "500 Server Error"
    assert r.verdict is not None  # still evaluated on partial data


def test_metadata_fallback_fills_missing_name(monkeypatch):
    monkeypatch.setattr(
        orchestrator.dexscreener, "fetch_market_data",
        lambda a: _market(name=None, symbol=None),
    )
    monkeypatch.setattr(orchestrator.rugcheck, "fetch_risk_data", lambda a: None
                        if False else RiskData(1, False, False, 100.0, [], []))
    monkeypatch.setattr(orchestrator.birdeye, "fetch_holder_data", lambda a: None)
    monkeypatch.setattr(
        orchestrator.helius, "fetch_metadata_fallback", lambda a: ("dogwifhat", "WIF")
    )

    r = orchestrator.screen("mint111")
    assert r.market.name == "dogwifhat"
    assert r.market.symbol == "WIF"


def test_dexscreener_down_still_returns_report(monkeypatch):
    def boom(a):
        raise ProviderError("dexscreener", "no pairs found")

    monkeypatch.setattr(orchestrator.dexscreener, "fetch_market_data", boom)
    monkeypatch.setattr(
        orchestrator.rugcheck, "fetch_risk_data",
        lambda a: RiskData(1, False, False, 100.0, [], []),
    )
    monkeypatch.setattr(orchestrator.birdeye, "fetch_holder_data", lambda a: None)
    monkeypatch.setattr(orchestrator.helius, "fetch_metadata_fallback", lambda a: None)

    r = orchestrator.screen("mint111")
    assert r.market is None
    assert r.provider_errors["dexscreener"] == "no pairs found"
    assert r.verdict is not None
