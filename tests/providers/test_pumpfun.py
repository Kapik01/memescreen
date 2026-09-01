import httpx
import respx

from memescreen import config
from memescreen.models import CreatorStats
from memescreen.providers import pumpfun

_B = "https://frontend-api-v3.pump.fun"
MINT = "9bTJqiCWRcMstWKdpLtxbWbnUJERnewFxFSjw8zRpump"
CREATOR = "CxxcsDAobcZPeYgWUGfuXb4Via5QNm9xhdU96dUTKsfR"


@respx.mock
def test_returns_creator_and_count():
    respx.get(f"{_B}/coins/{MINT}").mock(
        return_value=httpx.Response(200, json={"mint": MINT, "creator": CREATOR})
    )
    respx.get(url__startswith=f"{_B}/coins").mock(
        return_value=httpx.Response(200, json=[{"mint": "a"}, {"mint": "b"}, {"mint": "c"}])
    )
    stats = pumpfun.fetch_creator_stats(MINT)
    assert isinstance(stats, CreatorStats)
    assert stats.wallet == CREATOR
    assert stats.coins_created == 3
    assert stats.capped is False


@respx.mock
def test_full_page_is_marked_capped(monkeypatch):
    monkeypatch.setattr(config, "PUMPFUN_COUNT_LIMIT", 3)
    respx.get(f"{_B}/coins/{MINT}").mock(
        return_value=httpx.Response(200, json={"creator": CREATOR})
    )
    respx.get(url__startswith=f"{_B}/coins").mock(
        return_value=httpx.Response(200, json=[{}, {}, {}])
    )
    stats = pumpfun.fetch_creator_stats(MINT)
    assert stats.coins_created == 3 and stats.capped is True


@respx.mock
def test_non_pumpfun_token_404_returns_none():
    respx.get(f"{_B}/coins/{MINT}").mock(return_value=httpx.Response(404, json={"m": "x"}))
    assert pumpfun.fetch_creator_stats(MINT) is None


@respx.mock
def test_cloudflare_5xx_returns_none():
    respx.get(f"{_B}/coins/{MINT}").mock(return_value=httpx.Response(530))
    assert pumpfun.fetch_creator_stats(MINT) is None


@respx.mock
def test_missing_creator_field_returns_none():
    respx.get(f"{_B}/coins/{MINT}").mock(
        return_value=httpx.Response(200, json={"mint": MINT})
    )
    assert pumpfun.fetch_creator_stats(MINT) is None


@respx.mock
def test_creator_coins_call_failing_returns_none():
    respx.get(f"{_B}/coins/{MINT}").mock(
        return_value=httpx.Response(200, json={"creator": CREATOR})
    )
    respx.get(url__startswith=f"{_B}/coins").mock(return_value=httpx.Response(503))
    assert pumpfun.fetch_creator_stats(MINT) is None


@respx.mock
def test_creator_coins_non_list_returns_none():
    respx.get(f"{_B}/coins/{MINT}").mock(
        return_value=httpx.Response(200, json={"creator": CREATOR})
    )
    respx.get(url__startswith=f"{_B}/coins").mock(
        return_value=httpx.Response(200, json={"error": "nope"})
    )
    assert pumpfun.fetch_creator_stats(MINT) is None


@respx.mock
def test_sends_browser_user_agent():
    route = respx.get(f"{_B}/coins/{MINT}").mock(
        return_value=httpx.Response(200, json={"creator": CREATOR})
    )
    respx.get(url__startswith=f"{_B}/coins").mock(return_value=httpx.Response(200, json=[]))
    pumpfun.fetch_creator_stats(MINT)
    assert "Mozilla" in route.calls.last.request.headers["user-agent"]


@respx.mock
def test_system_program_creator_returns_none():
    # v3 answers for non-pump.fun mints with the system program as "creator".
    respx.get(f"{_B}/coins/{MINT}").mock(
        return_value=httpx.Response(200, json={"creator": "1" * 32})
    )
    assert pumpfun.fetch_creator_stats(MINT) is None
