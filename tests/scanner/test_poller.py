from datetime import UTC, datetime, timedelta
from memescreen.models import MarketSnapshot
from memescreen.scanner import poller
from memescreen.scanner.store import Store

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_poll_once_writes_history_and_touches_activity(monkeypatch):
    with Store(":memory:") as s:
        s.upsert_seen({"a", "b"}, now=T0)
        snaps = {
            "a": MarketSnapshot(address="a", fetched_at=T0, vol_m5=10.0, liquidity_usd=1.0),
            "b": MarketSnapshot(address="b", fetched_at=T0, vol_m5=20.0, liquidity_usd=2.0),
        }
        monkeypatch.setattr(poller.dexscreener, "fetch_snapshots", lambda addrs: snaps)
        n = poller.poll_once(s, now=T0)
        assert n == 2
        assert len(s.history("a", since=T0 - timedelta(hours=1))) == 1


def test_poll_once_empty_universe_is_noop(monkeypatch):
    with Store(":memory:") as s:
        monkeypatch.setattr(poller.dexscreener, "fetch_snapshots",
                            lambda addrs: (_ for _ in ()).throw(AssertionError("called")))
        assert poller.poll_once(s, now=T0) == 0
