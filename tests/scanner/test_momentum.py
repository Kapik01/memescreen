"""NEW PUMP momentum scan: the young-token price run-up signal that runs
alongside (and independent of) the volume-spike funnel."""
from datetime import UTC, datetime, timedelta

from memescreen import config
from memescreen.models import HolderShare, MarketSnapshot, RiskData
from memescreen.scanner import momentum
from memescreen.scanner.store import Store

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _snap(addr="m", *, age_h=3.0, pct=25.0, liq=60_000.0, mcap=100_000.0,
          vol_h24=180_000.0, symbol="MEME") -> MarketSnapshot:
    return MarketSnapshot(
        address=addr,
        fetched_at=T0,
        symbol=symbol,
        price_usd=0.001,
        liquidity_usd=liq,
        market_cap_usd=mcap,
        vol_h24=vol_h24,
        pair_created_at=T0 - timedelta(hours=age_h),
        price_change_h1=pct,
        dex="raydium",
    )


def _clean_risk(top_pcts=(10.0, 5.0, 4.0)) -> RiskData:
    return RiskData(
        rugcheck_score=5,
        mint_authority_enabled=False,
        freeze_authority_enabled=False,
        lp_locked_pct=100.0,
        top_holders=[HolderShare(address=f"h{i}", pct=p) for i, p in enumerate(top_pcts)],
    )


class _Risk:
    """Minimal RiskCache stand-in."""
    def __init__(self, data):
        self._data = data
        self.calls = 0

    def get(self, address):
        self.calls += 1
        return self._data


def _rejects_on(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL",
                        "https://discord.com/api/webhooks/1/rej")


# --- _top10_pct -------------------------------------------------------------

def test_top10_pct_sums_the_ten_largest():
    risk = _clean_risk(tuple(range(1, 15)))  # 1..14
    # ten largest are 5..14 -> sum 95
    assert momentum._top10_pct(risk) == sum(range(5, 15))


def test_top10_pct_none_when_no_holder_list():
    assert momentum._top10_pct(None) is None
    assert momentum._top10_pct(_clean_risk(())) is None


# --- _passes_cheap_checks -------------------------------------------------

def test_cheap_checks_accepts_a_clean_young_runner():
    assert momentum._passes_cheap_checks(_snap(), T0) is True


def test_cheap_checks_rejects_below_price_threshold():
    assert momentum._passes_cheap_checks(_snap(pct=19.9), T0) is False


def test_cheap_checks_rejects_too_new():
    assert momentum._passes_cheap_checks(_snap(age_h=0.5), T0) is False


def test_cheap_checks_rejects_too_old():
    assert momentum._passes_cheap_checks(_snap(age_h=25.0), T0) is False


def test_cheap_checks_rejects_thin_liquidity():
    assert momentum._passes_cheap_checks(_snap(liq=9_000.0), T0) is False


def test_cheap_checks_rejects_when_volume_not_above_mcap():
    assert momentum._passes_cheap_checks(_snap(vol_h24=90_000.0, mcap=100_000.0), T0) is False


def test_cheap_checks_rejects_missing_price_change():
    snap = _snap()
    snap.price_change_h1 = None
    assert momentum._passes_cheap_checks(snap, T0) is False


# --- scan ---------------------------------------------------------------

def test_scan_noop_without_rejects_webhook(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", None)
    with Store(":memory:") as s:
        s.append_snapshots([_snap()])
        assert momentum.scan(s, _Risk(_clean_risk()), T0) == 0


def test_scan_alerts_and_records_dedup(monkeypatch):
    _rejects_on(monkeypatch)
    sent = []
    monkeypatch.setattr(momentum.alerter, "send_pump",
                        lambda snap, top10, age_min, now: sent.append(snap.address) or True)
    with Store(":memory:") as s:
        s.append_snapshots([_snap()])
        assert momentum.scan(s, _Risk(_clean_risk()), T0) == 1
        assert sent == ["m"]
        assert s.recent_pump_alert("m", T0 + timedelta(hours=1), config.PUMP_COOLDOWN_H) is True
        # second pass in the cooldown window: no second alert
        assert momentum.scan(s, _Risk(_clean_risk()), T0 + timedelta(hours=1)) == 0
        assert sent == ["m"]


def test_scan_skips_when_top10_too_concentrated(monkeypatch):
    _rejects_on(monkeypatch)
    calls = []
    monkeypatch.setattr(momentum.alerter, "send_pump",
                        lambda *a, **k: calls.append(1) or True)
    with Store(":memory:") as s:
        s.append_snapshots([_snap()])
        # top-10 sums to 45% > 30% cap
        risk = _clean_risk((20.0, 15.0, 10.0))
        assert momentum.scan(s, _Risk(risk), T0) == 0
        assert calls == []
        assert s.recent_pump_alert("m", T0 + timedelta(minutes=1), config.PUMP_COOLDOWN_H) is False


def test_scan_skips_when_no_holder_data(monkeypatch):
    _rejects_on(monkeypatch)
    monkeypatch.setattr(momentum.alerter, "send_pump", lambda *a, **k: True)
    with Store(":memory:") as s:
        s.append_snapshots([_snap()])
        assert momentum.scan(s, _Risk(None), T0) == 0


def test_scan_does_not_record_when_send_fails(monkeypatch):
    _rejects_on(monkeypatch)
    monkeypatch.setattr(momentum.alerter, "send_pump", lambda *a, **k: False)
    with Store(":memory:") as s:
        s.append_snapshots([_snap()])
        assert momentum.scan(s, _Risk(_clean_risk()), T0) == 0
        # not recorded -> a later cycle can retry
        assert s.recent_pump_alert("m", T0 + timedelta(minutes=1), config.PUMP_COOLDOWN_H) is False


def test_scan_only_checks_risk_for_cheap_check_survivors(monkeypatch):
    _rejects_on(monkeypatch)
    monkeypatch.setattr(momentum.alerter, "send_pump", lambda *a, **k: True)
    risk = _Risk(_clean_risk())
    with Store(":memory:") as s:
        s.append_snapshots([
            _snap("good"),
            _snap("slow", pct=5.0),
            _snap("old", age_h=48.0),
        ])
        momentum.scan(s, risk, T0)
        assert risk.calls == 1  # only "good" reached the RugCheck lookup
