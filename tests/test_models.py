from datetime import UTC, datetime

from memescreen.models import (
    Flag,
    HolderData,
    HolderShare,
    MarketData,
    RiskData,
    TokenReport,
)


def test_marketdata_defaults():
    m = MarketData(
        name="Doge", symbol="DOGE", price_usd=0.01, liquidity_usd=50_000.0,
        volume_24h_usd=100_000.0, market_cap_usd=1_000_000.0,
        pair_created_at=datetime(2026, 8, 1, tzinfo=UTC), dex="raydium",
    )
    assert m.socials == []
    assert m.source == "dexscreener"


def test_riskdata_holdershare():
    r = RiskData(
        rugcheck_score=1, mint_authority_enabled=False,
        freeze_authority_enabled=False, lp_locked_pct=100.0,
        top_holders=[HolderShare(address="abc", pct=12.5)],
        risk_flags=["Low liquidity"],
    )
    assert r.top_holders[0].pct == 12.5
    assert r.source == "rugcheck"


def test_tokenreport_mutable_result_fields():
    tr = TokenReport(
        address="mint111", fetched_at=datetime.now(UTC), market=None,
        risk=None, holders=None, provider_errors={},
    )
    assert tr.flags == []
    assert tr.score is None
    assert tr.verdict is None
    tr.flags.append(Flag(level="red", check="Liquidity", message="too low"))
    tr.score = 42
    assert tr.score == 42


def test_holderdata_optional():
    h = HolderData(holder_count=None, top10_pct=None, top20_pct=None, creator_pct=None)
    assert h.source == "birdeye"


def test_market_snapshot_optional_fields():
    from memescreen.models import MarketSnapshot
    s = MarketSnapshot(address="m", fetched_at=datetime.now(UTC))
    assert s.price_usd is None and s.buys_m5 is None and s.dex is None


def test_anomaly_hit_shape():
    from memescreen.models import AnomalyHit
    h = AnomalyHit(
        address="m", detected_at=datetime.now(UTC), spike_ratio=12.0, accel=4.0,
        buy_ratio=0.8, buys_m5=40, vol_m5=47000.0, vol_h1=210000.0,
        liq_now=63000.0, liq_change=1.04, first_seen_at=datetime.now(UTC),
        anomaly_score=82,
    )
    assert h.anomaly_score == 82
