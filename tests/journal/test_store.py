from datetime import UTC, datetime, timedelta

from memescreen import config
from memescreen.journal.store import JournalStore

T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
A = "So11111111111111111111111111111111111111112"
U = "user-1"


def _open(s, **over):
    kw = dict(address=A, alert_kind="pump", symbol="RUN", entry_price=0.001,
              size_usd=None, now=T0, user=U)
    kw.update(over)
    return s.open_trade(**kw)


def test_open_then_close_computes_pnl():
    with JournalStore() as s:
        ok, _ = _open(s, entry_price=0.001)
        assert ok
        ok, msg, pnl = s.close_trade(address=A, exit_price=0.0025,
                                     now=T0 + timedelta(hours=2), user=U)
        assert ok and msg == "closed"
        assert pnl == 150.0


def test_second_open_on_same_token_is_refused():
    with JournalStore() as s:
        assert _open(s)[0] is True
        ok, msg = _open(s)
        assert ok is False
        assert "open position" in msg


def test_can_reopen_after_closing():
    with JournalStore() as s:
        _open(s)
        s.close_trade(address=A, exit_price=0.002, now=T0 + timedelta(hours=1), user=U)
        assert _open(s, now=T0 + timedelta(hours=2))[0] is True


def test_close_with_no_open_position():
    with JournalStore() as s:
        ok, msg, pnl = s.close_trade(address=A, exit_price=0.002, now=T0, user=U)
        assert (ok, pnl) == (False, None)
        assert "no open position" in msg


def test_users_are_isolated():
    with JournalStore() as s:
        _open(s, user="user-1")
        # a different user has no open position on the same token
        ok, _, _ = s.close_trade(address=A, exit_price=0.002, now=T0, user="user-2")
        assert ok is False
        assert _open(s, user="user-2")[0] is True


def test_add_note_targets_latest_trade():
    with JournalStore() as s:
        _open(s)
        assert s.add_note(address=A, note="dev doxxed", user=U) is True
        assert s.add_note(address="other", note="x", user=U) is False
        pos = s.open_positions(U)[0]
        assert pos["note"] == "dev doxxed"


def test_open_positions_and_closed_trades_partitioning():
    with JournalStore() as s:
        _open(s, address="AAA", symbol="AAA")
        _open(s, address="BBB", symbol="BBB")
        s.close_trade(address="AAA", exit_price=0.002, now=T0 + timedelta(hours=1), user=U)
        assert [p["symbol"] for p in s.open_positions(U)] == ["BBB"]
        assert [t["symbol"] for t in s.closed_trades(U)] == ["AAA"]


# --- watched_wallets --------------------------------------------------

WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def test_watch_then_list_then_unwatch():
    with JournalStore() as s:
        ok, msg = s.watch_wallet(address=WALLET, label="whale1", user=U, now=T0)
        assert (ok, msg) == (True, "watching")
        rows = s.list_watched()
        assert len(rows) == 1
        assert rows[0]["address"] == WALLET and rows[0]["label"] == "whale1"
        assert rows[0]["last_sig"] is None
        assert s.unwatch_wallet(WALLET) is True
        assert s.list_watched() == []


def test_unwatch_unknown_wallet_returns_false():
    with JournalStore() as s:
        assert s.unwatch_wallet(WALLET) is False


def test_rewatching_updates_label_not_cursor():
    with JournalStore() as s:
        s.watch_wallet(address=WALLET, label="old-label", user=U, now=T0)
        s.update_wallet_cursor(WALLET, "sig123")
        s.watch_wallet(address=WALLET, label="new-label", user=U, now=T0 + timedelta(hours=1))
        row = s.list_watched()[0]
        assert row["label"] == "new-label"
        assert row["last_sig"] == "sig123"  # untouched by re-watch


def test_watch_refuses_past_the_cap(monkeypatch):
    monkeypatch.setattr(config, "WALLET_WATCH_MAX", 2)
    with JournalStore() as s:
        assert s.watch_wallet(address="w1", label=None, user=U, now=T0)[0] is True
        assert s.watch_wallet(address="w2", label=None, user=U, now=T0)[0] is True
        ok, msg = s.watch_wallet(address="w3", label=None, user=U, now=T0)
        assert ok is False
        assert "max" in msg


def test_watch_cap_does_not_block_re_watching_an_existing_wallet(monkeypatch):
    monkeypatch.setattr(config, "WALLET_WATCH_MAX", 1)
    with JournalStore() as s:
        s.watch_wallet(address=WALLET, label="a", user=U, now=T0)
        ok, _ = s.watch_wallet(address=WALLET, label="b", user=U, now=T0)
        assert ok is True


def test_update_wallet_cursor_persists():
    with JournalStore() as s:
        s.watch_wallet(address=WALLET, label=None, user=U, now=T0)
        s.update_wallet_cursor(WALLET, "abc123")
        assert s.list_watched()[0]["last_sig"] == "abc123"


# --- candidate_sightings ------------------------------------------------

def test_record_and_list_candidates_orders_by_hit_count():
    with JournalStore() as s:
        s.record_candidate_sighting("w1", "mintA", "spike", T0)
        s.record_candidate_sighting("w1", "mintB", "pump", T0 + timedelta(hours=1))
        s.record_candidate_sighting("w2", "mintA", "spike", T0)
        rows = s.list_candidates(min_hits=1)
        assert [(r["wallet"], r["hits"]) for r in rows] == [("w1", 2), ("w2", 1)]


def test_list_candidates_respects_min_hits_threshold():
    with JournalStore() as s:
        s.record_candidate_sighting("w1", "mintA", "spike", T0)
        assert s.list_candidates(min_hits=2) == []
        s.record_candidate_sighting("w1", "mintB", "spike", T0)
        assert len(s.list_candidates(min_hits=2)) == 1


def test_same_wallet_mint_pair_only_counts_once():
    with JournalStore() as s:
        s.record_candidate_sighting("w1", "mintA", "spike", T0)
        s.record_candidate_sighting("w1", "mintA", "pump", T0 + timedelta(hours=1))
        rows = s.list_candidates(min_hits=1)
        assert rows[0]["hits"] == 1


def test_list_candidates_respects_limit():
    with JournalStore() as s:
        for i in range(5):
            s.record_candidate_sighting(f"w{i}", "mintA", "spike", T0)
            s.record_candidate_sighting(f"w{i}", "mintB", "spike", T0)
        assert len(s.list_candidates(min_hits=1, limit=2)) == 2


def test_list_candidates_empty_by_default():
    with JournalStore() as s:
        assert s.list_candidates() == []


def test_close_oldest_open_first_when_reopened_across_users():
    # same token, two users each with an open position; each closes their own
    with JournalStore() as s:
        _open(s, user="u1", entry_price=0.001)
        _open(s, user="u2", entry_price=0.002)
        _, _, p1 = s.close_trade(address=A, exit_price=0.002, now=T0, user="u1")
        _, _, p2 = s.close_trade(address=A, exit_price=0.002, now=T0, user="u2")
        assert round(p1) == 100 and round(p2) == 0
