"""Forward price path of every alert and gate reject (spec 2026-10-21 §4).

`record()` writes one `alert_outcomes` row when an alert (or reject) happens.
`update()` runs once per cycle after the poll and advances every open row
from the token's latest snapshot: running peak/trough, +15m/+1h/+6h
checkpoints, rug detection, and closing at 6h. `advance()` is the pure core.

Nothing here may break the scan: every public entry point logs and swallows.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
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
        # A rug is a total loss at every checkpoint not yet observed: the last
        # quoted price of a pulled / delisted token can't be sold at.
        for col, _ in CHECKPOINTS:
            if cur[col] is None:
                out[col] = 0.0
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


# --- reporting (spec §4.4) ----------------------------------------------------

_LABELS = {
    "spike": "VOLUME SPIKE",
    "spike_reject": "spike rejects (gate said no)",
    "pump": "NEW PUMP",
    "pump_reject": "pump rejects (insider/dev)",
    "wallet": "SMART WALLET BUY",
}
_BUCKETS = ("<10%", "10–30%", "≥30%", "unknown")


@dataclass
class KindStats:
    n: int
    no_price: int
    ret_15m: float | None
    ret_1h: float | None
    ret_6h: float | None
    pct_peak_50: float | None
    pct_down_50: float | None
    age_min0: float | None
    change_h1_0: float | None


def _median(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _ret(price: float | None, p0: float) -> float | None:
    return None if price is None else (price / p0 - 1.0) * 100.0


def summarize(rows: list[dict]) -> KindStats:
    priced = [r for r in rows if r["price0"]]
    n = len(priced)

    def share(pred) -> float | None:
        return 100.0 * sum(1 for r in priced if pred(r)) / n if n else None

    return KindStats(
        n=n,
        no_price=len(rows) - n,
        ret_15m=_median(_ret(r["price_15m"], r["price0"]) for r in priced),
        ret_1h=_median(_ret(r["price_1h"], r["price0"]) for r in priced),
        ret_6h=_median(_ret(r["price_6h"], r["price0"]) for r in priced),
        pct_peak_50=share(lambda r: r["peak_price"] is not None
                          and r["peak_price"] >= 1.5 * r["price0"]),
        pct_down_50=share(lambda r: bool(r["rugged"]) or (
            r["trough_price"] is not None and r["trough_price"] <= 0.5 * r["price0"])),
        age_min0=_median(r["age_min0"] for r in priced),
        change_h1_0=_median(r["change_h1_0"] for r in priced),
    )


_PASS_KIND = {"spike_reject": "spike", "pump_reject": "pump"}


def summarize_by_kind(rows: list[dict]) -> dict[str, KindStats]:
    """Reject rows for a token that also alerted in the same window are left
    out: e.g. "no holder baseline yet" fails a token's first hit and it passes
    a cycle later -- that is the same price path, not a token the gate threw
    away."""
    alerted = {(r["kind"], r.get("address")) for r in rows if r["kind"] in _PASS_KIND.values()}
    kept = [r for r in rows
            if r["kind"] not in _PASS_KIND
            or (_PASS_KIND[r["kind"]], r.get("address")) not in alerted]
    return {k: summarize([r for r in kept if r["kind"] == k])
            for k in KINDS if any(r["kind"] == k for r in kept)}


def insider_bucket(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    if pct < 10.0:
        return "<10%"
    if pct < 30.0:
        return "10–30%"
    return "≥30%"


def summarize_by_insider(rows: list[dict]) -> dict[str, KindStats]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(insider_bucket(r["insider_pct"]), []).append(r)
    return {b: summarize(groups[b]) for b in _BUCKETS if b in groups}


def _p(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.0f}%"


def _s(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0f}%"


def _hm(minutes: float | None) -> str:
    if minutes is None:
        return "n/a"
    m = int(minutes)
    return f"{m // 60}h {m % 60:02d}m"


def format_results(
    by_kind: dict[str, KindStats], by_insider: dict[str, KindStats], days: int,
) -> str:
    if not by_kind:
        return (f"no alerts recorded in the last {days}d yet — outcome tracking "
                "fills in as alerts fire.")
    lines = [f"results — last {days}d  (median price change after the alert; "
             "open alerts count with the checkpoints they have so far)", ""]
    for kind, st in by_kind.items():
        extra = f"  ({st.no_price} never priced)" if st.no_price else ""
        lines += [
            f"{_LABELS[kind]}   n={st.n}{extra}",
            f"  15m {_p(st.ret_15m)} · 1h {_p(st.ret_1h)} · 6h {_p(st.ret_6h)}",
            f"  peaked ≥+50%: {_s(st.pct_peak_50)} · fell ≥50%/rugged: {_s(st.pct_down_50)}",
            f"  at alert: age {_hm(st.age_min0)} · already {_p(st.change_h1_0)} 1h",
        ]
    if by_insider:
        lines += ["", "by insider share (all kinds):"]
        for bucket, st in by_insider.items():
            lines.append(
                f"  {bucket:<8} n={st.n:<4} 6h {_p(st.ret_6h):>5} · "
                f"≥+50% {_s(st.pct_peak_50):>4} · −50%/rug {_s(st.pct_down_50):>4}"
            )
    return "\n".join(lines)


def results_text(db_path: str, days: int, now: datetime) -> str:
    """What `!results` posts. Opens its own Store (the bot runs on another
    thread than the scanner, and sqlite connections are per-thread)."""
    with Store(db_path) as s:
        rows = s.outcomes_since(now - timedelta(days=days))
    return format_results(summarize_by_kind(rows), summarize_by_insider(rows), days)
