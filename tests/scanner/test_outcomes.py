"""Outcome tracking (spec 2026-10-21 §4): record / advance / update."""
from datetime import UTC, datetime, timedelta

from memescreen.models import MarketSnapshot, RiskData
from memescreen.scanner import outcomes
from memescreen.scanner.store import Store

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _snap(price=1.0, at=T0, liq=50_000.0, addr="tok", **kw) -> MarketSnapshot:
    return MarketSnapshot(
        address=addr, fetched_at=at, symbol="TOK", price_usd=price,
        liquidity_usd=liq, market_cap_usd=price * 1_000_000,
        pair_created_at=T0 - timedelta(minutes=95), price_change_h1=40.0, **kw,
    )


def _risk() -> RiskData:
    return RiskData(rugcheck_score=1, mint_authority_enabled=False,
                    freeze_authority_enabled=False, lp_locked_pct=100.0,
                    insider_pct=12.0, insider_network_size=48, creator_pct=3.0)


# --- record -------------------------------------------------------------------

def test_record_captures_alert_time_state():
    with Store(":memory:") as s:
        assert outcomes.record(s, address="tok", kind="spike", now=T0,
                               snap=_snap(), risk=_risk()) is True
        [row] = s.open_outcomes()
        assert row["price0"] == 1.0 and row["liq0"] == 50_000.0
        assert row["age_min0"] == 95.0 and row["change_h1_0"] == 40.0
        assert row["insider_pct"] == 12.0 and row["insider_net"] == 48
        assert row["peak_price"] == row["trough_price"] == row["last_price"] == 1.0
        assert "tok" in s.universe_addresses()      # tracked for the window


def test_record_dedups_open_row():
    with Store(":memory:") as s:
        for i in range(5):
            outcomes.record(s, address="tok", kind="spike_reject", now=T0 + timedelta(minutes=i),
                            snap=_snap(), reason="holders flat")
        outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap())
        kinds = sorted(r["kind"] for r in s.open_outcomes())
        assert kinds == ["spike", "spike_reject"]


def test_record_without_snapshot_is_allowed():
    with Store(":memory:") as s:
        assert outcomes.record(s, address="mint", kind="wallet", now=T0, reason="whale") is True
        [row] = s.open_outcomes()
        assert row["price0"] is None and row["reason"] == "whale"


def test_record_never_raises(monkeypatch):
    with Store(":memory:") as s:
        monkeypatch.setattr(s, "insert_outcome", lambda f: 1 / 0)
        assert outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap()) is False


# --- advance (pure) -------------------------------------------------------------

def _row(**over) -> dict:
    base = dict(
        id=1, address="tok", kind="spike", alerted_at=T0.isoformat(), symbol="TOK",
        price0=1.0, mcap0=1e6, liq0=50_000.0,
        price_15m=None, price_1h=None, price_6h=None,
        peak_price=1.0, trough_price=1.0, last_price=1.0,
        last_seen_at=T0.isoformat(), rugged=0, closed=0,
    )
    base.update(over)
    return base


def test_advance_tracks_peak_trough_and_checkpoints():
    row = _row()
    f = outcomes.advance(row, _snap(price=2.0, at=T0 + timedelta(minutes=16)),
                         T0 + timedelta(minutes=16), T0 + timedelta(minutes=16))
    assert f["peak_price"] == 2.0 and f["trough_price"] == 1.0
    assert f["price_15m"] == 2.0
    assert "price_1h" not in f and "closed" not in f


def test_advance_ignores_already_seen_snapshot():
    f = outcomes.advance(_row(), _snap(price=1.0, at=T0), T0 + timedelta(minutes=1), T0)
    assert "last_price" not in f and "peak_price" not in f


def test_advance_closes_at_6h_and_fills_6h_checkpoint():
    now = T0 + timedelta(hours=6, minutes=1)
    f = outcomes.advance(_row(price_15m=1.1, price_1h=1.2), _snap(price=0.8, at=now), now, now)
    assert f["price_6h"] == 0.8 and f["closed"] == 1 and "rugged" not in f


def test_advance_rug_on_liquidity_pull_fills_checkpoints():
    now = T0 + timedelta(minutes=20)
    f = outcomes.advance(_row(), _snap(price=0.5, liq=5_000.0, at=now), now, now)
    assert f["rugged"] == 1 and f["closed"] == 1
    assert f["price_15m"] == 0.5 and f["price_1h"] == 0.5 and f["price_6h"] == 0.5


def test_advance_rug_on_price_collapse():
    now = T0 + timedelta(minutes=5)
    f = outcomes.advance(_row(), _snap(price=0.09, at=now), now, now)
    assert f["rugged"] == 1


def test_advance_rug_when_token_vanishes():
    # newest poll is 31 min after this token's last snapshot
    now = T0 + timedelta(minutes=31)
    f = outcomes.advance(_row(), None, now, now)
    assert f["rugged"] == 1


def test_poller_outage_does_not_rug():
    # wall clock is 2h later but the poller produced nothing new for anyone
    now = T0 + timedelta(hours=2)
    f = outcomes.advance(_row(), None, now, T0)
    assert "rugged" not in f


def test_backfills_price0_for_unpriced_row():
    row = _row(price0=None, mcap0=None, liq0=None, peak_price=None,
               trough_price=None, last_price=None, last_seen_at=None, symbol=None)
    now = T0 + timedelta(minutes=2)
    f = outcomes.advance(row, _snap(price=3.0, at=now), now, now)
    assert f["price0"] == 3.0 and f["liq0"] == 50_000.0 and f["symbol"] == "TOK"


def test_never_priced_row_closes_at_6h_without_data():
    row = _row(price0=None, mcap0=None, liq0=None, peak_price=None,
               trough_price=None, last_price=None, last_seen_at=None)
    now = T0 + timedelta(hours=6)
    f = outcomes.advance(row, None, now, now)
    assert f == {"closed": 1}


# --- update (store loop) -------------------------------------------------------

def test_update_advances_rows_and_keeps_tokens_tracked():
    with Store(":memory:") as s:
        s.append_snapshots([_snap()])
        outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap())
        later = T0 + timedelta(minutes=16)
        s.append_snapshots([_snap(price=1.5, at=later)])
        assert outcomes.update(s, later) == 0
        [row] = s.open_outcomes()
        assert row["price_15m"] == 1.5 and row["peak_price"] == 1.5


def test_update_protects_open_rows_from_eviction():
    # 4h on, the token left every feed (last_seen_in_feed = T0 is past the 3h
    # retention cut). Only update()'s touch_activity keeps it in the universe.
    with Store(":memory:") as s:
        outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap())
        much_later = T0 + timedelta(hours=4)
        s.append_snapshots([_snap(price=1.2, at=much_later)])
        outcomes.update(s, much_later)
        s.evict(much_later, cap=10_000)
        assert "tok" in s.universe_addresses()


def test_update_survives_a_bad_row(monkeypatch):
    with Store(":memory:") as s:
        outcomes.record(s, address="a", kind="spike", now=T0, snap=_snap(addr="a"))
        outcomes.record(s, address="b", kind="spike", now=T0, snap=_snap(addr="b"))
        real = outcomes.advance

        def flaky(row, snap, now, newest):
            if row["address"] == "a":
                raise RuntimeError("boom")
            return real(row, snap, now, newest)

        monkeypatch.setattr(outcomes, "advance", flaky)
        assert outcomes.update(s, T0 + timedelta(hours=7)) == 1   # b closed, a skipped


# --- summarize / format ----------------------------------------------------------

def _closed(kind="spike", p0=1.0, p15=None, p1h=None, p6h=None, peak=None,
            trough=None, rugged=0, insider=None, age=60.0, ch=30.0) -> dict:
    return dict(kind=kind, price0=p0, price_15m=p15, price_1h=p1h, price_6h=p6h,
                peak_price=peak, trough_price=trough, rugged=rugged,
                insider_pct=insider, age_min0=age, change_h1_0=ch)


def test_summarize_medians_and_shares():
    rows = [
        _closed(p15=1.1, p1h=1.2, p6h=2.0, peak=2.5, trough=0.9),   # winner
        _closed(p15=0.9, p1h=0.5, p6h=0.2, peak=1.0, trough=0.2),   # loser
        _closed(p15=1.0, p1h=None, p6h=None, peak=1.0, trough=1.0, age=30.0),  # still open
    ]
    st = outcomes.summarize(rows)
    assert st.n == 3 and st.no_price == 0
    assert round(st.ret_15m) == 0          # median of +10, -10, 0
    assert round(st.ret_1h) == -15         # median of +20, -50
    assert round(st.ret_6h) == 10          # median of +100, -80
    assert round(st.pct_peak_50) == 33
    assert round(st.pct_down_50) == 33
    assert st.age_min0 == 60.0


def test_summarize_counts_no_price_rows():
    st = outcomes.summarize([_closed(p0=None), _closed(p15=1.5, peak=1.5, trough=1.0)])
    assert st.n == 1 and st.no_price == 1
    assert round(st.ret_15m) == 50


def test_summarize_rugged_counts_as_down():
    st = outcomes.summarize([_closed(p15=0.95, peak=1.0, trough=0.95, rugged=1)])
    assert st.pct_down_50 == 100.0


def test_insider_buckets():
    assert outcomes.insider_bucket(None) == "unknown"
    assert outcomes.insider_bucket(9.99) == "<10%"
    assert outcomes.insider_bucket(10.0) == "10–30%"
    assert outcomes.insider_bucket(30.0) == "≥30%"


def test_format_results_empty():
    text = outcomes.format_results({}, {}, 7)
    assert "no alerts recorded in the last 7d" in text


def test_format_results_all_kinds_fit_discord():
    rows = []
    for kind in outcomes.KINDS:
        for i in range(30):
            rows.append(_closed(kind=kind, p15=1.1, p1h=0.9, p6h=0.5, peak=1.8,
                                trough=0.4, insider=(i * 3.0) if i % 4 else None))
    text = outcomes.format_results(outcomes.summarize_by_kind(rows),
                                   outcomes.summarize_by_insider(rows), 30)
    assert len(text) < 1900
    for label in ("VOLUME SPIKE", "spike rejects", "NEW PUMP", "pump rejects",
                  "SMART WALLET", "by insider share"):
        assert label in text


def test_results_text_reads_the_db(tmp_path):
    db = str(tmp_path / "m.db")
    with Store(db) as s:
        outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap())
    text = outcomes.results_text(db, 7, T0 + timedelta(hours=1))
    assert "VOLUME SPIKE" in text and "n=1" in text
