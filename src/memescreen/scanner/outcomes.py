"""Forward price path of every alert and gate reject (spec 2026-10-21 §4).

`record()` writes one `alert_outcomes` row when an alert (or reject) happens.
`update()` runs once per cycle after the poll and advances every open row
from the token's latest snapshot: running peak/trough, +15m/+1h/+6h
checkpoints, rug detection, and closing at 6h. `advance()` is the pure core.

Nothing here may break the scan: every public entry point logs and swallows.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from memescreen.models import MarketSnapshot, RiskData
from memescreen.scanner.store import Store

_log = logging.getLogger("memescreen.scanner")

KINDS = ("spike", "spike_reject", "pump", "pump_reject", "wallet")
WINDOW = timedelta(hours=6)
CHECKPOINTS = (
    ("price_15m", timedelta(minutes=15)),
    ("price_1h", timedelta(hours=1)),
    ("price_6h", timedelta(hours=6)),
)
STALE = timedelta(minutes=30)
RUG_LIQ_FRAC = 0.2
RUG_PRICE_FRAC = 0.1


def record(
    store: Store, *, address: str, kind: str, now: datetime,
    snap: MarketSnapshot | None = None, reason: str | None = None,
    risk: RiskData | None = None, age_min: float | None = None,
) -> bool:
    """Open an outcome row. No-op (False) while one is already open for this
    (address, kind) -- a token re-rejected every cycle stays one row."""
    try:
        if store.has_open_outcome(address, kind):
            return False
        if age_min is None and snap is not None and snap.pair_created_at is not None:
            age_min = (now - snap.pair_created_at).total_seconds() / 60.0
        price = snap.price_usd if snap is not None else None
        store.insert_outcome({
            "address": address, "kind": kind, "reason": reason,
            "alerted_at": now.isoformat(),
            "symbol": snap.symbol if snap is not None else None,
            "price0": price,
            "mcap0": snap.market_cap_usd if snap is not None else None,
            "liq0": snap.liquidity_usd if snap is not None else None,
            "age_min0": age_min,
            "change_h1_0": snap.price_change_h1 if snap is not None else None,
            "insider_pct": risk.insider_pct if risk is not None else None,
            "insider_net": risk.insider_network_size if risk is not None else None,
            "creator_pct": risk.creator_pct if risk is not None else None,
            "peak_price": price, "trough_price": price, "last_price": price,
            "last_seen_at": (
                snap.fetched_at.isoformat() if snap is not None and price is not None else None
            ),
        })
        # Make sure the poller tracks it (wallet buys can be of untracked mints).
        store.upsert_seen({address}, now)
        return True
    except Exception:
        _log.exception("outcomes: record failed for %s", address[:8])
        return False


def advance(
    row: dict, snap: MarketSnapshot | None, now: datetime, newest_ts: datetime | None,
) -> dict:
    """Pure: the fields of `row` that change given its latest snapshot.
    `newest_ts` is the newest poll timestamp across ALL tokens -- staleness is
    measured against it so a poller outage never looks like a rug."""
    out: dict = {}
    cur = dict(row)
    alerted = datetime.fromisoformat(row["alerted_at"])
    last_seen = datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None
    liq_now = None

    if (
        snap is not None and snap.price_usd is not None
        and (last_seen is None or snap.fetched_at > last_seen)
    ):
        p = snap.price_usd
        out["last_price"] = p
        out["last_seen_at"] = snap.fetched_at.isoformat()
        out["peak_price"] = p if cur["peak_price"] is None else max(cur["peak_price"], p)
        out["trough_price"] = p if cur["trough_price"] is None else min(cur["trough_price"], p)
        if cur["price0"] is None:
            out["price0"] = p
            out["mcap0"] = snap.market_cap_usd
            out["liq0"] = snap.liquidity_usd
            if cur["symbol"] is None:
                out["symbol"] = snap.symbol
        cur.update(out)
        last_seen = snap.fetched_at
        liq_now = snap.liquidity_usd

    if cur["last_price"] is not None:
        for col, delta in CHECKPOINTS:
            if cur[col] is None and now >= alerted + delta:
                out[col] = cur["last_price"]
                cur[col] = cur["last_price"]

    rugged = False
    if cur["price0"]:
        if liq_now is not None and cur["liq0"] and liq_now <= cur["liq0"] * RUG_LIQ_FRAC:
            rugged = True
        if cur["last_price"] is not None and cur["last_price"] <= cur["price0"] * RUG_PRICE_FRAC:
            rugged = True
        if last_seen is not None and newest_ts is not None and newest_ts - last_seen >= STALE:
            rugged = True

    if rugged:
        out["rugged"] = 1
        out["closed"] = 1
        for col, _ in CHECKPOINTS:   # a rug counts as a loss at every later checkpoint
            if cur[col] is None:
                out[col] = cur["last_price"]
    elif now >= alerted + WINDOW:
        out["closed"] = 1
    return out


def update(store: Store, now: datetime) -> int:
    """Advance every open outcome row. Returns how many closed this call."""
    try:
        rows = store.open_outcomes()
        newest = store.newest_history_ts()
    except Exception:
        _log.exception("outcomes: could not load open rows")
        return 0

    closed = 0
    still_open = []
    for row in rows:
        try:
            fields = advance(row, store.latest_snapshot(row["address"]), now, newest)
            store.update_outcome(row["id"], fields)
            if fields.get("closed"):
                closed += 1
            else:
                still_open.append(row["address"])
        except Exception:
            _log.exception("outcomes: update failed for row %s", row.get("id"))

    try:
        # Keeps alerted tokens out of store.evict for the whole window.
        store.touch_activity(set(still_open), now)
    except Exception:
        _log.exception("outcomes: touch_activity failed")
    if closed:
        _log.info("outcomes: %d closed, %d still open", closed, len(still_open))
    return closed
