"""Maintain the rolling candidate universe: pull feeds, upsert, evict."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from memescreen import config
from memescreen.providers import birdeye, dexscreener
from memescreen.scanner.store import Store

_log = logging.getLogger(__name__)


def _safe(fetch) -> set[str]:
    try:
        return set(fetch())
    except Exception as exc:  # provider already tolerant; belt-and-braces
        _log.warning("feed call %s failed: %s", getattr(fetch, "__name__", fetch), exc)
        return set()


def seed(store: Store, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    feeds = _safe(dexscreener.fetch_feed_addresses) | _safe(birdeye.fetch_feed_addresses)
    store.upsert_seen(feeds, now)
    store.evict(now, config.UNIVERSE_CAP)
    total = len(store.universe_addresses())
    _log.info("seed: feeds=%d universe=%d", len(feeds), total)
    return total
