"""Strict safety/quality gate (spec §6): the nine-filter chain that decides
whether a detected `AnomalyHit` becomes a Discord alert.

Filters 1-8 are fail-closed: any filter whose safety data could not be
obtained (risk cache miss, a Birdeye error, a missing holder baseline) is
treated as a failure, never a pass. Filter 9 (pump.fun serial-creator) is
fail-OPEN: it only drops an alert when it positively identifies a farm
wallet; a non-pump.fun token or an unreachable API just skips it.

`evaluate()` always calls `store.record_hit(...)` exactly once, on every
path, so every gate decision is auditable in the `hits` table regardless
of outcome."""
from __future__ import annotations

from dataclasses import dataclass

from memescreen import config
from memescreen.models import AnomalyHit, HolderData, MarketSnapshot, RiskData
from memescreen.providers import birdeye, pumpfun
from memescreen.providers.base import ProviderError
from memescreen.scanner.riskcache import RiskCache
from memescreen.scanner.store import Store


@dataclass
class FilterContext:
    """Carries what the alert message needs. Populated incrementally as
    `evaluate` proceeds -- a field stays `None` if evaluation never reached
    the step that would compute it."""
    risk: RiskData | None
    holders: HolderData | None
    holder_growth: int | None
    age_min: float | None
    dex: str | None
    symbol: str | None = None  # Ruling B: filled by the caller (Task 11) later
    pumpfun_creator: str | None = None
    pumpfun_coins: int | None = None


def evaluate(
    hit: AnomalyHit,
    store: Store,
    risk_cache: RiskCache,
    latest_snapshot: MarketSnapshot,
) -> tuple[bool, str, FilterContext]:
    ctx = FilterContext(
        risk=None, holders=None, holder_growth=None, age_min=None,
        dex=latest_snapshot.dex,
    )

    def _done(passed: bool, reason: str) -> tuple[bool, str, FilterContext]:
        store.record_hit(hit, passed_gate=passed, fail_reason=None if passed else reason)
        return passed, reason, ctx

    # 1. cooldown -- already alerted on this token recently
    if store.recent_alert(hit.address, now=hit.detected_at, cooldown_h=config.ALERT_COOLDOWN_H):
        return _done(False, "cooldown")

    # 2. minimum liquidity
    if hit.liq_now < config.MIN_LIQUIDITY:
        return _done(False, "liquidity <20k")

    # 3. minimum pair age
    if latest_snapshot.pair_created_at is None:
        return _done(False, "age unknown")
    age_min = (hit.detected_at - latest_snapshot.pair_created_at).total_seconds() / 60.0
    ctx.age_min = age_min
    if age_min < config.MIN_AGE_MIN:
        return _done(False, "age <60m")

    # 4. risk fetch
    risk = risk_cache.get(hit.address)
    if risk is None:
        return _done(False, "risk data unavailable")
    ctx.risk = risk

    # 5. LP locked
    if risk.lp_locked_pct is None or risk.lp_locked_pct < config.LP_LOCKED_MIN:
        return _done(False, "LP not locked")

    # 6. mint/freeze authorities must be exactly disabled (False), not merely
    # not-True -- an unknown (None) reading must not pass.
    if risk.mint_authority_enabled is not False or risk.freeze_authority_enabled is not False:
        return _done(False, "authority live")

    # 7. RugCheck danger signal
    if risk.danger_flags:
        return _done(False, "rugcheck danger")
    if risk.rugcheck_score is not None and risk.rugcheck_score > config.RUGCHECK_DANGER_MAX:
        return _done(False, "rugcheck danger")

    # 8. holder growth vs. a per-token baseline recorded on first hit
    try:
        h = birdeye.fetch_holder_data(hit.address)
    except ProviderError:
        h = None
    if h is None or h.holder_count is None:
        return _done(False, "holder data unavailable")
    ctx.holders = h

    ctx_row = store.hit_context(hit.address)
    if ctx_row is None or ctx_row["first_holder_count"] is None:
        store.ensure_hit_context(hit.address, hit.detected_at, h.holder_count)
        return _done(False, "no holder baseline yet")

    first_holder_count = ctx_row["first_holder_count"]
    grew = h.holder_count - first_holder_count
    ctx.holder_growth = grew
    grew_enough = (
        grew >= config.HOLDER_GROWTH_ABS
        or h.holder_count >= first_holder_count * (1 + config.HOLDER_GROWTH_MIN)
    )
    if not grew_enough:
        return _done(False, "holders flat")

    # 9. pump.fun serial-creator (fail-open). Only reached by tokens that
    # cleared 1-8, so at most a few pump.fun calls per hour.
    try:
        stats = pumpfun.fetch_creator_stats(hit.address)
    except Exception:  # unofficial API -- never let it break the gate
        stats = None
    if stats is not None:
        ctx.pumpfun_creator = stats.wallet
        ctx.pumpfun_coins = stats.coins_created
        if stats.coins_created >= config.PUMPFUN_MAX_COINS:
            plus = "+" if stats.capped else ""
            return _done(False, f"serial pump.fun creator ({stats.coins_created}{plus} coins)")

    return _done(True, "")
