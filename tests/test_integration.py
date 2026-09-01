"""End-to-end: every provider mocked at the HTTP boundary, orchestrator ->
rules -> renderer. No live network calls."""
import json
from pathlib import Path

import httpx
import respx

from memescreen import config, orchestrator
from memescreen.render import render_report

FIX = Path(__file__).parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
DEX_URL = f"https://api.dexscreener.com/tokens/v1/solana/{ADDR}"
RUG_URL = f"https://api.rugcheck.xyz/v1/tokens/{ADDR}/report"
OVERVIEW = "https://public-api.birdeye.so/defi/token_overview"
HOLDERS = "https://public-api.birdeye.so/defi/v3/token/holder"
HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=testkey"


def _load(name):
    return json.loads((FIX / name).read_text())


def _with_keys(monkeypatch):
    """Turn the keyed providers on without reloading config (tests/conftest.py
    restores the module either way)."""
    monkeypatch.setattr(config, "BIRDEYE_API_KEY", "testkey")
    monkeypatch.setattr(config, "HELIUS_API_KEY", "testkey")
    monkeypatch.setattr(config, "HELIUS_RPC", HELIUS_RPC)


def _mock_birdeye_and_helius():
    respx.get(OVERVIEW).mock(
        return_value=httpx.Response(200, json=_load("birdeye_overview.json"))
    )
    respx.get(HOLDERS).mock(
        return_value=httpx.Response(200, json=_load("birdeye_holders.json"))
    )
    respx.post(HELIUS_RPC).mock(
        return_value=httpx.Response(200, json=_load("helius_getasset.json"))
    )


@respx.mock
def test_full_screen_from_fixtures_renders_a_report(monkeypatch):
    _with_keys(monkeypatch)
    respx.get(DEX_URL).mock(
        return_value=httpx.Response(200, json=_load("dexscreener_wif.json"))
    )
    respx.get(RUG_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report.json"))
    )
    _mock_birdeye_and_helius()

    report = orchestrator.screen(ADDR)
    assert report.provider_errors == {}
    assert report.verdict in {"TRADE", "CAUTION", "AVOID"}
    assert report.market.symbol == "WIF"
    assert report.risk.lp_locked_pct == 100.0
    assert report.holders.holder_count == 210000

    out = render_report(report)
    assert "WIF" in out
    assert "VERDICT:" in out
    assert report.verdict in out
    assert "Data gaps" not in out


@respx.mock
def test_dexscreener_outage_still_renders_with_a_data_gaps_block(monkeypatch):
    _with_keys(monkeypatch)
    respx.get(DEX_URL).mock(return_value=httpx.Response(500))
    respx.get(RUG_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report.json"))
    )
    _mock_birdeye_and_helius()

    report = orchestrator.screen(ADDR)
    assert report.market is None
    assert "dexscreener" in report.provider_errors
    assert report.verdict in {"TRADE", "CAUTION", "AVOID"}

    out = render_report(report)
    assert "Data gaps" in out
    assert "dexscreener" in out
    assert "VERDICT:" in out
