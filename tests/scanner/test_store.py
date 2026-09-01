from datetime import UTC, datetime, timedelta

from memescreen.models import AnomalyHit, MarketSnapshot
from memescreen.scanner.store import Store

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _snap(addr, ts, **kw):
    base = dict(price_usd=1.0, liquidity_usd=50000.0, market_cap_usd=1e6,
                vol_m5=100.0, vol_h1=1200.0, vol_h6=6000.0, vol_h24=24000.0,
                buys_m5=5, sells_m5=4)
    base.update(kw)
    return MarketSnapshot(address=addr, fetched_at=ts, **base)


def test_append_and_read_history_ascending():
    with Store(":memory:") as s:
        s.append_snapshots([_snap("m", T0 + timedelta(minutes=i), vol_m5=float(i))
                            for i in (2, 0, 1)])
        rows = s.history("m", since=T0 - timedelta(hours=1))
        assert [r["vol_m5"] for r in rows] == [0.0, 1.0, 2.0]
        assert isinstance(rows[0]["ts"], datetime)


def test_prune_history_drops_old_rows():
    with Store(":memory:") as s:
        s.append_snapshots([_snap("m", T0 - timedelta(hours=5)),
                            _snap("m", T0 - timedelta(minutes=30))])
        deleted = s.prune_history(now=T0)          # retention default 3h
        assert deleted == 1
        assert len(s.history("m", since=T0 - timedelta(days=1))) == 1


def test_universe_upsert_touch_and_first_seen():
    with Store(":memory:") as s:
        s.upsert_seen({"a", "b"}, now=T0)
        s.upsert_seen({"b", "c"}, now=T0 + timedelta(minutes=3))
        assert set(s.universe_addresses()) == {"a", "b", "c"}
        assert s.first_seen("b") == T0


def test_universe_evict_stale_then_cap():
    with Store(":memory:") as s:
        s.upsert_seen({"old"}, now=T0 - timedelta(hours=10))
        s.upsert_seen({"k1", "k2", "k3"}, now=T0)
        s.touch_activity(["k1", "k2", "k3"], now=T0)
        deleted = s.evict(now=T0, cap=2)
        addrs = set(s.universe_addresses())
        assert "old" not in addrs and len(addrs) == 2


def test_alerts_cooldown():
    with Store(":memory:") as s:
        assert s.recent_alert("m", now=T0, cooldown_h=6.0) is False
        s.record_alert("m", now=T0, anomaly_score=80)
        assert s.recent_alert("m", now=T0 + timedelta(hours=2), cooldown_h=6.0) is True
        assert s.recent_alert("m", now=T0 + timedelta(hours=7), cooldown_h=6.0) is False


def test_record_hit_and_hit_context_once():
    h = AnomalyHit(address="m", detected_at=T0, spike_ratio=10.0, accel=4.0,
                   buy_ratio=0.8, buys_m5=40, vol_m5=9000.0, vol_h1=60000.0,
                   liq_now=60000.0, liq_change=1.0, first_seen_at=T0, anomaly_score=77)
    with Store(":memory:") as s:
        s.record_hit(h, passed_gate=False, fail_reason="holders flat")
        s.ensure_hit_context("m", now=T0, holder_count=100)
        s.ensure_hit_context("m", now=T0 + timedelta(hours=8), holder_count=999)
        ctx = s.hit_context("m")
        assert ctx["first_holder_count"] == 100      # not overwritten


def test_latest_snapshot():
    with Store(":memory:") as s:
        s.append_snapshots([_snap("m", T0, vol_m5=1.0),
                            _snap("m", T0 + timedelta(minutes=5), vol_m5=2.0)])
        snap = s.latest_snapshot("m")
        assert snap.vol_m5 == 2.0
        assert s.latest_snapshot("never-seen") is None
