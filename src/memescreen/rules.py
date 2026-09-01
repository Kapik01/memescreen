"""Pure evaluation: TokenReport in, flags + score + verdict attached. No I/O."""
from __future__ import annotations

from memescreen import config
from memescreen.models import Flag, TokenReport

_AUTHORITY_LP_CHECKS = {"Mint authority", "Freeze authority", "LP locked"}

# A check we could not run at all: no evidence, so TRADE must never be reached.
_CRITICAL_CHECKS = {"Liquidity", "LP locked", "Mint authority", "Freeze authority"}


def _liquidity(report: TokenReport) -> Flag:
    v = report.market.liquidity_usd if report.market else None
    if v is None:
        return Flag("unknown", "Liquidity", "no liquidity data")
    if v < config.FLAG_LIQ_RED:
        return Flag("red", "Liquidity", f"${v:,.0f} — very low, easy to rug")
    if v < config.FLAG_LIQ_GREEN:
        return Flag("yellow", "Liquidity", f"${v:,.0f} — thin")
    return Flag("green", "Liquidity", f"${v:,.0f}")


def _lp_locked(report: TokenReport) -> Flag:
    v = report.risk.lp_locked_pct if report.risk else None
    if v is None:
        return Flag("unknown", "LP locked", "no LP lock data")
    if v < config.FLAG_LP_LOCKED_RED:
        return Flag("red", "LP locked", f"{v:.0f}% locked — dev can pull liquidity")
    if v < config.FLAG_LP_LOCKED_GREEN:
        return Flag("yellow", "LP locked", f"{v:.0f}% locked")
    return Flag("green", "LP locked", f"{v:.0f}% locked")


def _mint_authority(report: TokenReport) -> Flag:
    v = report.risk.mint_authority_enabled if report.risk else None
    if v is None:
        return Flag("unknown", "Mint authority", "unknown")
    if v:
        return Flag("red", "Mint authority", "enabled — dev can mint more supply")
    return Flag("green", "Mint authority", "renounced")


def _freeze_authority(report: TokenReport) -> Flag:
    v = report.risk.freeze_authority_enabled if report.risk else None
    if v is None:
        return Flag("unknown", "Freeze authority", "unknown")
    if v:
        return Flag("red", "Freeze authority", "enabled — dev can freeze your wallet")
    return Flag("green", "Freeze authority", "renounced")


def _top10(report: TokenReport) -> Flag:
    v = report.holders.top10_pct if report.holders else None
    if v is None:
        return Flag("unknown", "Top-10 concentration", "no holder data")
    if v > config.FLAG_TOP10_RED:
        return Flag("red", "Top-10 concentration", f"{v:.0f}% held by top 10")
    if v > config.FLAG_TOP10_YELLOW:
        return Flag("yellow", "Top-10 concentration", f"{v:.0f}% held by top 10")
    return Flag("green", "Top-10 concentration", f"{v:.0f}% held by top 10")


def _pair_age(report: TokenReport) -> Flag:
    created = report.market.pair_created_at if report.market else None
    if created is None:
        return Flag("unknown", "Pair age", "unknown")
    minutes = (report.fetched_at - created).total_seconds() / 60
    if minutes < config.FLAG_AGE_RED_MIN:
        return Flag("red", "Pair age", f"{minutes:.0f} min old — brand new")
    if minutes < config.FLAG_AGE_GREEN_HR * 60:
        return Flag("yellow", "Pair age", f"{minutes / 60:.1f} h old")
    return Flag("green", "Pair age", f"{minutes / 60:.0f} h old")


def _vol_liq(report: TokenReport) -> Flag:
    m = report.market
    if not m or m.volume_24h_usd is None or not m.liquidity_usd:
        return Flag("unknown", "Volume/liquidity ratio", "insufficient data")
    ratio = m.volume_24h_usd / m.liquidity_usd
    if ratio > config.FLAG_VOL_LIQ_HIGH or ratio < config.FLAG_VOL_LIQ_LOW:
        return Flag("yellow", "Volume/liquidity ratio", f"{ratio:.2f}x")
    return Flag("green", "Volume/liquidity ratio", f"{ratio:.2f}x")


def _rugcheck(report: TokenReport) -> Flag:
    if not report.risk:
        return Flag("unknown", "RugCheck risks", "no RugCheck data")
    flags = report.risk.risk_flags
    danger = report.risk.danger_flags
    score = report.risk.rugcheck_score
    if danger:
        return Flag("red", "RugCheck risks", ", ".join(danger[:3]))
    if not flags:
        return Flag("green", "RugCheck risks", "none")
    joined = ", ".join(flags[:3])
    if score is not None and score > config.RUGCHECK_DANGER_MAX:
        return Flag("red", "RugCheck risks", f"{joined} (score {score})")
    return Flag("yellow", "RugCheck risks", joined)


_CHECKS = (
    _liquidity, _lp_locked, _mint_authority, _freeze_authority,
    _top10, _pair_age, _vol_liq, _rugcheck,
)


def evaluate(report: TokenReport) -> None:
    flags = [check(report) for check in _CHECKS]
    score = 100
    for f in flags:
        if f.level == "red":
            score -= config.SCORE_RED_WEIGHT
        elif f.level == "yellow":
            score -= config.SCORE_YELLOW_WEIGHT
        elif f.level == "unknown":
            score -= config.SCORE_UNKNOWN_WEIGHT
    score = max(0, min(100, score))

    has_red = any(f.level == "red" for f in flags)
    caution_yellow = any(
        f.level == "yellow" and f.check in _AUTHORITY_LP_CHECKS for f in flags
    )
    # Missing data on a safety-critical check means we never saw the evidence
    # TRADE would be claiming; degrade to CAUTION regardless of score.
    blind = any(f.level == "unknown" and f.check in _CRITICAL_CHECKS for f in flags)
    if has_red or score < config.VERDICT_AVOID_MAX:
        verdict = "AVOID"
    elif score < config.VERDICT_TRADE_MIN or caution_yellow or blind:
        verdict = "CAUTION"
    else:
        verdict = "TRADE"

    report.flags = flags
    report.score = score
    report.verdict = verdict
