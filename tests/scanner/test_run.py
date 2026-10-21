"""Task 11: the always-on scan loop. We exercise `build_logger`, `cycle` and
the pure cadence helper `_advance` -- never `main` itself, which owns an
infinite loop (that is Task 12's integration test)."""
import logging

from datetime import UTC, datetime

from memescreen.models import AnomalyHit, MarketSnapshot
from memescreen.scanner import run
from memescreen.scanner.riskcache import RiskCache
from memescreen.scanner.store import Store

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_cycle_runs_seed_poll_detect_without_error(monkeypatch):
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 0)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [])
    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=True)  # no raise


def test_cycle_runs_the_momentum_scan(monkeypatch):
    calls = []
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 0)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [])
    monkeypatch.setattr(run.momentum, "scan", lambda s, rc, now: calls.append(now) or 0)
    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)
    assert calls == [T0]


def test_cycle_records_a_candidate_sighting_on_a_confirmed_send(monkeypatch):
    hit = AnomalyHit(
        address="m", detected_at=T0, spike_ratio=20.0, accel=5.0, buy_ratio=0.9,
        buys_m5=50, vol_m5=9000.0, vol_h1=60000.0, liq_now=60000.0,
        liq_change=1.0, first_seen_at=T0, anomaly_score=88,
    )
    snap = MarketSnapshot(address="m", fetched_at=T0, symbol="AAA", dex="raydium")
    calls = []
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 1)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [hit])
    monkeypatch.setattr(run, "_latest_or_none", lambda s, a: snap)
    monkeypatch.setattr(
        run.filters, "evaluate",
        lambda h, s, rc, sn: (True, "", run.filters.FilterContext(
            risk=None, holders=None, holder_growth=30, age_min=120.0, dex="raydium")),
    )
    monkeypatch.setattr(run.candidates, "record_hit",
                        lambda mint, kind, now: calls.append((mint, kind)) or 0)

    with Store(":memory:") as s:
        monkeypatch.setattr(run.alerter, "send", lambda h, ctx, st, now: False)
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)
        assert calls == []  # not sent -> no candidate lookup

        monkeypatch.setattr(run.alerter, "send", lambda h, ctx, st, now: True)
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)
        assert calls == [("m", "spike")]


def test_cycle_runs_walletwatch_only_when_do_wallet_true(monkeypatch):
    calls = []
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 0)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [])
    monkeypatch.setattr(run.momentum, "scan", lambda s, rc, now: 0)
    monkeypatch.setattr(run.walletwatch, "scan", lambda now, store=None: calls.append(now) or 0)
    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)
        assert calls == []
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False, do_wallet=True)
        assert calls == [T0]


def test_cycle_runs_digest_only_when_do_digest_true(monkeypatch):
    calls = []
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 0)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [])
    monkeypatch.setattr(run.momentum, "scan", lambda s, rc, now: 0)
    monkeypatch.setattr(run.digest, "post", lambda s, now: calls.append(now) or 1)
    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)
        assert calls == []
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False, do_digest=True)
        assert calls == [T0]


def test_cycle_swallows_a_failing_step(monkeypatch, caplog):
    def _boom(s, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", _boom)
    caplog.set_level(logging.ERROR, logger="memescreen.scanner")
    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)  # no raise
    assert "cycle failed" in caplog.text


def test_build_logger_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "_LOG_PATH", str(tmp_path / "memescreen.log"))
    a = run.build_logger()
    b = run.build_logger()
    try:
        assert a is b
        assert len(a.handlers) == len(b.handlers)
    finally:
        for h in a.handlers[:]:
            a.removeHandler(h)
            h.close()


def test_cycle_records_a_hit_with_no_snapshot(monkeypatch):
    hit = AnomalyHit(
        address="m", detected_at=T0, spike_ratio=20.0, accel=5.0, buy_ratio=0.9,
        buys_m5=50, vol_m5=9000.0, vol_h1=60000.0, liq_now=60000.0,
        liq_change=1.0, first_seen_at=T0, anomaly_score=88,
    )
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 0)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [hit])
    monkeypatch.setattr(run, "_latest_or_none", lambda s, a: None)

    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)
        row = s._conn.execute(
            "SELECT passed_gate, fail_reason FROM hits WHERE address=?", ("m",)
        ).fetchone()
    assert (row["passed_gate"], row["fail_reason"]) == (0, "no snapshot")


def test_cycle_posts_dropped_hits_to_the_rejects_feed(monkeypatch):
    hit = AnomalyHit(
        address="m", detected_at=T0, spike_ratio=9.0, accel=3.0, buy_ratio=0.7,
        buys_m5=20, vol_m5=9000.0, vol_h1=60000.0, liq_now=60000.0,
        liq_change=1.0, first_seen_at=T0, anomaly_score=60,
    )
    snap = MarketSnapshot(address="m", fetched_at=T0, symbol="M", dex="raydium")
    rejects = []
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 1)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [hit])
    monkeypatch.setattr(run, "_latest_or_none", lambda s, a: snap)
    monkeypatch.setattr(
        run.filters, "evaluate",
        lambda h, s, rc, sn: (False, "holders flat", run.filters.FilterContext(
            risk=None, holders=None, holder_growth=1, age_min=60.0, dex="raydium")),
    )
    monkeypatch.setattr(
        run.alerter, "send_reject",
        lambda h, ctx, reason, now: rejects.append((h.address, reason)) or True,
    )

    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)

    assert rejects == [("m", "holders flat")]


def test_cycle_logs_a_drop_reason_tally(monkeypatch, caplog):
    hits = [
        AnomalyHit(address=a, detected_at=T0, spike_ratio=9.0, accel=3.0, buy_ratio=0.7,
                   buys_m5=20, vol_m5=9000.0, vol_h1=60000.0, liq_now=60000.0,
                   liq_change=1.0, first_seen_at=T0, anomaly_score=60)
        for a in ("aa", "bb", "cc")
    ]
    snap = MarketSnapshot(address="x", fetched_at=T0, symbol="X", dex="raydium")
    reasons = {"aa": "LP not locked", "bb": "LP not locked", "cc": "age <60m"}
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 1)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: hits)
    monkeypatch.setattr(run, "_latest_or_none", lambda s, a: snap)
    monkeypatch.setattr(run.momentum, "scan", lambda s, rc, now: 0)
    monkeypatch.setattr(
        run.filters, "evaluate",
        lambda h, s, rc, sn: (False, reasons[h.address], run.filters.FilterContext(
            risk=None, holders=None, holder_growth=1, age_min=60.0, dex="raydium")),
    )
    monkeypatch.setattr(run.alerter, "send_reject", lambda h, ctx, reason, now: True)

    with Store(":memory:") as s:
        with caplog.at_level(logging.INFO, logger="memescreen.scanner"):
            run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)

    assert "cycle summary: detected=3 passed=0 dropped=3" in caplog.text
    assert "LP not locked:2" in caplog.text
    assert "age <60m:1" in caplog.text


def test_cycle_does_not_post_a_reject_when_the_gate_passes(monkeypatch):
    hit = AnomalyHit(
        address="m", detected_at=T0, spike_ratio=20.0, accel=5.0, buy_ratio=0.9,
        buys_m5=50, vol_m5=9000.0, vol_h1=60000.0, liq_now=60000.0,
        liq_change=1.0, first_seen_at=T0, anomaly_score=88,
    )
    snap = MarketSnapshot(address="m", fetched_at=T0, symbol="M", dex="raydium")
    rejects = []
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 1)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [hit])
    monkeypatch.setattr(run, "_latest_or_none", lambda s, a: snap)
    monkeypatch.setattr(
        run.filters, "evaluate",
        lambda h, s, rc, sn: (True, "", run.filters.FilterContext(
            risk=None, holders=None, holder_growth=99, age_min=60.0, dex="raydium")),
    )
    monkeypatch.setattr(run.alerter, "send", lambda h, ctx, s, now: True)
    monkeypatch.setattr(
        run.alerter, "send_reject",
        lambda h, ctx, reason, now: rejects.append(h.address) or True,
    )

    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)

    assert rejects == []


# --- cadence -------------------------------------------------------------

def test_advance_skips_missed_slots_instead_of_accumulating():
    # a cycle that overran: the mark is far in the past -> clamp to now, so the
    # loop sleeps a full interval instead of firing back-to-back cycles
    assert run._advance(mark=100.0, interval=60.0, now=1000.0) == 1000.0


def test_advance_keeps_a_fixed_cadence_when_on_time():
    assert run._advance(mark=1000.0, interval=60.0, now=1010.0) == 1060.0


def test_advance_at_exactly_now_returns_now():
    # mark + interval == now: the boundary must not step backwards
    assert run._advance(mark=940.0, interval=60.0, now=1000.0) == 1000.0


def test_hit_is_filtered_and_alerted(monkeypatch, caplog):
    hit = AnomalyHit(
        address="m", detected_at=T0, spike_ratio=20.0, accel=5.0, buy_ratio=0.9,
        buys_m5=50, vol_m5=9000.0, vol_h1=60000.0, liq_now=60000.0,
        liq_change=1.0, first_seen_at=T0, anomaly_score=88,
    )
    snap = MarketSnapshot(address="m", fetched_at=T0, symbol="AAA", dex="raydium")
    calls = []

    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 1)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [hit])
    monkeypatch.setattr(run, "_latest_or_none", lambda s, a: snap)
    # FilterContext carries a stale symbol; only `run.cycle` reassigning
    # ctx.symbol = snap.symbol (Ruling B) makes the spy see "AAA".
    monkeypatch.setattr(
        run.filters, "evaluate",
        lambda h, s, rc, snapshot: (
            True, "",
            run.filters.FilterContext(
                risk=None, holders=None, holder_growth=30, age_min=120.0,
                dex="raydium", symbol="OLD",
            ),
        ),
    )

    def _spy(h, ctx, s, now):
        calls.append((h.address, ctx.symbol))
        return True

    monkeypatch.setattr(run.alerter, "send", _spy)

    with Store(":memory:") as s:
        with caplog.at_level(logging.INFO, logger="memescreen.scanner"):
            run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)

    assert calls == [("m", "AAA")]
    assert "detected 1 anomaly hit" in caplog.text
    assert "gate: m" in caplog.text and "PASSED" in caplog.text
