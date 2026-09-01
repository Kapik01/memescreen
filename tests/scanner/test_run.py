"""Task 11: the always-on scan loop. We exercise `build_logger` and `cycle`
only -- never `main`, which owns an infinite loop (that is Task 12's
integration test)."""
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
    monkeypatch.setattr(run.config, "DB_PATH", str(tmp_path / "x.db"))
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


def test_hit_is_filtered_and_alerted(monkeypatch):
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
        lambda h, s, rc, snapshot: (
            True, "",
            run.filters.FilterContext(
                risk=None, holders=None, holder_growth=30, age_min=120.0,
                dex="raydium", symbol="AAA",
            ),
        ),
    )

    def _spy(h, ctx, s, now):
        calls.append((h.address, ctx.symbol))
        return True

    monkeypatch.setattr(run.alerter, "send", _spy)

    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)

    assert calls == [("m", "AAA")]
