from datetime import UTC, datetime

from memescreen import config
from memescreen.journal.store import JournalStore
from memescreen.models import EarlyBuyer
from memescreen.scanner import candidates

T0 = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
MINT = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _buyer(wallet):
    return EarlyBuyer(wallet=wallet, signature="sig", timestamp=T0)


def test_record_hit_noop_without_helius_key(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HELIUS_API_KEY", None)
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", str(tmp_path / "j.db"))
    assert candidates.record_hit(MINT, "spike", T0) == 0


def test_record_hit_stores_each_buyer(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    db = str(tmp_path / "j.db")
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", db)
    monkeypatch.setattr(candidates.helius, "fetch_early_buyers",
                        lambda mint: [_buyer("w1"), _buyer("w2")])

    assert candidates.record_hit(MINT, "spike", T0) == 2
    with JournalStore(db) as s:
        rows = s.list_candidates(min_hits=1)
        assert {r["wallet"] for r in rows} == {"w1", "w2"}


def test_record_hit_empty_buyers_returns_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", str(tmp_path / "j.db"))
    monkeypatch.setattr(candidates.helius, "fetch_early_buyers", lambda mint: [])
    assert candidates.record_hit(MINT, "spike", T0) == 0


def test_record_hit_swallows_a_lookup_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HELIUS_API_KEY", "k")
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", str(tmp_path / "j.db"))
    def _boom(mint):
        raise RuntimeError("helius down")
    monkeypatch.setattr(candidates.helius, "fetch_early_buyers", _boom)
    assert candidates.record_hit(MINT, "spike", T0) == 0  # does not raise
