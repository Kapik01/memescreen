"""6-hour in-process cache over rugcheck.fetch_risk_data. Resets on restart —
at most one extra RugCheck call per token per process lifetime."""
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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._fetch = fetch
        self._ttl = timedelta(hours=ttl_h if ttl_h is not None else config.RISK_CACHE_TTL_H)
        self._clock = clock
        self._entries: dict[str, tuple[datetime, RiskData]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, address: str) -> RiskData | None:
        now = self._clock()
        entry = self._entries.get(address)
        if entry is not None and now - entry[0] < self._ttl:
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

    def stats(self) -> tuple[int, int]:
        return self._hits, self._misses
