from datetime import UTC, datetime, timedelta

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


def test_close_oldest_open_first_when_reopened_across_users():
    # same token, two users each with an open position; each closes their own
    with JournalStore() as s:
        _open(s, user="u1", entry_price=0.001)
        _open(s, user="u2", entry_price=0.002)
        _, _, p1 = s.close_trade(address=A, exit_price=0.002, now=T0, user="u1")
        _, _, p2 = s.close_trade(address=A, exit_price=0.002, now=T0, user="u2")
        assert round(p1) == 100 and round(p2) == 0
