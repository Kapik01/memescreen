"""Volume-anomaly corroboration checks (spec §5).

`detect()` runs five independent checks against one token's recent
`token_history` and returns an `AnomalyHit` only when all five pass:

  1. short-window spike   -- vol_m5 vs a median baseline a few minutes back
  2. hour-scale acceleration -- vol_h1 vs vol_h6/6
  3. buy-driven            -- buys_m5 dominate sells_m5
  4. liquidity holding      -- liquidity roughly flat vs ~30min ago
  5. fresh transition       -- the spike wasn't already present ~90min ago

This module is pure: its only I/O is `store.history` / `store.first_seen` /
`store.universe_addresses`. Alert-state concerns (cooldowns, dedup) live in
filters.py, not here.
"""
from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

from memescreen import config
from memescreen.models import AnomalyHit
from memescreen.scanner.store import Store

_REQUIRED_LATEST_FIELDS = (
    "vol_m5", "vol_h1", "vol_h6", "buys_m5", "sells_m5", "liquidity_usd",
)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def detect(store: Store, address: str, now: datetime) -> AnomalyHit | None:
    history = store.history(
        address, since=now - timedelta(minutes=config.FRESH_WINDOW_MIN * 2)
    )
    if not history:
        return None

    latest = history[-1]
    if any(latest[f] is None for f in _REQUIRED_LATEST_FIELDS):
        return None

    earliest = history[0]
    history_span_min = (latest["ts"] - earliest["ts"]).total_seconds() / 60.0
    if history_span_min < config.DETECT_MIN_HISTORY_MIN:
        return None

    latest_ts = latest["ts"]

    # --- Check 1: short-window spike ---
    window_start = latest_ts - timedelta(minutes=config.BASELINE_WINDOW_MIN)
    window_end = latest_ts - timedelta(minutes=config.BASELINE_EXCLUDE_MIN)
    samples = [
        r["vol_m5"] for r in history
        if r["vol_m5"] is not None and window_start <= r["ts"] <= window_end
    ]
    if len(samples) < 3:
        return None
    baseline_m5 = statistics.median(samples)
    spike_ratio = latest["vol_m5"] / max(baseline_m5, config.VOL_M5_FLOOR)
    pass1 = spike_ratio >= config.SPIKE_RATIO_MIN and latest["vol_m5"] >= config.VOL_M5_FLOOR

    # --- Check 2: hour-scale acceleration ---
    accel = latest["vol_h1"] / max(latest["vol_h6"] / 6.0, config.VOL_M5_FLOOR)
    pass2 = accel >= config.ACCEL_MIN

    # --- Check 3: buy-driven ---
    denom = max(latest["buys_m5"] + latest["sells_m5"], 1)
    buy_ratio = latest["buys_m5"] / denom
    pass3 = buy_ratio >= config.BUY_RATIO_MIN and latest["buys_m5"] >= config.MIN_BUYS

    # --- Check 4: liquidity holding ---
    target_ts = latest_ts - timedelta(minutes=config.LIQ_REF_MIN)
    tol = timedelta(minutes=config.LIQ_REF_TOL_MIN)
    candidates = [r for r in history if r is not latest and abs(r["ts"] - target_ts) <= tol]
    ref = min(candidates, key=lambda r: abs(r["ts"] - target_ts)) if candidates else None
    if ref is None or not ref["liquidity_usd"]:
        liq_change = 1.0
    else:
        liq_change = latest["liquidity_usd"] / ref["liquidity_usd"]
    pass4 = liq_change >= config.LIQ_HOLD_MIN

    # --- Check 5: fresh transition ---
    fresh_start = latest_ts - timedelta(minutes=config.FRESH_WINDOW_MIN)
    pass5 = True
    for r in history:
        if r is latest or r["ts"] < fresh_start:
            continue
        if r["vol_m5"] is None:
            continue
        r_spike = r["vol_m5"] / max(baseline_m5, config.VOL_M5_FLOOR)
        if r_spike >= config.SPIKE_RATIO_MIN:
            pass5 = False
            break

    if not (pass1 and pass2 and pass3 and pass4 and pass5):
        return None

    anomaly_score = round(100 * _clamp01(
        0.45 * min(spike_ratio / (3 * config.SPIKE_RATIO_MIN), 1.0)
        + 0.25 * min(accel / (3 * config.ACCEL_MIN), 1.0)
        + 0.30 * min((buy_ratio - config.BUY_RATIO_MIN) / (1.0 - config.BUY_RATIO_MIN), 1.0)
    ))

    first_seen_at = store.first_seen(address) or earliest["ts"]

    return AnomalyHit(
        address=address,
        detected_at=now,
        spike_ratio=spike_ratio,
        accel=accel,
        buy_ratio=buy_ratio,
        buys_m5=latest["buys_m5"],
        vol_m5=latest["vol_m5"],
        vol_h1=latest["vol_h1"],
        liq_now=latest["liquidity_usd"],
        liq_change=liq_change,
        first_seen_at=first_seen_at,
        anomaly_score=anomaly_score,
    )


def detect_all(store: Store, now: datetime | None = None) -> list[AnomalyHit]:
    now = now or datetime.now(UTC)
    hits = []
    for address in store.universe_addresses():
        hit = detect(store, address, now)
        if hit is not None:
            hits.append(hit)
    hits.sort(key=lambda h: h.anomaly_score, reverse=True)
    return hits
