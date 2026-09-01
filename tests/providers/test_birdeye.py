import importlib
import json
from pathlib import Path

import httpx
import pytest
import respx

FIX = Path(__file__).parent.parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
OVERVIEW = f"https://public-api.birdeye.so/defi/token_overview"
HOLDERS = f"https://public-api.birdeye.so/defi/v3/token/holder"


def _load(name):
    return json.loads((FIX / name).read_text())


def _reload_with_key(monkeypatch, key):
    if key is None:
        monkeypatch.delenv("BIRDEYE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("BIRDEYE_API_KEY", key)
    import memescreen.config as config
    importlib.reload(config)
    import memescreen.providers.birdeye as birdeye
    importlib.reload(birdeye)
    return birdeye


def test_returns_none_without_key(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, None)
    assert birdeye.fetch_holder_data(ADDR) is None


@respx.mock
def test_maps_overview_and_holders(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(OVERVIEW).mock(
        return_value=httpx.Response(200, json=_load("birdeye_overview.json"))
    )
    respx.get(HOLDERS).mock(
        return_value=httpx.Response(200, json=_load("birdeye_holders.json"))
    )
    h = birdeye.fetch_holder_data(ADDR)
    assert h.holder_count == 210000
    # fixture: first 10 percentages sum to 17.6, first 20 sum to 21.0
    assert round(h.top10_pct, 1) == 17.6
    assert round(h.top20_pct, 1) == 21.0
    assert h.source == "birdeye"


@respx.mock
def test_rate_limit_propagates(monkeypatch):
    from memescreen.providers.base import ProviderError

    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(OVERVIEW).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderError):
        birdeye.fetch_holder_data(ADDR)


@respx.mock
def test_holder_percentages_absent_yield_none_not_zero(monkeypatch):
    # A non-empty items list where nothing is parseable must not be reported
    # as "0% held by top 10" — that is a fabricated safety signal.
    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(OVERVIEW).mock(
        return_value=httpx.Response(200, json=_load("birdeye_overview.json"))
    )
    respx.get(HOLDERS).mock(
        return_value=httpx.Response(200, json={"data": {"items": [
            {"owner": "h1"}, {"owner": "h2", "percentage": "4.2"},
        ]}})
    )
    h = birdeye.fetch_holder_data(ADDR)
    assert h.top10_pct is None
    assert h.top20_pct is None
    assert h.holder_count == 210000


@respx.mock
def test_requests_carry_key_chain_and_holder_limit(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, "testkey")
    overview = respx.get(OVERVIEW).mock(
        return_value=httpx.Response(200, json=_load("birdeye_overview.json"))
    )
    holders = respx.get(HOLDERS).mock(
        return_value=httpx.Response(200, json=_load("birdeye_holders.json"))
    )
    birdeye.fetch_holder_data(ADDR)

    for route in (overview, holders):
        request = route.calls.last.request
        assert request.headers["X-API-KEY"] == "testkey"
        assert request.headers["x-chain"] == "solana"

    holder_request = holders.calls.last.request
    assert holder_request.url.params["address"] == ADDR
    assert holder_request.url.params["limit"] == "20"


@respx.mock
def test_feed_addresses_union_of_trending_and_new_listing(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(url__regex=r"https://public-api\.birdeye\.so/defi/token_trending.*").mock(
        return_value=httpx.Response(200, json=_load("birdeye_trending.json")))
    respx.get(url__regex=r"https://public-api\.birdeye\.so/defi/v2/tokens/new_listing.*").mock(
        return_value=httpx.Response(200, json=_load("birdeye_new_listing.json")))
    addrs = birdeye.fetch_feed_addresses()
    assert "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE" in addrs
    assert "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF" in addrs


@respx.mock
def test_feed_addresses_tolerates_a_non_dict_body(monkeypatch):
    # A list (or null) body must not raise AttributeError out of _feed_call.
    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(url__regex=r".*/defi/token_trending.*").mock(
        return_value=httpx.Response(200, json=[]))
    respx.get(url__regex=r".*/defi/v2/tokens/new_listing.*").mock(
        return_value=httpx.Response(200, json=[]))
    assert birdeye.fetch_feed_addresses() == set()


def test_feed_addresses_empty_without_key(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, None)
    assert birdeye.fetch_feed_addresses() == set()


@respx.mock
def test_feed_addresses_tolerates_one_endpoint_down(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(url__regex=r".*/defi/token_trending.*").mock(return_value=httpx.Response(429))
    respx.get(url__regex=r".*/defi/v2/tokens/new_listing.*").mock(
        return_value=httpx.Response(200, json=_load("birdeye_new_listing.json")))
    addrs = birdeye.fetch_feed_addresses()
    assert "GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG" in addrs
