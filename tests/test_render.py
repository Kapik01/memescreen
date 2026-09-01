from datetime import UTC, datetime, timedelta

from memescreen.models import Flag, HolderData, MarketData, RiskData, TokenReport
from memescreen.orchestrator import screen  # not used; ensures import graph is fine
from memescreen.render import render_report

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _full_report():
    r = TokenReport(
        address="EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        fetched_at=NOW,
        market=MarketData(
            name="dogwifhat", symbol="WIF", price_usd=0.00123,
            liquidity_usd=250_000.0, volume_24h_usd=1_200_000.0,
            market_cap_usd=1_000_000.0, pair_created_at=NOW - timedelta(hours=30),
            dex="raydium", socials=["https://x.com/dogwifcoin"],
        ),
        risk=RiskData(12, False, False, 100.0, [], ["Low amount of LP Providers"]),
        holders=HolderData(210000, 18.0, 22.0, None),
        provider_errors={},
    )
    r.flags = [
        Flag("green", "Liquidity", "$250,000"),
        Flag("yellow", "RugCheck risks", "Low amount of LP Providers"),
    ]
    r.score = 84
    r.verdict = "CAUTION"
    return r


def test_render_contains_key_lines():
    out = render_report(_full_report())
    assert "WIF" in out
    assert "dogwifhat" in out
    assert "CAUTION" in out
    assert "84/100" in out
    assert "Liquidity" in out
    assert "$250,000" in out
    assert "dexscreener.com/solana/EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm" in out
    assert "x.com/dogwifcoin" in out


def test_render_shows_data_gaps():
    r = _full_report()
    r.provider_errors = {"birdeye": "429 Too Many Requests"}
    r.holders = None
    out = render_report(r)
    assert "Data gaps" in out
    assert "birdeye" in out
    assert "429" in out


def test_render_omits_missing_market_lines():
    r = _full_report()
    r.market.market_cap_usd = None
    out = render_report(r)
    assert "Market cap" not in out


def test_render_handles_no_market_at_all():
    r = _full_report()
    r.market = None
    out = render_report(r)
    assert "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm" in out
    assert "CAUTION" in out


def test_render_keeps_zero_price_line():
    r = _full_report()
    r.market.price_usd = 0.0
    out = render_report(r)
    assert "Price" in out
    assert "$0" in out
