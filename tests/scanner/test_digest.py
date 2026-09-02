from datetime import UTC, datetime, timedelta

from memescreen import config
from memescreen.models import MarketSnapshot
from memescreen.scanner import digest
from memescreen.scanner.store import Store

T0 = datetime(2026, 9, 2, 22, 0, tzinfo=UTC)


def _snap(addr, *, pct, age_h=1.0, mcap=100_000.0, vol=180_000.0, liq=50_000.0, sym=None):
    return MarketSnapshot(
        address=addr, fetched_at=T0, symbol=sym or addr.upper(),
        liquidity_usd=liq, market_cap_usd=mcap, vol_h24=vol,
        pair_created_at=T0 - timedelta(hours=age_h), price_change_h1=pct, dex="raydium",
    )


def _on(monkeypatch, interval=20.0):
    monkeypatch.setattr(config, "DIGEST_INTERVAL_MIN", interval)
    monkeypatch.setattr(config, "DISCORD_DIGEST_WEBHOOK_URL",
                        "https://discord.com/api/webhooks/1/dig")


def test_rows_rank_by_1h_change_desc_and_cap_top_n(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_TOP_N", 2)
    with Store(":memory:") as s:
        s.append_snapshots([_snap("a", pct=10), _snap("b", pct=99), _snap("c", pct=50)])
        rows = digest._rows(s, T0)
    assert [r[1].address for r in rows] == ["b", "c"]


def test_rows_skip_missing_price_change_or_age():
    with Store(":memory:") as s:
        s.append_snapshots([
            _snap("a", pct=10),
            MarketSnapshot(address="b", fetched_at=T0, price_change_h1=None,
                           pair_created_at=T0 - timedelta(hours=1)),
            MarketSnapshot(address="c", fetched_at=T0, price_change_h1=40.0,
                           pair_created_at=None),
        ])
        assert [r[1].address for r in digest._rows(s, T0)] == ["a"]


def test_post_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_INTERVAL_MIN", 0.0)
    monkeypatch.setattr(config, "DISCORD_DIGEST_WEBHOOK_URL", "https://x/y")
    with Store(":memory:") as s:
        s.append_snapshots([_snap("a", pct=10)])
        assert digest.post(s, T0) == 0


def test_post_noop_when_no_webhook(monkeypatch):
    monkeypatch.setattr(config, "DIGEST_INTERVAL_MIN", 20.0)
    monkeypatch.setattr(config, "DISCORD_DIGEST_WEBHOOK_URL", None)
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", None)
    with Store(":memory:") as s:
        s.append_snapshots([_snap("a", pct=10)])
        assert digest.post(s, T0) == 0


def test_post_noop_when_board_empty(monkeypatch):
    _on(monkeypatch)
    with Store(":memory:") as s:
        assert digest.post(s, T0) == 0


def test_post_sends_and_reports(monkeypatch):
    _on(monkeypatch)
    sent = {}
    monkeypatch.setattr(digest.alerter, "send_digest",
                        lambda content: sent.setdefault("c", content) or True)
    with Store(":memory:") as s:
        s.append_snapshots([_snap("a", pct=142, sym="FOO", age_h=0.3),
                            _snap("b", pct=12, sym="BAR")])
        assert digest.post(s, T0) == 1
    assert "BOARD" in sent["c"]
    assert "$FOO" in sent["c"] and "+142%" in sent["c"]
    assert "dexscreener.com/solana/a" in sent["c"]
    # ranked: FOO before BAR
    assert sent["c"].index("$FOO") < sent["c"].index("$BAR")


def test_post_returns_zero_when_send_fails(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(digest.alerter, "send_digest", lambda content: False)
    with Store(":memory:") as s:
        s.append_snapshots([_snap("a", pct=10)])
        assert digest.post(s, T0) == 0


def test_format_digest_age_formatting():
    row = (55.0, _snap("a", pct=55, sym="ZZZ"), 18.0, 3.1)
    text = digest.format_digest([row], T0)
    assert "age 18m" in text and "vol/mcap 3.1x" in text
