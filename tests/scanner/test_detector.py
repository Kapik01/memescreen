"""Tests for the five-check anomaly detector (spec §5). See
scanner/detector.py for the exact check logic these exercise."""
from datetime import UTC, datetime, timedelta

import pytest

from memescreen.models import MarketSnapshot
from memescreen.scanner import detector
from memescreen.scanner.store import Store

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

QUIET = dict(
    price_usd=1.0, liquidity_usd=50000.0, market_cap_usd=1_000_000.0,
    vol_m5=100.0, vol_h1=1200.0, vol_h6=6000.0, vol_h24=24000.0,
    buys_m5=5, sells_m5=4,
)

# Values chosen so the ratio denominators are dominated by VOL_M5_FLOOR
# (3000) rather than the quiet baseline (100) -- see task-8-report.md for
# the arithmetic. This latest-row profile alone clears all five checks.
SPIKE_LATEST = dict(
    vol_m5=30000.0, vol_h1=180000.0, vol_h6=90000.0,
    buys_m5=40, sells_m5=6, liquidity_usd=50000.0,
)


def _seed(store, address, now, minutes_back, quiet=None, overrides=None):
    """Insert `minutes_back` quiet rows at 1-min spacing ending at
    `now - 1min`, then one `latest` row at `now` built from `quiet` merged
    with `overrides`. Sets first_seen to the earliest row's ts."""
    quiet = dict(QUIET, **(quiet or {}))
    overrides = overrides or {}
    earliest_ts = now - timedelta(minutes=minutes_back)
    snaps = [
        MarketSnapshot(address=address, fetched_at=now - timedelta(minutes=i), **quiet)
        for i in range(minutes_back, 0, -1)
    ]
    latest_kwargs = dict(quiet)
    latest_kwargs.update(overrides)
    snaps.append(MarketSnapshot(address=address, fetched_at=now, **latest_kwargs))
    store.append_snapshots(snaps)
    store.upsert_seen({address}, earliest_ts)


def test_clean_spike_is_detected():
    with Store(":memory:") as s:
        _seed(s, "tok", T0, minutes_back=50, overrides=SPIKE_LATEST)
        hit = detector.detect(s, "tok", T0)
        assert hit is not None
        assert hit.spike_ratio == pytest.approx(10.0, rel=1e-6)
        assert hit.accel == pytest.approx(12.0, rel=1e-6)
        assert hit.buy_ratio == pytest.approx(40 / 46, rel=1e-6)
        assert hit.liq_change == pytest.approx(1.0, rel=1e-6)
        assert 0 < hit.anomaly_score <= 100
        assert hit.first_seen_at == T0 - timedelta(minutes=50)


def test_single_print_blip_rejected_by_accel():
    with Store(":memory:") as s:
        overrides = dict(SPIKE_LATEST, vol_h1=8000.0, vol_h6=45000.0)
        _seed(s, "tok", T0, minutes_back=50, overrides=overrides)
        # sanity: this really is a low-accel case, not something else
        accel = 8000.0 / max(45000.0 / 6.0, 3000.0)
        assert accel == pytest.approx(1.0667, abs=1e-3)
        assert detector.detect(s, "tok", T0) is None


def test_sell_driven_spike_rejected():
    with Store(":memory:") as s:
        overrides = dict(SPIKE_LATEST, buys_m5=6, sells_m5=40)
        _seed(s, "tok", T0, minutes_back=50, overrides=overrides)
        assert detector.detect(s, "tok", T0) is None


def test_draining_liquidity_rejected():
    with Store(":memory:") as s:
        overrides = dict(SPIKE_LATEST, liquidity_usd=40000.0)
        _seed(s, "tok", T0, minutes_back=50,
              quiet=dict(liquidity_usd=100000.0), overrides=overrides)
        assert detector.detect(s, "tok", T0) is None


def test_not_fresh_when_already_spiking_30min_ago():
    with Store(":memory:") as s:
        minutes_back = 90
        earliest_ts = T0 - timedelta(minutes=minutes_back)
        snaps = [
            MarketSnapshot(address="tok", fetched_at=T0 - timedelta(minutes=i), **QUIET)
            for i in range(minutes_back, 0, -1)
        ]
        # Overwrite the row exactly 30 minutes back with a matching spike
        # (INSERT OR REPLACE keyed on address+ts). It sits inside check 1's
        # baseline window too, but with 35 quiet neighbors in that window
        # the median stays at the quiet level, so check 1 still fires and
        # only check 5 (freshness) is what should reject this hit.
        prior_spike = dict(QUIET, **SPIKE_LATEST)
        snaps.append(MarketSnapshot(address="tok", fetched_at=T0 - timedelta(minutes=30), **prior_spike))
        latest_kwargs = dict(QUIET, **SPIKE_LATEST)
        snaps.append(MarketSnapshot(address="tok", fetched_at=T0, **latest_kwargs))
        s.append_snapshots(snaps)
        s.upsert_seen({"tok"}, earliest_ts)

        assert detector.detect(s, "tok", T0) is None


def test_insufficient_history_returns_none():
    with Store(":memory:") as s:
        _seed(s, "tok", T0, minutes_back=15, overrides=SPIKE_LATEST)
        assert detector.detect(s, "tok", T0) is None


def test_detect_all_ranks_by_score():
    with Store(":memory:") as s:
        strong = dict(SPIKE_LATEST, vol_m5=90000.0, vol_h1=600000.0)
        weak = dict(SPIKE_LATEST)  # borderline pass, lower score
        _seed(s, "hi", T0, minutes_back=50, overrides=strong)
        _seed(s, "lo", T0, minutes_back=50, overrides=weak)

        hits = detector.detect_all(s, now=T0)

        assert [h.address for h in hits] == ["hi", "lo"]
        assert hits[0].anomaly_score >= hits[1].anomaly_score
        assert hits[0].anomaly_score > 0 and hits[1].anomaly_score > 0
