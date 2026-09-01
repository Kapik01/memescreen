"""One poll cycle: batch-refresh the universe into token_history, then prune."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from memescreen.providers import dexscreener
from memescreen.scanner.store import Store

_log = logging.getLogger(__name__)


def poll_once(store: Store, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    addrs = store.universe_addresses()
    if not addrs:
        return 0
    snaps = dexscreener.fetch_snapshots(addrs)
    store.append_snapshots(snaps.values())
    store.touch_activity(snaps.keys(), now)
    pruned = store.prune_history(now)
    _log.info("poll: polled=%d pruned=%d", len(snaps), pruned)
    return len(snaps)
