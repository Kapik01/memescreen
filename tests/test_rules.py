from datetime import UTC, datetime, timedelta

from memescreen.models import HolderData, MarketData, RiskData, TokenReport
from memescreen.rules import evaluate

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _report(market=None, risk=None, holders=None):
    return TokenReport(
        address="mint", fetched_at=NOW, market=market, risk=risk,
        holders=holders, provider_errors={},
    )


def _market(**kw):
    base = dict(
        name="X", symbol="X", price_usd=1.0, liquidity_usd=50_000.0,
        volume_24h_usd=100_000.0, market_cap_usd=1_000_000.0,
        pair_created_at=NOW - timedelta(days=2), dex="raydium",
    )
    base.update(kw)
    return MarketData(**base)


def _risk(**kw):
    base = dict(
        rugcheck_score=5, mint_authority_enabled=False,
        freeze_authority_enabled=False, lp_locked_pct=100.0,
        top_holders=[], risk_flags=[],
    )
    base.update(kw)
    return RiskData(**base)


def _flag_levels(report):
    return {f.check: f.level for f in report.flags}


def test_clean_token_scores_high_and_trades():
    r = _report(_market(), _risk(), HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert r.verdict == "TRADE"
    assert r.score >= VERDICT_TRADE_MIN_VALUE()
    assert _flag_levels(r)["Liquidity"] == "green"


def VERDICT_TRADE_MIN_VALUE():
    from memescreen import config
    return config.VERDICT_TRADE_MIN


def test_live_mint_authority_forces_avoid():
    r = _report(_market(), _risk(mint_authority_enabled=True),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["Mint authority"] == "red"
    assert r.verdict == "AVOID"


def test_low_liquidity_is_red():
    r = _report(_market(liquidity_usd=4_000.0), _risk(),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["Liquidity"] == "red"
    assert r.verdict == "AVOID"


def test_unlocked_lp_yellow_gives_caution():
    r = _report(_market(), _risk(lp_locked_pct=70.0),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["LP locked"] == "yellow"
    assert r.verdict == "CAUTION"


def test_missing_risk_data_yields_unknown_flags_and_penalty():
    r = _report(_market(), None, HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    levels = _flag_levels(r)
    assert levels["LP locked"] == "unknown"
    assert levels["Mint authority"] == "unknown"
    assert r.score < 100


def test_pair_age_under_30_min_is_red():
    r = _report(_market(pair_created_at=NOW - timedelta(minutes=10)), _risk(),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["Pair age"] == "red"


def test_top10_concentration_over_60_is_red():
    r = _report(_market(), _risk(), HolderData(1000, 72.0, 80.0, None))
    evaluate(r)
    assert _flag_levels(r)["Top-10 concentration"] == "red"


def test_vol_liq_ratio_extreme_is_yellow_not_red():
    r = _report(_market(volume_24h_usd=5_000_000.0, liquidity_usd=50_000.0),
                _risk(), HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["Volume/liquidity ratio"] == "yellow"


def test_rugcheck_flag_with_high_score_is_red():
    r = _report(_market(), _risk(risk_flags=["Rugged"], rugcheck_score=85),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["RugCheck risks"] == "red"


def test_score_clamped_to_zero_floor():
    r = _report(
        _market(liquidity_usd=100.0, pair_created_at=NOW - timedelta(minutes=1)),
        _risk(mint_authority_enabled=True, freeze_authority_enabled=True,
              lp_locked_pct=0.0, risk_flags=["Rugged"], rugcheck_score=99),
        HolderData(1000, 95.0, 99.0, None),
    )
    evaluate(r)
    assert r.score == 0
    assert r.verdict == "AVOID"
