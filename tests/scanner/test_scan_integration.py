"""Task 12 -- end-to-end integration for the `scan` funnel.

These tests drive the REAL `run.cycle` (seed is skipped; poll -> detect ->
filter -> alert all run for real) against a REAL `Store`. The ONLY thing faked
is outward HTTP: every DexScreener / RugCheck / Birdeye / Telegram URL is
mocked with `respx`. No scanner module (`universe`, `poller`, `detector`,
`filters`, `alerter`, `run`) is monkeypatched.

Discovered gap (see task-12-report.md): `token_history` has no
`pair_created_at` / `dex` column, so `Store.latest_snapshot()` always returns
those as `None`. `run.cycle` feeds that snapshot straight into
`filters.evaluate`, whose age check (#3) needs `pair_created_at` -- so the
fully-wired path currently stops at `fail_reason == "age unknown"` and can
never reach the holder-baseline check or a passing gate.

* `test_wired_path_polls_detects_and_fails_closed` exercises the path with
  ZERO seams and locks that honest current behaviour.
* `test_first_hit_records_holder_baseline_and_fails_closed` and
  `test_hit_with_holder_baseline_sends_telegram_alert` add ONE compensating
  seam -- `Store.latest_snapshot` is wrapped to re-attach the
  `pair_created_at` / `dex` / `symbol` the store dropped (a faithful stand-in
  for the missing persistence, not a behaviour change) -- and then prove the
  rest of the funnel end to end, including the Telegram alert.
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
TELEGRAM_URL = "https://api.telegram.org/bott/sendMessage"

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


def _mock_outward_http(mock: respx.MockRouter) -> dict:
    """Wire every outward URL the driven cycle can touch. Returns the routes
    so tests can assert call counts / bodies."""
    routes = {}
    # DexScreener batch poll -- one spike pair for ADDR.
    routes["batch"] = mock.get(url__startswith=f"{DEX_BASE}/tokens/v1/solana/").mock(
        return_value=httpx.Response(200, json=[_spike_pair(_mock_outward_http.now)])
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
    # Telegram sendMessage -- an accidental send is observable.
    routes["telegram"] = mock.route(method="POST", url=TELEGRAM_URL).mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    return routes


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setattr(config, "BIRDEYE_API_KEY", "k")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "c")


@pytest.fixture
def restore_latest_snapshot():
    """Undo any wrap of Store.latest_snapshot after the test."""
    original = Store.latest_snapshot
    yield
    Store.latest_snapshot = original


def _wrap_latest_snapshot_with_pair_age(now: datetime) -> None:
    """Compensating seam: re-attach the pair_created_at / dex / symbol that
    `token_history` does not persist, so the age filter (#3) can run. This
    stands in for the missing store column -- it does not change any scanner
    behaviour, it only supplies data the store threw away on write."""
    original = Store.latest_snapshot
    pair_created_at = now - timedelta(hours=3)  # 180 min old -> clears 60-min gate

    def _latest(self, address):
        snap = original(self, address)
        if snap is not None:
            snap.pair_created_at = pair_created_at
            snap.dex = "raydium"
            snap.symbol = "MEME"
        return snap

    Store.latest_snapshot = _latest


# --------------------------------------------------------------------------
# Scenario A -- fully wired, no seams: poll + detect run for real, the gate
# fails closed, nothing is sent.
# --------------------------------------------------------------------------
def test_wired_path_polls_detects_and_fails_closed(secrets):
    store = Store(":memory:")
    now = datetime.now(UTC)
    _seed_quiet_history(store, now)
    store.upsert_seen({ADDR}, now - timedelta(hours=8))

    with respx.mock(assert_all_called=False) as mock:
        _mock_outward_http.now = now
        routes = _mock_outward_http(mock)
        run.cycle(store, RiskCache(), datetime.now(UTC), do_seed=False)

    # the poll happened and the detector produced (and recorded) a hit
    assert routes["batch"].call_count == 1
    row = store._conn.execute(
        "SELECT passed_gate, fail_reason FROM hits WHERE address=?", (ADDR,)
    ).fetchone()
    assert row is not None, "detector should have produced an AnomalyHit"

    # the gate fails closed and NOTHING is sent
    assert row["passed_gate"] == 0
    assert routes["telegram"].call_count == 0

    # Current wired stopping point: Store.latest_snapshot drops pair_created_at,
    # so the age check fails before RugCheck/Birdeye are ever consulted. When
    # the store is taught to persist pair_created_at this assertion should be
    # updated to "no holder baseline yet" (and RugCheck/Birdeye will be hit).
    assert row["fail_reason"] == "age unknown"
    assert routes["rug"].call_count == 0
    assert routes["overview"].call_count == 0
    assert store.hit_context(ADDR) is None


# --------------------------------------------------------------------------
# Scenario A' (brief's intent) -- with the pair-age seam in place, the first
# hit for a token records the holder baseline and still fails closed.
# --------------------------------------------------------------------------
def test_first_hit_records_holder_baseline_and_fails_closed(
    secrets, restore_latest_snapshot
):
    store = Store(":memory:")
    now = datetime.now(UTC)
    _seed_quiet_history(store, now)
    store.upsert_seen({ADDR}, now - timedelta(hours=8))
    _wrap_latest_snapshot_with_pair_age(now)

    with respx.mock(assert_all_called=False) as mock:
        _mock_outward_http.now = now
        routes = _mock_outward_http(mock)
        run.cycle(store, RiskCache(), datetime.now(UTC), do_seed=False)

    # no baseline existed -> gate fails closed on exactly that, nothing sent
    row = store._conn.execute(
        "SELECT passed_gate, fail_reason FROM hits WHERE address=?", (ADDR,)
    ).fetchone()
    assert (row["passed_gate"], row["fail_reason"]) == (0, "no holder baseline yet")
    assert routes["telegram"].call_count == 0

    # RugCheck + Birdeye were consulted, and the baseline is now captured
    assert routes["rug"].call_count == 1
    assert routes["overview"].call_count == 1
    ctx_row = store.hit_context(ADDR)
    assert ctx_row is not None
    assert ctx_row["first_holder_count"] == 200


# --------------------------------------------------------------------------
# Scenario B -- with a holder baseline pre-seeded, the same wired cycle passes
# the gate and fires a Telegram alert.
# --------------------------------------------------------------------------
def test_hit_with_holder_baseline_sends_telegram_alert(
    secrets, restore_latest_snapshot
):
    store = Store(":memory:")
    now = datetime.now(UTC)
    _seed_quiet_history(store, now)
    store.upsert_seen({ADDR}, now - timedelta(hours=8))
    _wrap_latest_snapshot_with_pair_age(now)

    # baseline of 100 holders 8h ago; Birdeye now reports 200 -- past +25 and +5%
    store.ensure_hit_context(ADDR, now - timedelta(hours=8), holder_count=100)

    cycle_now = datetime.now(UTC)
    with respx.mock(assert_all_called=False) as mock:
        _mock_outward_http.now = now
        routes = _mock_outward_http(mock)
        run.cycle(store, RiskCache(), cycle_now, do_seed=False)

    # exactly one Telegram alert, addressed and carrying the token address
    assert routes["telegram"].call_count == 1
    body = json.loads(routes["telegram"].calls.last.request.content)
    assert body["chat_id"] == "c"
    assert ADDR in body["text"]

    # the gate passed and the alert is recorded (cooldown now active)
    row = store._conn.execute(
        "SELECT passed_gate FROM hits WHERE address=?", (ADDR,)
    ).fetchone()
    assert row["passed_gate"] == 1
    assert store.recent_alert(
        ADDR, cycle_now + timedelta(minutes=1), config.ALERT_COOLDOWN_H
    ) is True
