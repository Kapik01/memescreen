import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from memescreen.providers.base import ProviderError
from memescreen.providers.dexscreener import _pick_primary_pair, fetch_market_data

FIX = Path(__file__).parent.parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


def _load(name):
    return json.loads((FIX / name).read_text())


def test_pick_primary_pair_takes_highest_liquidity():
    pairs = _load("dexscreener_wif.json")
    assert _pick_primary_pair(pairs)["pairAddress"] == "pairmain"


def test_pick_primary_pair_tolerates_missing_liquidity():
    pairs = [{"pairAddress": "a"}, {"pairAddress": "b", "liquidity": {"usd": 1.0}}]
    assert _pick_primary_pair(pairs)["pairAddress"] == "b"


@respx.mock
def test_fetch_market_data_maps_primary_pair():
    respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{ADDR}").mock(
        return_value=httpx.Response(200, json=_load("dexscreener_wif.json"))
    )
    m = fetch_market_data(ADDR)
    assert m.symbol == "WIF"
    assert m.name == "dogwifhat"
    assert m.price_usd == 0.001
    assert m.liquidity_usd == 250000.0
    assert m.volume_24h_usd == 1200000.0
    assert m.market_cap_usd == 1000000.0
    assert m.dex == "raydium"
    assert m.pair_created_at == datetime.fromtimestamp(1722470400, tz=UTC)
    assert "https://x.com/dogwifcoin" in m.socials
    assert "https://dogwifcoin.org" in m.socials
    assert m.source == "dexscreener"


@respx.mock
def test_fetch_market_data_empty_array_raises():
    respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{ADDR}").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(ProviderError) as ei:
        fetch_market_data(ADDR)
    assert ei.value.reason == "no pairs found"


@respx.mock
def test_fetch_market_data_missing_optional_fields_are_none():
    minimal = [{
        "dexId": "raydium",
        "baseToken": {"name": "X", "symbol": "X"},
        "priceUsd": "0.5",
    }]
    respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{ADDR}").mock(
        return_value=httpx.Response(200, json=minimal)
    )
    m = fetch_market_data(ADDR)
    assert m.liquidity_usd is None
    assert m.volume_24h_usd is None
    assert m.pair_created_at is None
    assert m.socials == []
