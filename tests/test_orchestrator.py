import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from memescreen import orchestrator
from memescreen.models import HolderData, MarketData, RiskData
from memescreen.providers.base import ProviderError

FIX = Path(__file__).parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
DEX_URL = f"https://api.dexscreener.com/tokens/v1/solana/{ADDR}"
RUG_URL = f"https://api.rugcheck.xyz/v1/tokens/{ADDR}/report"


def _load(name):
    return json.loads((FIX / name).read_text())


def _skip_keyed_providers(monkeypatch):
    """Birdeye/Helius need keys; pin them off so these tests exercise only the
    two keyless providers and never touch the network."""
    monkeypatch.setattr(orchestrator.birdeye, "fetch_holder_data", lambda a: None)
    monkeypatch.setattr(orchestrator.helius, "fetch_metadata_fallback", lambda a: None)


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


@respx.mock
def test_dexscreener_empty_list_is_recorded_not_raised(monkeypatch):
    _skip_keyed_providers(monkeypatch)
    respx.get(DEX_URL).mock(
        return_value=httpx.Response(200, json=_load("dexscreener_empty.json"))
    )
    respx.get(RUG_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report.json"))
    )
    r = orchestrator.screen(ADDR)
    assert r.market is None
    assert r.provider_errors["dexscreener"] == "no pairs found"
    assert r.verdict is not None


@respx.mock
def test_dexscreener_dict_instead_of_list_is_recorded(monkeypatch):
    _skip_keyed_providers(monkeypatch)
    respx.get(DEX_URL).mock(return_value=httpx.Response(200, json={"pairs": []}))
    respx.get(RUG_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report.json"))
    )
    r = orchestrator.screen(ADDR)
    assert r.market is None
    assert "dexscreener" in r.provider_errors
    assert r.verdict is not None


@respx.mock
def test_dexscreener_non_dict_pair_does_not_abort_the_run(monkeypatch):
    # 200 OK, a list as expected, but the elements are strings: the mapping
    # code raises AttributeError, which is not a ProviderError.
    _skip_keyed_providers(monkeypatch)
    respx.get(DEX_URL).mock(return_value=httpx.Response(200, json=["nope"]))
    respx.get(RUG_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report.json"))
    )
    r = orchestrator.screen(ADDR)
    assert r.market is None
    assert r.provider_errors["dexscreener"].startswith("unexpected")
    assert r.verdict is not None


@respx.mock
def test_rugcheck_non_numeric_pct_does_not_abort_the_run(monkeypatch):
    _skip_keyed_providers(monkeypatch)
    bad = _load("rugcheck_report.json")
    bad["topHolders"][0]["pct"] = "not-a-number"
    respx.get(DEX_URL).mock(
        return_value=httpx.Response(200, json=_load("dexscreener_wif.json"))
    )
    respx.get(RUG_URL).mock(return_value=httpx.Response(200, json=bad))
    r = orchestrator.screen(ADDR)
    assert r.risk is None
    assert r.provider_errors["rugcheck"].startswith("unexpected")
    assert r.market is not None
    assert r.verdict is not None


def test_birdeye_unexpected_exception_is_recorded(monkeypatch):
    def boom(a):
        raise TypeError("'NoneType' object is not subscriptable")

    monkeypatch.setattr(orchestrator.dexscreener, "fetch_market_data", lambda a: _market())
    monkeypatch.setattr(
        orchestrator.rugcheck, "fetch_risk_data",
        lambda a: RiskData(1, False, False, 100.0, [], []),
    )
    monkeypatch.setattr(orchestrator.birdeye, "fetch_holder_data", boom)
    monkeypatch.setattr(orchestrator.helius, "fetch_metadata_fallback", lambda a: None)

    r = orchestrator.screen("mint111")
    assert r.holders is None
    assert r.provider_errors["birdeye"] == "unexpected TypeError mapping response"
    assert r.verdict is not None


def test_helius_fallback_exception_is_recorded_and_leaves_name_alone(monkeypatch):
    def boom(a):
        raise AttributeError("'list' object has no attribute 'get'")

    monkeypatch.setattr(
        orchestrator.dexscreener, "fetch_market_data",
        lambda a: _market(name=None, symbol=None),
    )
    monkeypatch.setattr(
        orchestrator.rugcheck, "fetch_risk_data",
        lambda a: RiskData(1, False, False, 100.0, [], []),
    )
    monkeypatch.setattr(orchestrator.birdeye, "fetch_holder_data", lambda a: None)
    monkeypatch.setattr(orchestrator.helius, "fetch_metadata_fallback", boom)

    r = orchestrator.screen("mint111")
    assert r.provider_errors["helius"] == "unexpected AttributeError mapping response"
    assert r.market.name is None
    assert r.market.symbol is None
    assert r.verdict is not None
