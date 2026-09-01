"""pump.fun creator lookup -> CreatorStats.

No API key. Unofficial `frontend-api-v3.pump.fun` endpoints, called only on
gate-surviving hits (a handful per hour). Every failure -- not a pump.fun
token (404), Cloudflare hiccup, shape drift -- returns None, and the caller
treats None as "cannot assess, do not block": this is an enrichment signal,
not a core safety check.
"""
from __future__ import annotations

import logging

from memescreen import config
from memescreen.models import CreatorStats
from memescreen.providers.base import ProviderError, request_json

_SOURCE = "pumpfun"
_log = logging.getLogger(__name__)

# pump.fun sits behind Cloudflare; a browser-ish UA avoids a 1010/1016 block.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; memescreen/0.1)",
    "Accept": "application/json",
}


def fetch_creator_stats(mint: str) -> CreatorStats | None:
    """(creator_wallet, coins_created) for a pump.fun token, or None if the
    token isn't a pump.fun launch or the API can't be reached."""
    try:
        coin = request_json(
            _SOURCE, "GET", f"{config.PUMPFUN_BASE}/coins/{mint}", headers=_HEADERS
        )
    except ProviderError as exc:
        _log.info("pumpfun /coins/%s -> %s (skipping creator check)", mint[:8], exc.reason)
        return None

    creator = (coin or {}).get("creator") if isinstance(coin, dict) else None
    if not isinstance(creator, str) or not creator:
        return None
    # The v3 API answers for non-pump.fun mints too, with the system program as
    # "creator" -- that's not a real launch wallet, skip.
    if set(creator) <= {"1"}:
        return None

    limit = config.PUMPFUN_COUNT_LIMIT
    try:
        made = request_json(
            _SOURCE,
            "GET",
            f"{config.PUMPFUN_BASE}/coins",
            params={"creator": creator, "offset": 0, "limit": limit},
            headers=_HEADERS,
        )
    except ProviderError as exc:
        _log.info("pumpfun creator-coins for %s -> %s", creator[:8], exc.reason)
        return None

    if not isinstance(made, list):
        return None
    count = len(made)
    # A full page means "at least `limit`" -- report the floor, it's enough to
    # trip the serial-creator threshold.
    return CreatorStats(wallet=creator, coins_created=count, capped=count >= limit)
