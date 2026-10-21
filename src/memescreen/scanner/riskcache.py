"""6-hour in-process cache over rugcheck.fetch_risk_data. Resets on restart —
at most one extra RugCheck call per token per process lifetime.

A result RugCheck hasn't analysed yet (`total_holders is None` -- a
minutes-old launch) is only kept for `RISK_CACHE_UNANALYSED_TTL_MIN`, so the
insider gate gets real data once RugCheck catches up instead of 6h later."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Callable

from memescreen import config
from memescreen.models import RiskData
from memescreen.providers import rugcheck
from memescreen.providers.base import ProviderError

_log = logging.getLogger(__name__)


class RiskCache:
    def __init__(
        self,
        fetch: Callable[[str], RiskData] = rugcheck.fetch_risk_data,
        ttl_h: float | None = None,
        unanalysed_ttl_min: float | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._fetch = fetch
        self._ttl = timedelta(hours=ttl_h if ttl_h is not None else config.RISK_CACHE_TTL_H)
        self._unanalysed_ttl = timedelta(minutes=(
            unanalysed_ttl_min if unanalysed_ttl_min is not None
            else config.RISK_CACHE_UNANALYSED_TTL_MIN
        ))
        self._clock = clock
        self._entries: dict[str, tuple[datetime, RiskData]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, address: str) -> RiskData | None:
        now = self._clock()
        entry = self._entries.get(address)
        if entry is not None and now - entry[0] < self._ttl_for(entry[1]):
            self._hits += 1
            return entry[1]
        self._misses += 1
        try:
            data = self._fetch(address)
        except ProviderError as exc:
            if entry is not None:
                _log.warning("rugcheck fetch failed for %s (%s); serving stale",
                             address, exc.reason)
                return entry[1]
            return None
        self._entries[address] = (now, data)
        return data

    def _ttl_for(self, data: RiskData) -> timedelta:
        return self._unanalysed_ttl if data.total_holders is None else self._ttl

    def stats(self) -> tuple[int, int]:
        return self._hits, self._misses
