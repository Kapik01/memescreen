"""NEW PUMP momentum scan -- a lighter, separate signal from the volume-spike
funnel in `detector` + `filters`.

Each cycle it walks every tracked token's latest snapshot and flags the ones
that are already running: 1h price change past `PUMP_PRICE_PCT`, pair age
between `PUMP_MIN_AGE_H` and `PUMP_MAX_AGE_H`, 24h volume above market cap,
liquidity above `PUMP_MIN_LIQUIDITY`, and the top-10 holders (from RugCheck,
keyless) at or under `PUMP_MAX_TOP10_PCT` of supply.

There is deliberately NO safety gate here -- these alerts go to the
#scan-rejects channel labelled unvetted. Dedup is one alert per token per
`PUMP_COOLDOWN_H`, tracked in `pump_alerts_sent`.
"""
from __future__ import annotations

import logging
from datetime import datetime

from memescreen import config
from memescreen.models import MarketSnapshot, RiskData
from memescreen.scanner import alerter
from memescreen.scanner.riskcache import RiskCache
from memescreen.scanner.store import Store

_log = logging.getLogger("memescreen.scanner")


def _top10_pct(risk: RiskData | None) -> float | None:
    """Sum of the ten largest holder shares, in percent. None when RugCheck
    gave us no holder list -- the caller treats that as 'cannot confirm' and
    skips (this check is a hard requirement, not fail-open)."""
    if risk is None or not risk.top_holders:
        return None
    return sum(sorted((h.pct for h in risk.top_holders), reverse=True)[:10])


def _passes_cheap_checks(snap: MarketSnapshot, now: datetime) -> bool:
    """Everything that needs no network call. Ordered cheapest-first."""
    if snap.price_change_h1 is None or snap.price_change_h1 < config.PUMP_PRICE_PCT:
        return False
    if snap.pair_created_at is None:
        return False
    age_h = (now - snap.pair_created_at).total_seconds() / 3600.0
    if age_h < config.PUMP_MIN_AGE_H or age_h > config.PUMP_MAX_AGE_H:
        return False
    if snap.liquidity_usd is None or snap.liquidity_usd < config.PUMP_MIN_LIQUIDITY:
        return False
    if not snap.market_cap_usd or snap.vol_h24 is None:
        return False
    if snap.vol_h24 <= snap.market_cap_usd:
        return False
    return True


def scan(store: Store, risk_cache: RiskCache, now: datetime) -> int:
    """One momentum pass over the tracked universe. Returns alerts sent."""
    if not alerter.rejects_configured():
        return 0

    sent = 0
    checked = 0
    for snap in store.latest_snapshots():
        if not _passes_cheap_checks(snap, now):
            continue
        if store.recent_pump_alert(snap.address, now, config.PUMP_COOLDOWN_H):
            continue
        checked += 1
        top10 = _top10_pct(risk_cache.get(snap.address))
        if top10 is None or top10 > config.PUMP_MAX_TOP10_PCT:
            continue
        age_min = (now - snap.pair_created_at).total_seconds() / 60.0
        if alerter.send_pump(snap, top10, age_min, now):
            store.record_pump_alert(snap.address, now, snap.price_change_h1)
            sent += 1

    if checked or sent:
        _log.info("momentum: %d candidate(s) checked, %d NEW PUMP alert(s) sent", checked, sent)
    return sent
