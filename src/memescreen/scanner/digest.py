"""Periodic BOARD digest: every `DIGEST_INTERVAL_MIN` minutes, post the
tracked universe's top movers by 1h price change.

This is watchlist context, NOT a signal -- no safety gate, no dedup, no
scoring. It answers "what is moving right now" so the user has somewhere to
start looking between real alerts.
"""
from __future__ import annotations

import logging
from datetime import datetime

from memescreen import config
from memescreen.scanner import alerter
from memescreen.scanner.store import Store

_log = logging.getLogger("memescreen.scanner")


def _age(minutes: float) -> str:
    m = int(minutes)
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60:02d}m"


def _money(v: float | None) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:.0f}"


def _rows(store: Store, now: datetime) -> list[tuple]:
    out = []
    for s in store.latest_snapshots():
        if s.price_change_h1 is None or s.pair_created_at is None:
            continue
        age = (now - s.pair_created_at).total_seconds() / 60.0
        if age < 0:
            continue
        vmc = (
            s.vol_h24 / s.market_cap_usd
            if s.vol_h24 is not None and s.market_cap_usd
            else None
        )
        out.append((s.price_change_h1, s, age, vmc))
    out.sort(key=lambda r: r[0], reverse=True)
    return out[: max(1, config.DIGEST_TOP_N)]


def format_digest(rows: list[tuple], now: datetime) -> str:
    lines = [f"\U0001f552 BOARD — top {len(rows)} movers by 1h price  ·  {now:%H:%M}Z",
             "(watchlist context, not signals — DYOR)", ""]
    for pct, s, age, vmc in rows:
        sym = s.symbol or s.address[:8]
        vmc_s = f"{vmc:.1f}x" if vmc is not None else "n/a"
        lines.append(
            f"{pct:+.0f}%  ${sym}   age {_age(age)}  ·  vol/mcap {vmc_s}  ·  liq {_money(s.liquidity_usd)}"
        )
        lines.append(f"   https://dexscreener.com/solana/{s.address}")
    return "\n".join(lines)


def post(store: Store, now: datetime) -> int:
    """Build and send one digest. No-op (0) when disabled, unconfigured, or the
    board is empty. Returns 1 on a confirmed send."""
    if config.DIGEST_INTERVAL_MIN <= 0 or not alerter.digest_configured():
        return 0
    rows = _rows(store, now)
    if not rows:
        return 0
    ok = alerter.send_digest(format_digest(rows, now))
    _log.info("digest: top %d movers, sent=%s", len(rows), ok)
    return 1 if ok else 0
