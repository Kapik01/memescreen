from datetime import UTC, datetime
from memescreen.scanner import universe
from memescreen.scanner.store import Store

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_seed_unions_feeds_and_populates_universe(monkeypatch):
    monkeypatch.setattr(universe.dexscreener, "fetch_feed_addresses", lambda: {"a", "b"})
    monkeypatch.setattr(universe.birdeye, "fetch_feed_addresses", lambda: {"b", "c"})
    with Store(":memory:") as s:
        n = universe.seed(s, now=T0)
        assert set(s.universe_addresses()) == {"a", "b", "c"}
        assert n == 3


def test_seed_survives_a_feed_raising(monkeypatch):
    def boom(): raise RuntimeError("should have been caught inside the provider")
    monkeypatch.setattr(universe.dexscreener, "fetch_feed_addresses", lambda: {"a"})
    monkeypatch.setattr(universe.birdeye, "fetch_feed_addresses", boom)
    with Store(":memory:") as s:
        # provider-level fault tolerance is Task 2/3; here we assert seed() itself
        # wraps the calls so one bad feed cannot abort a cycle
        n = universe.seed(s, now=T0)
        assert set(s.universe_addresses()) == {"a"}
