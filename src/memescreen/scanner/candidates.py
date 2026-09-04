"""Candidate smart-wallet discovery.

On every CONFIRMED VOLUME SPIKE or NEW PUMP send, look up who bought the
token early (Helius, best-effort) and record each wallet as a "sighting" of
that token. A wallet that recurs as an early buyer across multiple SEPARATE
alerts is an evidence-based signal drawn from the scanner's own data -- not
an unverifiable leaderboard or a Twitter personality. Surfaced in Discord via
the journal bot's `!candidates` command, which also suggests the exact
`!watch` command to start tracking one.
"""
from __future__ import annotations

import logging
from datetime import datetime

from memescreen import config
from memescreen.journal.store import JournalStore
from memescreen.providers import helius

_log = logging.getLogger("memescreen.scanner")


def record_hit(mint: str, alert_kind: str, now: datetime) -> int:
    """Best-effort: pull recent buyers of `mint` and record each as a
    sighting. Never raises -- a failure here must never affect alert
    delivery, which has already happened by the time this is called."""
    if not config.HELIUS_API_KEY:
        return 0
    try:
        buyers = helius.fetch_early_buyers(mint)
    except Exception:
        _log.exception("candidates: buyer lookup failed for %s", mint[:8])
        return 0
    if not buyers:
        return 0
    with JournalStore(config.JOURNAL_DB_PATH) as jstore:
        for buyer in buyers:
            jstore.record_candidate_sighting(buyer.wallet, mint, alert_kind, now)
    return len(buyers)
