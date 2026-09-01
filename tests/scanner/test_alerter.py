import json
from datetime import UTC, datetime, timedelta

import httpx
import respx

from memescreen import config
from memescreen.models import AnomalyHit, HolderData, RiskData
from memescreen.scanner import alerter
from memescreen.scanner.filters import FilterContext
from memescreen.scanner.store import Store

_ADDR = "So11111111111111111111111111111111111111112"
_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
_WEBHOOK = "https://discord.com/api/webhooks/123456789/abcDEF-token"
_POST_URL = f"{_WEBHOOK}?wait=true"


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
