"""SMART WALLET BUY: poll the wallets the user is watching (managed from
Discord via the journal bot's `!watch` / `!unwatch` / `!wallets`) for new
token acquisitions, and alert.

The watchlist lives in the journal's own database (`JournalStore`) -- this
module only reads it. A freshly-watched wallet is bootstrapped silently: its
first poll just records the current newest signature as the cursor, so
watching a wallet does not dump its entire recent history into Discord.
"""
from __future__ import annotations

import logging
from datetime import datetime

from memescreen import config
from memescreen.journal.store import JournalStore
from memescreen.providers import helius
from memescreen.scanner import alerter, outcomes
from memescreen.scanner.store import Store

_log = logging.getLogger("memescreen.scanner")


def scan(now: datetime, store: Store | None = None) -> int:
    """One pass over the watchlist. Returns alerts sent. No-op without a
    Helius key or an alert destination. With a `store`, each confirmed buy
    also opens a `wallet` outcome row."""
    if not config.HELIUS_API_KEY or not alerter.wallet_configured():
        return 0

    sent = 0
    with JournalStore(config.JOURNAL_DB_PATH) as jstore:
        for wallet in jstore.list_watched():
            buys = helius.fetch_wallet_buys(wallet["address"], until_sig=wallet["last_sig"])
            if not buys:
                continue
            bootstrap = wallet["last_sig"] is None
            if not bootstrap:
                # `buys` is newest-first; alert oldest-first so Discord reads
                # in chronological order.
                for buy in reversed(buys):
                    if alerter.send_wallet_buy(wallet, buy):
                        sent += 1
                        if store is not None:
                            outcomes.record(store, address=buy.mint, kind="wallet", now=now,
                                            snap=store.latest_snapshot(buy.mint),
                                            reason=wallet["label"] or wallet["address"][:8])
            # Advance the cursor regardless of individual send outcomes -- a
            # transiently-down webhook loses that one buy rather than
            # replaying it forever (same tradeoff as the rejects feed).
            jstore.update_wallet_cursor(wallet["address"], buys[0].signature)

    if sent:
        _log.info("walletwatch: %d smart-wallet buy alert(s) sent", sent)
    return sent
