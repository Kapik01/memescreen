import sqlite3
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
    created = T0 - timedelta(hours=3)
    with Store(":memory:") as s:
        s.append_snapshots([
            _snap("m", T0, vol_m5=1.0),
            _snap("m", T0 + timedelta(minutes=5), vol_m5=2.0,
                  pair_created_at=created, dex="raydium", symbol="MEME"),
        ])
        snap = s.latest_snapshot("m")
        assert snap.vol_m5 == 2.0
        # the three columns the filter gate needs must survive the round trip
        assert snap.pair_created_at == created
        assert snap.dex == "raydium"
        assert snap.symbol == "MEME"
        assert s.latest_snapshot("never-seen") is None


def test_latest_snapshot_keeps_null_pair_created_at_none():
    with Store(":memory:") as s:
        s.append_snapshots([_snap("m", T0)])          # no pair_created_at/dex/symbol
        snap = s.latest_snapshot("m")
        assert snap.pair_created_at is None
        assert snap.dex is None and snap.symbol is None


def test_latest_snapshot_round_trips_price_change_h1():
    with Store(":memory:") as s:
        s.append_snapshots([_snap("m", T0, price_change_h1=42.5)])
        assert s.latest_snapshot("m").price_change_h1 == 42.5


def test_latest_snapshots_returns_newest_row_per_address():
    with Store(":memory:") as s:
        s.append_snapshots([
            _snap("a", T0, vol_m5=1.0),
            _snap("a", T0 + timedelta(minutes=5), vol_m5=2.0, price_change_h1=30.0),
            _snap("b", T0 + timedelta(minutes=1), vol_m5=9.0),
        ])
        got = {sn.address: sn for sn in s.latest_snapshots()}
        assert set(got) == {"a", "b"}
        assert got["a"].vol_m5 == 2.0
        assert got["a"].price_change_h1 == 30.0
        assert got["b"].vol_m5 == 9.0


def test_latest_snapshots_empty_when_no_history():
    with Store(":memory:") as s:
        assert s.latest_snapshots() == []


def test_pump_alert_cooldown():
    with Store(":memory:") as s:
        assert s.recent_pump_alert("m", now=T0, cooldown_h=3.0) is False
        s.record_pump_alert("m", now=T0, price_change_h1=25.0)
        assert s.recent_pump_alert("m", now=T0 + timedelta(hours=1), cooldown_h=3.0) is True
        assert s.recent_pump_alert("m", now=T0 + timedelta(hours=4), cooldown_h=3.0) is False


def test_history_returns_only_the_detector_columns():
    with Store(":memory:") as s:
        s.append_snapshots([_snap("m", T0, pair_created_at=T0 - timedelta(hours=3),
                                  dex="raydium", symbol="MEME")])
        row = s.history("m", since=T0 - timedelta(hours=1))[0]
        assert set(row) == {
            "address", "ts", "price_usd", "liquidity_usd", "market_cap_usd",
            "vol_m5", "vol_h1", "vol_h6", "vol_h24", "buys_m5", "sells_m5",
        }
        assert isinstance(row["ts"], datetime)


# The 11-column token_history that shipped before pair_created_at/dex/symbol.
_OLD_HISTORY_DDL = """
CREATE TABLE token_history (
  address TEXT NOT NULL, ts TEXT NOT NULL,
  price_usd REAL, liquidity_usd REAL, market_cap_usd REAL,
  vol_m5 REAL, vol_h1 REAL, vol_h6 REAL, vol_h24 REAL,
  buys_m5 INTEGER, sells_m5 INTEGER,
  PRIMARY KEY (address, ts)
);
"""


def test_migrates_an_existing_old_shape_database(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.executescript(_OLD_HISTORY_DDL)
    old.execute(
        "INSERT INTO token_history (address, ts, price_usd, liquidity_usd, "
        "market_cap_usd, vol_m5, vol_h1, vol_h6, vol_h24, buys_m5, sells_m5) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("m", (T0 - timedelta(minutes=5)).isoformat(), 1.0, 50000.0, 1e6,
         100.0, 1200.0, 6000.0, 24000.0, 5, 4),
    )
    old.commit()
    old.close()

    created = T0 - timedelta(hours=3)
    with Store(str(path)) as s:
        # writing the new shape against the migrated table must not raise
        s.append_snapshots([_snap("m", T0, vol_m5=2.0, pair_created_at=created,
                                  dex="raydium", symbol="MEME")])
        snap = s.latest_snapshot("m")
        assert snap.pair_created_at == created
        assert (snap.dex, snap.symbol) == ("raydium", "MEME")

        # the pre-existing old-shape row is still readable
        rows = s.history("m", since=T0 - timedelta(hours=1))
        assert [r["vol_m5"] for r in rows] == [100.0, 2.0]


def test_migration_is_idempotent_across_reopens(tmp_path):
    path = tmp_path / "twice.db"
    with Store(str(path)):
        pass
    with Store(str(path)) as s:                       # no duplicate-column error
        cols = [r[1] for r in s._conn.execute("PRAGMA table_info(token_history)")]
        assert cols.count("pair_created_at") == 1
        assert cols.count("dex") == 1 and cols.count("symbol") == 1
