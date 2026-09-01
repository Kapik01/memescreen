"""Task 12 -- end-to-end integration for the `scan` funnel.

These tests drive the REAL `run.cycle` (seed is skipped; poll -> detect ->
filter -> alert all run for real) against a REAL `Store`. The ONLY thing faked
is outward HTTP: every DexScreener / RugCheck / Birdeye / Discord URL is
mocked with `respx`. NOTHING in the scanner is monkeypatched -- no module, no
`Store` method: the poll writes `pair_created_at` / `dex` / `symbol` to
`token_history` for real, so `Store.latest_snapshot()` hands the filter gate a
snapshot with a real pair age and the whole funnel runs unaided.

* `test_wired_path_polls_detects_and_fails_closed` -- first ever hit for the
  token: the gate reaches check 8, records the holder baseline, and fails
  closed on "no holder baseline yet".
* `test_first_hit_records_holder_baseline_and_fails_closed` -- same path,
  asserting the RugCheck/Birdeye consultation and the captured baseline.
* `test_hit_with_holder_baseline_sends_discord_alert` -- with a baseline
  pre-seeded, the gate passes and a Discord alert goes out.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from memescreen import config
from memescreen.models import MarketSnapshot
from memescreen.scanner import run
from memescreen.scanner.riskcache import RiskCache
from memescreen.scanner.store import Store

ADDR = "So11111111111111111111111111111111111111112"

DEX_BASE = "https://api.dexscreener.com"
RUG_BASE = "https://api.rugcheck.xyz"
BIRDEYE_BASE = "https://public-api.birdeye.so"
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1/tok"
DISCORD_POST_URL = f"{DISCORD_WEBHOOK}?wait=true"

# 60 flat, quiet 1-min history rows ending ~1 min before `now`. That is well
# past DETECT_MIN_HISTORY_MIN (45) and fills the detector's 45..10-min-back
# baseline window with a stable vol_m5 == 100.
QUIET_VOL_M5 = 100.0
FLAT_LIQUIDITY = 50_000.0


def _seed_quiet_history(store: Store, now: datetime) -> None:
    snaps = [
        MarketSnapshot(
            address=ADDR,
            fetched_at=now - timedelta(minutes=m),
            symbol="MEME",
            price_usd=0.001,
            liquidity_usd=FLAT_LIQUIDITY,
            market_cap_usd=1_000_000.0,
            vol_m5=QUIET_VOL_M5,
            vol_h1=600.0,
            vol_h6=3_600.0,
            vol_h24=14_400.0,
            buys_m5=2,
            sells_m5=1,
        )
        for m in range(1, 61)
    ]
    store.append_snapshots(snaps)


def _spike_pair(now: datetime) -> dict:
    """One DexScreener pair whose m5 volume is a genuine spike vs the seeded
    baseline: 40000 / max(median 100, VOL_M5_FLOOR 3000) = 13.3x >= 8x.
    accel = h1 / (h6 / 6) = 360000 / 100000 = 3.6x >= 3.0x. buys dominate.
    Liquidity is flat vs history so the "not draining" check holds.
    """
    created_ms = int((now - timedelta(hours=3)).timestamp() * 1000)
    return {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "PAIR",
        "baseToken": {"address": ADDR, "symbol": "MEME", "name": "Meme"},
        "priceUsd": "0.001",
        "liquidity": {"usd": FLAT_LIQUIDITY},
        "volume": {"m5": 40_000, "h1": 360_000, "h6": 600_000, "h24": 900_000},
        "txns": {"m5": {"buys": 50, "sells": 5}},
        "marketCap": 1_000_000.0,
        "pairCreatedAt": created_ms,
    }


def _clean_rugcheck_report() -> dict:
    # keys must be PRESENT with null so rugcheck maps them to "disabled" (False)
    return {
        "mintAuthority": None,
        "freezeAuthority": None,
        "markets": [{"lp": {"lpLockedPct": 100}}],
        "topHolders": [],
        "risks": [],
        "score_normalised": 5,
    }


def _mock_outward_http(mock: respx.MockRouter, now: datetime) -> dict:
    """Wire every outward URL the driven cycle can touch. Returns the routes
    so tests can assert call counts / bodies."""
    routes = {}
    # DexScreener batch poll -- one spike pair for ADDR.
    routes["batch"] = mock.get(url__startswith=f"{DEX_BASE}/tokens/v1/solana/").mock(
        return_value=httpx.Response(200, json=[_spike_pair(now)])
    )
    # DexScreener universe feeds -- empty (do_seed=False here, defensive anyway).
    for path in ("/token-boosts/top/v1", "/token-boosts/latest/v1",
                 "/token-profiles/latest/v1"):
        mock.get(f"{DEX_BASE}{path}").mock(return_value=httpx.Response(200, json=[]))
    # RugCheck report -- clean.
    routes["rug"] = mock.get(f"{RUG_BASE}/v1/tokens/{ADDR}/report").mock(
        return_value=httpx.Response(200, json=_clean_rugcheck_report())
    )
    # Birdeye holder data -- 200 holders now.
    routes["overview"] = mock.get(f"{BIRDEYE_BASE}/defi/token_overview").mock(
        return_value=httpx.Response(200, json={"data": {"holder": 200}})
    )
    routes["holders"] = mock.get(f"{BIRDEYE_BASE}/defi/v3/token/holder").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"items": [
                {"address": "h1", "percentage": 2.0},
                {"address": "h2", "percentage": 1.5},
            ]}},
        )
    )
    # Discord webhook POST -- an accidental send is observable. `?wait=true`
    # makes Discord answer 200 + the created-message object.
    routes["discord"] = mock.post(DISCORD_POST_URL).mock(
        return_value=httpx.Response(200, json={"id": "42", "channel_id": "1"})
    )
    return routes


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setattr(config, "BIRDEYE_API_KEY", "k")
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK)


# --------------------------------------------------------------------------
# Scenario A -- fully wired, no seams: poll + detect run for real. The polled
# row carries pairCreatedAt = now - 3h, so `latest_snapshot` reports a 180-min
# age that clears the 60-min gate; the first-ever hit then fails closed on the
# holder baseline it has just recorded, and nothing is sent.
# --------------------------------------------------------------------------
def test_wired_path_polls_detects_and_fails_closed(secrets):
    store = Store(":memory:")
    now = datetime.now(UTC)
    _seed_quiet_history(store, now)
    store.upsert_seen({ADDR}, now - timedelta(hours=8))

    with respx.mock(assert_all_called=False) as mock:
        routes = _mock_outward_http(mock, now)
        run.cycle(store, RiskCache(), datetime.now(UTC), do_seed=False)

    # the poll happened and the detector produced (and recorded) a hit
    assert routes["batch"].call_count == 1
    row = store._conn.execute(
        "SELECT passed_gate, fail_reason FROM hits WHERE address=?", (ADDR,)
    ).fetchone()
    assert row is not None, "detector should have produced an AnomalyHit"

    # the gate fails closed and NOTHING is sent
    assert row["passed_gate"] == 0
    assert routes["discord"].call_count == 0

    # the age check now clears on the persisted pair_created_at, so RugCheck
    # and Birdeye ARE consulted and the stop is the holder baseline
    assert row["fail_reason"] == "no holder baseline yet"
    assert routes["rug"].call_count == 1
    assert routes["overview"].call_count == 1
    assert store.hit_context(ADDR)["first_holder_count"] == 200


# --------------------------------------------------------------------------
# Scenario A' -- the same unaided path, asserting the baseline capture in full.
# --------------------------------------------------------------------------
def test_first_hit_records_holder_baseline_and_fails_closed(secrets):
    store = Store(":memory:")
    now = datetime.now(UTC)
    _seed_quiet_history(store, now)
    store.upsert_seen({ADDR}, now - timedelta(hours=8))

    with respx.mock(assert_all_called=False) as mock:
        routes = _mock_outward_http(mock, now)
        run.cycle(store, RiskCache(), datetime.now(UTC), do_seed=False)

    # no baseline existed -> gate fails closed on exactly that, nothing sent
    row = store._conn.execute(
        "SELECT passed_gate, fail_reason FROM hits WHERE address=?", (ADDR,)
    ).fetchone()
    assert (row["passed_gate"], row["fail_reason"]) == (0, "no holder baseline yet")
    assert routes["discord"].call_count == 0

    # RugCheck + Birdeye were consulted, and the baseline is now captured
    assert routes["rug"].call_count == 1
    assert routes["overview"].call_count == 1
    ctx_row = store.hit_context(ADDR)
    assert ctx_row is not None
    assert ctx_row["first_holder_count"] == 200


# --------------------------------------------------------------------------
# Scenario B -- with a holder baseline pre-seeded, the same wired cycle passes
# the gate and fires a Discord alert.
# --------------------------------------------------------------------------
def test_hit_with_holder_baseline_sends_discord_alert(secrets):
    store = Store(":memory:")
    now = datetime.now(UTC)
    _seed_quiet_history(store, now)
    store.upsert_seen({ADDR}, now - timedelta(hours=8))

    # baseline of 100 holders 8h ago; Birdeye now reports 200 -- past +25 and +5%
    store.ensure_hit_context(ADDR, now - timedelta(hours=8), holder_count=100)

    cycle_now = datetime.now(UTC)
    with respx.mock(assert_all_called=False) as mock:
        routes = _mock_outward_http(mock, now)
        run.cycle(store, RiskCache(), cycle_now, do_seed=False)

    # exactly one Discord webhook POST, carrying the token address in `content`
    assert routes["discord"].call_count == 1
    body = json.loads(routes["discord"].calls.last.request.content)
    assert ADDR in body["content"]

    # the gate passed and the alert is recorded (cooldown now active)
    row = store._conn.execute(
        "SELECT passed_gate FROM hits WHERE address=?", (ADDR,)
    ).fetchone()
    assert row["passed_gate"] == 1
    assert store.recent_alert(
        ADDR, cycle_now + timedelta(minutes=1), config.ALERT_COOLDOWN_H
    ) is True
