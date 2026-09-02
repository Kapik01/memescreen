import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from memescreen.providers.base import ProviderError
from memescreen.providers.dexscreener import _pick_primary_pair, fetch_market_data, fetch_snapshots, fetch_feed_addresses

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


@respx.mock
def test_fetch_snapshots_groups_and_picks_primary_pair():
    respx.get(url__regex=r"https://api\.dexscreener\.com/tokens/v1/solana/.+").mock(
        return_value=httpx.Response(200, json=_load("dexscreener_batch.json"))
    )
    out = fetch_snapshots(["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                           "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"])
    assert set(out) == {"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                        "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"}
    a = out["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"]
    assert a.liquidity_usd == 250000.0          # highest-liquidity pair won
    assert a.vol_m5 == 8000.0 and a.buys_m5 == 40 and a.sells_m5 == 6
    assert a.vol_h1 == 60000.0 and a.vol_h6 == 120000.0
    assert a.dex == "raydium"


@respx.mock
def test_fetch_snapshots_maps_price_change_h1():
    pair = {
        "chainId": "solana", "dexId": "raydium", "pairAddress": "p",
        "baseToken": {"address": "C" * 44, "symbol": "CCC", "name": "Ccc"},
        "priceUsd": "0.002", "liquidity": {"usd": 40000.0},
        "volume": {"m5": 100.0, "h1": 2000.0, "h6": 9000.0, "h24": 300000.0},
        "txns": {"m5": {"buys": 3, "sells": 1}},
        "priceChange": {"m5": 1.0, "h1": 27.4, "h24": 55.0},
        "marketCap": 120000.0, "pairCreatedAt": 1722470400000,
    }
    respx.get(url__regex=r"https://api\.dexscreener\.com/tokens/v1/solana/.+").mock(
        return_value=httpx.Response(200, json=[pair])
    )
    out = fetch_snapshots(["C" * 44])
    assert out["C" * 44].price_change_h1 == 27.4


@respx.mock
def test_fetch_snapshots_price_change_h1_none_when_absent():
    pair = {
        "chainId": "solana", "dexId": "raydium", "pairAddress": "p",
        "baseToken": {"address": "D" * 44, "symbol": "DDD", "name": "Ddd"},
        "priceUsd": "0.002", "liquidity": {"usd": 40000.0},
        "volume": {"h24": 300000.0}, "txns": {"m5": {"buys": 3, "sells": 1}},
        "marketCap": 120000.0,
    }
    respx.get(url__regex=r"https://api\.dexscreener\.com/tokens/v1/solana/.+").mock(
        return_value=httpx.Response(200, json=[pair])
    )
    assert fetch_snapshots(["D" * 44])["D" * 44].price_change_h1 is None


@respx.mock
def test_fetch_snapshots_chunks_by_batch_size(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "BATCH_SIZE", 2)
    route = respx.get(url__regex=r"https://api\.dexscreener\.com/tokens/v1/solana/.+").mock(
        return_value=httpx.Response(200, json=[])
    )
    fetch_snapshots(["A" * 44, "B" * 44, "C" * 44, "D" * 44, "E" * 44])
    assert route.call_count == 3            # 2 + 2 + 1


@respx.mock
def test_fetch_snapshots_one_bad_chunk_does_not_sink_the_rest(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "BATCH_SIZE", 2)
    responses = [httpx.Response(500), httpx.Response(500), httpx.Response(200, json=_load("dexscreener_batch.json"))]
    route = respx.get(url__regex=r"https://api\.dexscreener\.com/tokens/v1/solana/.+").mock(
        side_effect=responses
    )
    out = fetch_snapshots(["X" * 44, "Y" * 44,
                           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                           "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"])
    assert route.call_count == 3
    assert set(out) == {"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                        "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"}


@respx.mock
def test_fetch_feed_addresses_solana_only_union():
    respx.get("https://api.dexscreener.com/token-boosts/top/v1").mock(
        return_value=httpx.Response(200, json=_load("dexscreener_boosts.json")))
    respx.get("https://api.dexscreener.com/token-boosts/latest/v1").mock(
        return_value=httpx.Response(200, json=[]))
    respx.get("https://api.dexscreener.com/token-profiles/latest/v1").mock(
        return_value=httpx.Response(200, json=_load("dexscreener_profiles.json")))
    addrs = fetch_feed_addresses()
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" in addrs
    assert "DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD" in addrs
    assert "0xdeadbeef" not in addrs


@respx.mock
def test_fetch_feed_addresses_tolerates_one_feed_down():
    respx.get("https://api.dexscreener.com/token-boosts/top/v1").mock(
        return_value=httpx.Response(500))
    respx.get("https://api.dexscreener.com/token-boosts/latest/v1").mock(
        return_value=httpx.Response(200, json=_load("dexscreener_boosts.json")))
    respx.get("https://api.dexscreener.com/token-profiles/latest/v1").mock(
        return_value=httpx.Response(200, json=[]))
    addrs = fetch_feed_addresses()
    assert "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC" in addrs
