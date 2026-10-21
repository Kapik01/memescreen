import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from memescreen import config
from memescreen.models import AnomalyHit, HolderData, MarketData, RiskData
from memescreen.scanner import alerter
from memescreen.scanner.filters import FilterContext
from memescreen.scanner.store import Store

_ADDR = "So11111111111111111111111111111111111111112"
_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
_WEBHOOK = "https://discord.com/api/webhooks/123456789/abcDEF-token"
_POST_URL = f"{_WEBHOOK}?wait=true"


def _market(socials=()) -> MarketData:
    return MarketData(
        name="X", symbol="X", price_usd=1.0, liquidity_usd=1.0,
        volume_24h_usd=1.0, market_cap_usd=1.0, pair_created_at=None, dex="raydium",
        socials=list(socials),
    )


@pytest.fixture(autouse=True)
def _stub_narrative(monkeypatch):
    """Default: no linked socials, no network. Individual tests override."""
    monkeypatch.setattr(alerter.dexscreener, "fetch_market_data",
                        lambda addr: _market(()))


def _hit(**over) -> AnomalyHit:
    base = dict(
        address=_ADDR,
        detected_at=_NOW,
        spike_ratio=18.0,
        accel=4.1,
        buy_ratio=0.82,
        buys_m5=87,
        vol_m5=47_200.0,
        vol_h1=210_000.0,
        liq_now=63_000.0,
        liq_change=1.04,
        first_seen_at=_NOW - timedelta(minutes=135),
        anomaly_score=82,
    )
    base.update(over)
    return AnomalyHit(**base)


def _ctx(**over) -> FilterContext:
    base = dict(
        risk=RiskData(
            rugcheck_score=1,
            mint_authority_enabled=False,
            freeze_authority_enabled=False,
            lp_locked_pct=100.0,
        ),
        holders=HolderData(holder_count=310, top10_pct=None, top20_pct=None, creator_pct=None),
        holder_growth=31,
        age_min=135.0,
        dex="raydium",
        symbol=None,
    )
    base.update(over)
    return FilterContext(**base)


def _configure(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", _WEBHOOK)


# Discord's success response to `?wait=true` is 200 + the created message object.
_OK = {"id": "999888777", "channel_id": "42", "content": "..."}


# --- format_message -------------------------------------------------------

def test_format_message_contains_key_fields():
    msg = alerter.format_message(_hit(), _ctx())
    assert _ADDR in msg
    assert f"dexscreener.com/solana/{_ADDR}" in msg
    assert str(82) in msg
    assert f"screen {_ADDR}" in msg


def test_format_message_uses_symbol_when_present():
    msg = alerter.format_message(_hit(), _ctx(symbol="WIF"))
    assert "WIF" in msg


def test_format_message_no_risk_shows_na_without_crashing():
    msg = alerter.format_message(_hit(), _ctx(risk=None))
    assert "safety: n/a" in msg


def test_format_message_no_holder_growth_shows_na():
    msg = alerter.format_message(_hit(), _ctx(holder_growth=None))
    assert "holders: n/a" in msg


def test_format_message_renders_rugcheck_score_zero_not_na():
    # 0 is the SAFEST RugCheck score -- it must never read as "unavailable"
    risk = RiskData(rugcheck_score=0, mint_authority_enabled=False,
                    freeze_authority_enabled=False, lp_locked_pct=100.0)
    msg = alerter.format_message(_hit(), _ctx(risk=risk))
    assert "RugCheck score: 0" in msg
    assert "RugCheck score: n/a" not in msg


def test_format_message_missing_rugcheck_score_shows_na():
    risk = RiskData(rugcheck_score=None, mint_authority_enabled=False,
                    freeze_authority_enabled=False, lp_locked_pct=100.0)
    assert "RugCheck score: n/a" in alerter.format_message(_hit(), _ctx(risk=risk))


def test_format_message_shows_deployer_line_when_pumpfun_coins_known():
    msg = alerter.format_message(_hit(), _ctx(pumpfun_coins=3))
    assert "Deployer: 3 pump.fun coins launched" in msg


def test_format_message_omits_deployer_line_when_unknown():
    msg = alerter.format_message(_hit(), _ctx(pumpfun_coins=None))
    assert "Deployer:" not in msg


# --- send ---------------------------------------------------------------

@respx.mock
def test_send_happy_path_posts_and_records(monkeypatch):
    _configure(monkeypatch)
    route = respx.post(_POST_URL).mock(return_value=httpx.Response(200, json=_OK))
    store = Store()

    result = alerter.send(_hit(), _ctx(), store, _NOW)

    assert result is True
    assert route.call_count == 1
    body = json.loads(route.calls.last.request.content)
    assert "content" in body and _ADDR in body["content"]
    assert body["content"].startswith("```") and body["content"].endswith("```")
    assert store.recent_alert(_ADDR, _NOW + timedelta(minutes=1), config.ALERT_COOLDOWN_H) is True


@respx.mock
def test_send_appends_narrative_socials(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        alerter.dexscreener, "fetch_market_data",
        lambda addr: _market(("https://x.com/thecoin", "https://t.me/thecoin")),
    )
    route = respx.post(_POST_URL).mock(return_value=httpx.Response(200, json=_OK))
    store = Store()

    alerter.send(_hit(), _ctx(), store, _NOW)

    body = json.loads(route.calls.last.request.content)["content"]
    assert "Narrative" in body
    assert "https://x.com/thecoin" in body
    assert "https://t.me/thecoin" in body


@respx.mock
def test_send_still_delivers_when_narrative_lookup_fails(monkeypatch):
    _configure(monkeypatch)
    def _boom(addr):
        raise RuntimeError("dexscreener down")
    monkeypatch.setattr(alerter.dexscreener, "fetch_market_data", _boom)
    route = respx.post(_POST_URL).mock(return_value=httpx.Response(200, json=_OK))
    store = Store()

    assert alerter.send(_hit(), _ctx(), store, _NOW) is True
    assert "Narrative" not in json.loads(route.calls.last.request.content)["content"]


@respx.mock
def test_send_posts_with_wait_true_query(monkeypatch):
    _configure(monkeypatch)
    route = respx.post(_POST_URL).mock(return_value=httpx.Response(200, json=_OK))
    store = Store()

    alerter.send(_hit(), _ctx(), store, _NOW)

    assert route.call_count == 1
    assert "wait=true" in str(route.calls.last.request.url)


@respx.mock
def test_send_discord_200_without_id_falls_back_and_does_not_record(monkeypatch, tmp_path):
    # A proxy or a malformed body that still returns 2xx must not count as a send.
    _configure(monkeypatch)
    missed = tmp_path / "missed_alerts.log"
    monkeypatch.setattr(alerter, "MISSED_LOG", str(missed))
    route = respx.post(_POST_URL).mock(return_value=httpx.Response(200, json={}))
    store = Store()

    result = alerter.send(_hit(), _ctx(), store, _NOW)

    assert result is False
    assert route.call_count == 1
    assert store.recent_alert(_ADDR, _NOW + timedelta(minutes=1), config.ALERT_COOLDOWN_H) is False
    assert _ADDR in missed.read_text(encoding="utf-8")


@respx.mock
def test_send_cooldown_makes_no_http_call(monkeypatch):
    _configure(monkeypatch)
    route = respx.post(_POST_URL).mock(return_value=httpx.Response(200, json=_OK))
    store = Store()
    store.record_alert(_ADDR, _NOW, 50)

    result = alerter.send(_hit(), _ctx(), store, _NOW)

    assert result is False
    assert route.call_count == 0


@respx.mock
def test_send_discord_404_falls_back_and_does_not_record(monkeypatch, tmp_path):
    # A deleted / wrong webhook is a 4xx -- request_json raises ProviderError.
    _configure(monkeypatch)
    missed = tmp_path / "missed_alerts.log"
    monkeypatch.setattr(alerter, "MISSED_LOG", str(missed))
    route = respx.post(_POST_URL).mock(return_value=httpx.Response(404))
    store = Store()

    result = alerter.send(_hit(), _ctx(), store, _NOW)

    assert result is False
    assert route.call_count == 1  # 4xx is not retried
    assert store.recent_alert(_ADDR, _NOW + timedelta(minutes=1), config.ALERT_COOLDOWN_H) is False
    assert _ADDR in missed.read_text(encoding="utf-8")


@respx.mock
def test_send_discord_500_retries_then_falls_back(monkeypatch, tmp_path):
    _configure(monkeypatch)
    missed = tmp_path / "missed_alerts.log"
    monkeypatch.setattr(alerter, "MISSED_LOG", str(missed))
    route = respx.post(_POST_URL).mock(return_value=httpx.Response(500))
    store = Store()

    result = alerter.send(_hit(), _ctx(), store, _NOW)

    assert result is False
    assert route.call_count == 2  # request_json retries once on 5xx
    assert store.recent_alert(_ADDR, _NOW + timedelta(minutes=1), config.ALERT_COOLDOWN_H) is False
    assert _ADDR in missed.read_text(encoding="utf-8")


@respx.mock
def test_send_no_webhook_prints_and_logs_no_http(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", None)
    missed = tmp_path / "missed_alerts.log"
    monkeypatch.setattr(alerter, "MISSED_LOG", str(missed))
    route = respx.post(url__startswith="https://discord.com").mock(
        return_value=httpx.Response(200, json=_OK)
    )
    store = Store()

    result = alerter.send(_hit(), _ctx(), store, _NOW)

    assert result is False
    assert route.call_count == 0
    assert _ADDR in capsys.readouterr().out
    assert store.recent_alert(_ADDR, _NOW + timedelta(minutes=1), config.ALERT_COOLDOWN_H) is False
    assert _ADDR in missed.read_text(encoding="utf-8")


# --- discord_configured ----------------------------------------------

def test_discord_configured_needs_the_webhook(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", None)
    assert alerter.discord_configured() is False

    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", _WEBHOOK)
    assert alerter.discord_configured() is True

    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "")
    assert alerter.discord_configured() is False


# --- send_reject (informational rejects feed) --------------------------

import pytest as _pytest  # noqa: E402

_REJ_HOOK = "https://discord.com/api/webhooks/999/rej-token"
_REJ_POST = f"{_REJ_HOOK}?wait=true"


@_pytest.fixture(autouse=True)
def _clear_reject_dedup():
    alerter._reject_seen.clear()
    yield
    alerter._reject_seen.clear()


@respx.mock
def test_send_reject_posts_to_the_rejects_webhook(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", _REJ_HOOK)
    route = respx.post(_REJ_POST).mock(return_value=httpx.Response(200, json=_OK))

    ok = alerter.send_reject(_hit(), _ctx(symbol="TOK"), "holders flat", _NOW)

    assert ok is True
    assert route.call_count == 1
    body = json.loads(route.calls.last.request.content)
    assert "holders flat" in body["content"]
    assert _ADDR in body["content"]
    assert "TOK" in body["content"]


@respx.mock
def test_send_reject_noop_when_webhook_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", None)
    route = respx.post(url__startswith="https://discord.com").mock(
        return_value=httpx.Response(200, json=_OK)
    )
    assert alerter.send_reject(_hit(), _ctx(), "age <30m", _NOW) is False
    assert route.call_count == 0


@respx.mock
def test_send_reject_deduped_within_cooldown(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", _REJ_HOOK)
    monkeypatch.setattr(config, "REJECT_COOLDOWN_H", 2.0)
    route = respx.post(_REJ_POST).mock(return_value=httpx.Response(200, json=_OK))

    assert alerter.send_reject(_hit(), _ctx(), "holders flat", _NOW) is True
    assert alerter.send_reject(_hit(), _ctx(), "holders flat",
                               _NOW + timedelta(hours=1)) is False  # still cooling
    assert alerter.send_reject(_hit(), _ctx(), "holders flat",
                               _NOW + timedelta(hours=3)) is True   # cooldown passed
    assert route.call_count == 2


@respx.mock
def test_send_reject_swallows_a_bad_webhook(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", _REJ_HOOK)
    respx.post(_REJ_POST).mock(return_value=httpx.Response(404))
    assert alerter.send_reject(_hit(), _ctx(), "rugcheck danger", _NOW) is False


@respx.mock
def test_send_reject_uses_address_prefix_when_no_symbol(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", _REJ_HOOK)
    route = respx.post(_REJ_POST).mock(return_value=httpx.Response(200, json=_OK))

    alerter.send_reject(_hit(), _ctx(symbol=None), "age <30m", _NOW)

    body = json.loads(route.calls.last.request.content)
    assert _ADDR[:8] in body["content"]


@respx.mock
def test_send_reject_5xx_retries_then_false(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", _REJ_HOOK)
    route = respx.post(_REJ_POST).mock(return_value=httpx.Response(500))
    assert alerter.send_reject(_hit(), _ctx(), "holders flat", _NOW) is False
    assert route.call_count == 2  # request_json retries once


@respx.mock
def test_send_reject_failed_post_still_burns_the_dedup_window(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", _REJ_HOOK)
    respx.post(_REJ_POST).mock(return_value=httpx.Response(404))

    assert alerter.send_reject(_hit(), _ctx(), "holders flat", _NOW) is False
    # marked seen despite the failure -> the next call within cooldown is a no-op
    assert _ADDR in alerter._reject_seen
    assert alerter.send_reject(_hit(), _ctx(), "holders flat",
                               _NOW + timedelta(minutes=30)) is False


from memescreen.models import MarketSnapshot as _MarketSnapshot  # noqa: E402


def _pump_snap(**over):
    base = dict(
        address=_ADDR, fetched_at=_NOW, symbol="RUN",
        liquidity_usd=61_000.0, market_cap_usd=100_000.0, vol_h24=180_000.0,
        pair_created_at=_NOW - timedelta(hours=3), price_change_h1=24.0, dex="raydium",
    )
    base.update(over)
    return _MarketSnapshot(**base)


_PUMP_HOOK = "https://discord.com/api/webhooks/777/pump-token"
_PUMP_POST = f"{_PUMP_HOOK}?wait=true"


@respx.mock
def test_send_pump_posts_the_new_pump_line(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", _PUMP_HOOK)
    route = respx.post(_PUMP_POST).mock(return_value=httpx.Response(200, json=_OK))

    ok = alerter.send_pump(_pump_snap(), top10_pct=22.0, age_min=185.0, now=_NOW)

    assert ok is True
    body = json.loads(route.calls.last.request.content)
    assert "NEW PUMP" in body["content"]
    assert "$RUN" in body["content"]
    assert "+24% 1h" in body["content"]
    assert "top10 22%" in body["content"]
    assert "vol/mcap 1.8x" in body["content"]
    assert _ADDR in body["content"]


@respx.mock
def test_send_pump_noop_when_pump_webhook_unset(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", None)
    route = respx.post(url__startswith="https://discord.com").mock(
        return_value=httpx.Response(200, json=_OK)
    )
    assert alerter.send_pump(_pump_snap(), 10.0, 120.0, _NOW) is False
    assert route.call_count == 0


@respx.mock
def test_send_pump_does_not_use_the_rejects_webhook(monkeypatch):
    # NEW PUMP has its own channel -- an unset PUMP webhook is a no-op even
    # when the rejects webhook IS set.
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", None)
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", _REJ_HOOK)
    route = respx.post(url__startswith="https://discord.com").mock(
        return_value=httpx.Response(200, json=_OK)
    )
    assert alerter.send_pump(_pump_snap(), 10.0, 120.0, _NOW) is False
    assert route.call_count == 0


@respx.mock
def test_send_pump_appends_narrative_socials(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", _PUMP_HOOK)
    monkeypatch.setattr(
        alerter.dexscreener, "fetch_market_data",
        lambda addr: _market(("https://x.com/runner",)),
    )
    route = respx.post(_PUMP_POST).mock(return_value=httpx.Response(200, json=_OK))
    alerter.send_pump(_pump_snap(), 22.0, 185.0, _NOW)
    assert "https://x.com/runner" in json.loads(route.calls.last.request.content)["content"]


@respx.mock
def test_send_pump_false_on_bad_webhook(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", _PUMP_HOOK)
    respx.post(_PUMP_POST).mock(return_value=httpx.Response(404))
    assert alerter.send_pump(_pump_snap(), 10.0, 120.0, _NOW) is False


@respx.mock
def test_send_pump_falls_back_to_address_without_symbol(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", _PUMP_HOOK)
    route = respx.post(_PUMP_POST).mock(return_value=httpx.Response(200, json=_OK))
    alerter.send_pump(_pump_snap(symbol=None), 10.0, 120.0, _NOW)
    assert _ADDR[:8] in json.loads(route.calls.last.request.content)["content"]


_DIG_HOOK = "https://discord.com/api/webhooks/555/dig-token"
_DIG_POST = f"{_DIG_HOOK}?wait=true"


@respx.mock
def test_send_digest_uses_the_digest_webhook(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_DIGEST_WEBHOOK_URL", _DIG_HOOK)
    route = respx.post(_DIG_POST).mock(return_value=httpx.Response(200, json=_OK))
    assert alerter.send_digest("BOARD ...") is True
    assert route.call_count == 1


@respx.mock
def test_send_digest_falls_back_to_pump_webhook(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_DIGEST_WEBHOOK_URL", None)
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", _PUMP_HOOK)
    route = respx.post(_PUMP_POST).mock(return_value=httpx.Response(200, json=_OK))
    assert alerter.send_digest("BOARD ...") is True
    assert route.call_count == 1


def test_send_digest_noop_when_neither_webhook_set(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_DIGEST_WEBHOOK_URL", None)
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", None)
    assert alerter.digest_configured() is False
    assert alerter.send_digest("x") is False


_WAL_HOOK = "https://discord.com/api/webhooks/321/wal-token"
_WAL_POST = f"{_WAL_HOOK}?wait=true"


def _wallet(**over):
    base = dict(address="9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM", label="whale1")
    base.update(over)
    return base


def _buy(**over):
    from memescreen.models import TokenBuy
    base = dict(signature="sig1", mint=_ADDR, amount=100.0, timestamp=_NOW)
    base.update(over)
    return TokenBuy(**base)


@respx.mock
def test_send_wallet_buy_uses_its_own_webhook(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WALLET_WEBHOOK_URL", _WAL_HOOK)
    monkeypatch.setattr(alerter.dexscreener, "fetch_market_data", lambda a: _market(()))
    route = respx.post(_WAL_POST).mock(return_value=httpx.Response(200, json=_OK))
    assert alerter.send_wallet_buy(_wallet(), _buy()) is True
    body = json.loads(route.calls.last.request.content)["content"]
    assert "SMART WALLET BUY" in body and "whale1" in body and _ADDR in body


@respx.mock
def test_send_wallet_buy_falls_back_to_pump_webhook(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WALLET_WEBHOOK_URL", None)
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", _PUMP_HOOK)
    monkeypatch.setattr(alerter.dexscreener, "fetch_market_data", lambda a: _market(()))
    route = respx.post(_PUMP_POST).mock(return_value=httpx.Response(200, json=_OK))
    assert alerter.send_wallet_buy(_wallet(), _buy()) is True
    assert route.call_count == 1


def test_send_wallet_buy_noop_when_neither_webhook_set(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WALLET_WEBHOOK_URL", None)
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", None)
    assert alerter.wallet_configured() is False
    assert alerter.send_wallet_buy(_wallet(), _buy()) is False


@respx.mock
def test_send_wallet_buy_uses_short_address_without_label(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WALLET_WEBHOOK_URL", _WAL_HOOK)
    monkeypatch.setattr(alerter.dexscreener, "fetch_market_data", lambda a: _market(()))
    route = respx.post(_WAL_POST).mock(return_value=httpx.Response(200, json=_OK))
    alerter.send_wallet_buy(_wallet(label=None), _buy())
    tag = "9WzDXwBb"
    assert tag in json.loads(route.calls.last.request.content)["content"]


@respx.mock
def test_send_wallet_buy_symbol_lookup_failure_still_sends(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WALLET_WEBHOOK_URL", _WAL_HOOK)
    def _boom(a):
        raise RuntimeError("down")
    monkeypatch.setattr(alerter.dexscreener, "fetch_market_data", _boom)
    respx.post(_WAL_POST).mock(return_value=httpx.Response(200, json=_OK))
    assert alerter.send_wallet_buy(_wallet(), _buy()) is True


def test_reject_seen_is_pruned_after_the_cooldown_window(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", None)  # no-op posts
    monkeypatch.setattr(config, "REJECT_COOLDOWN_H", 2.0)
    alerter._reject_seen["stale"] = _NOW
    # a call 3h later (webhook unset so it returns early) still runs the sweep?
    # No -- the sweep is after the config guard. Seed via a configured call:
    monkeypatch.setattr(config, "DISCORD_REJECTS_WEBHOOK_URL", _REJ_HOOK)
    import respx as _r, httpx as _h
    with _r.mock:
        _r.post(_REJ_POST).mock(return_value=_h.Response(200, json=_OK))
        alerter.send_reject(_hit(address="fresh"), _ctx(), "x", _NOW + timedelta(hours=3))
    assert "stale" not in alerter._reject_seen
    assert "fresh" in alerter._reject_seen


# --- insider line -----------------------------------------------------------

def _insider_risk(**over) -> RiskData:
    base = dict(
        rugcheck_score=1, mint_authority_enabled=False,
        freeze_authority_enabled=False, lp_locked_pct=100.0,
        insider_pct=12.0, insider_network_size=48, creator_pct=6.0,
        total_holders=1204,
    )
    base.update(over)
    return RiskData(**base)


def test_insider_line_full():
    assert alerter._insider_line(_insider_risk()) == (
        "\U0001f9e9 insiders 12% (net 48) · dev 6% · 1,204 holders"
    )


def test_insider_line_partial_and_empty():
    assert alerter._insider_line(None) is None
    assert alerter._insider_line(_insider_risk(
        insider_pct=None, insider_network_size=None, creator_pct=None, total_holders=None,
    )) is None
    assert alerter._insider_line(_insider_risk(
        insider_pct=None, insider_network_size=None, total_holders=None,
    )) == "\U0001f9e9 dev 6%"
    assert alerter._insider_line(_insider_risk(insider_network_size=0)) == (
        "\U0001f9e9 insiders 12% · dev 6% · 1,204 holders"
    )


def test_format_message_includes_insider_line():
    msg = alerter.format_message(_hit(), _ctx(risk=_insider_risk()))
    assert "insiders 12% (net 48)" in msg


@respx.mock
def test_send_pump_shows_insider_line_and_light_check_wording(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_PUMP_WEBHOOK_URL", _PUMP_HOOK)
    route = respx.post(_PUMP_POST).mock(return_value=httpx.Response(200, json=_OK))

    ok = alerter.send_pump(_pump_snap(), top10_pct=22.0, age_min=185.0, now=_NOW,
                           risk=_insider_risk())

    assert ok is True
    body = json.loads(route.calls.last.request.content)["content"]
    assert "light check only (insiders/dev)" in body
    assert "unvetted" not in body
    assert "insiders 12% (net 48)" in body
