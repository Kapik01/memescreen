# `scan` Volume-Spike Scanner — Implementation Plan (Plan 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `memescreen scan` — an always-on process that polls a wide universe of Solana tokens, learns each token's own normal trading rate, detects volume spikes that clear five corroboration checks, runs a strict safety/quality gate on the survivors, and sends deduped Telegram alerts.

**Architecture:** A funnel. `scanner/run.py` runs a `time.monotonic()`-paced loop. Every ~180s a **seed** step pulls candidate addresses from DexScreener + Birdeye feed endpoints into a rolling `universe` (SQLite, capped). Every ~60s a **poll** step batch-fetches a compact market snapshot for the whole universe (DexScreener, 30 addresses/call) and appends one row per token to `token_history`, then prunes. In-process **detect** reads each token's history and emits `AnomalyHit`s. **Filter** runs the strict gate on hits (one cached RugCheck call + one Birdeye holder call per new token; fail-closed on missing data). **Alert** sends survivors to Telegram, writing `alerts_sent` only on confirmed delivery. All of this reuses Plan 1's `providers/base.py` HTTP layer, `models.py`, and `providers/{dexscreener,rugcheck,birdeye}.py` (extended here, not rewritten).

**Tech Stack:** Python 3.11+, `httpx` (sync `Client`, already in `base.py`), `python-dotenv`, `rich`, stdlib `sqlite3` / `argparse` / `logging` / `signal` / `statistics` / `time`. Tests: `pytest` + `respx`. **Sync, not async** — spec §12's budget is ≤ 50 batch calls per 60s poll (~15s wall time sequentially), well inside the window; async would add a concept for no benefit and diverge from Plan 1.

**Spec:** `docs/superpowers/specs/2026-08-31-solana-meme-coin-screener-design.md` (Plan 2 implements §3.3, §5, §6, §7, §10 scanner constants, §11, §12, §13; Plan 1 already shipped the rest.)

## Global Constraints

- Python `>=3.11`. Runtime deps unchanged: `httpx`, `python-dotenv`, `rich`. Dev-only: `pytest`, `respx`. Add nothing.
- **No live network calls in the test suite.** Every HTTP interaction mocked with `respx`; every DB test uses a tmp-file or `:memory:` SQLite.
- Solana only. No chain parameter.
- Alert-only. No code path can move funds or hold a signing key.
- Secrets only from env / `.env`: `BIRDEYE_API_KEY`, `HELIUS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Never hardcode, never log their values.
- All tunables in `config.py` as module constants with `.env` overrides via the existing `env(key, default, cast)` helper — no inline literals in scanner logic.
- Every timestamp is timezone-aware UTC (`datetime.now(UTC)`), stored in SQLite as ISO-8601 strings (`dt.isoformat()`), read back with `datetime.fromisoformat(...)`.
- Do not modify `screen`-path behavior: `orchestrator.screen`, `rules.evaluate`, `render.render_report`, and the existing signature of `dexscreener.fetch_market_data` / `rugcheck.fetch_risk_data` / `birdeye.fetch_holder_data` stay as they are. Scanner needs are *added* alongside.
- SQLite db path: `config.DB_PATH` (default `data/memescreen.db`); `data/` is already gitignored.
- Reuse `providers.base.request_json` and `ProviderError` for every HTTP call, including Telegram.
- Existing suite is 73 passing on `main`. Keep it green; only add tests (never weaken an existing assertion).

## Current interfaces this plan builds on (already on `main`, do not change)

- `memescreen.providers.base`: `request_json(source, method, url, *, params=None, headers=None, json_body=None) -> Any`; `ProviderError(source, reason)` with `.source` / `.reason`; retries `config.HTTP_RETRIES` (1) times on 5xx / timeout / transport error, raises `ProviderError` on 4xx / exhausted retries / non-JSON body.
- `memescreen.models`: `MarketData`, `RiskData` (has `danger_flags: list[str]`), `HolderData`, `HolderShare`, `Flag`, `TokenReport`.
- `memescreen.providers.dexscreener`: `fetch_market_data(address) -> MarketData`, helpers `_pick_primary_pair(pairs)`, `_to_float(v)`.
- `memescreen.providers.rugcheck`: `fetch_risk_data(address) -> RiskData`, `_map_report(data) -> RiskData`.
- `memescreen.providers.birdeye`: `fetch_holder_data(address) -> HolderData | None` (None = no key), `_headers()`.
- `memescreen.config`: `env(...)`, `_opt_str(...)`, `DEXSCREENER_BASE`, `RUGCHECK_BASE`, `BIRDEYE_BASE`, `HTTP_TIMEOUT_SEC`, `RUGCHECK_DANGER_MAX`, the `FLAG_*` / `SCORE_*` / `VERDICT_*` deep-dive constants, and the four secrets.
- `memescreen.cli.main`: argparse with `screen` and a `scan` stub that currently prints "not available" and returns 2.

---

## File structure

| File | Responsibility |
|---|---|
| `src/memescreen/config.py` (modify) | + scanner constants (spec §10) and `DB_PATH` |
| `src/memescreen/models.py` (modify) | + `MarketSnapshot`, `AnomalyHit` |
| `src/memescreen/providers/dexscreener.py` (modify) | + `fetch_snapshots(addresses)`, + `fetch_feed_addresses()` |
| `src/memescreen/providers/birdeye.py` (modify) | + `fetch_feed_addresses()` (trending + new-listing) |
| `src/memescreen/scanner/__init__.py` (create) | empty package marker |
| `src/memescreen/scanner/riskcache.py` (create) | in-process 6h TTL cache wrapping `rugcheck.fetch_risk_data` |
| `src/memescreen/scanner/store.py` (create) | SQLite: schema, all queries, prune |
| `src/memescreen/scanner/universe.py` (create) | rolling candidate-set maintenance |
| `src/memescreen/scanner/poller.py` (create) | one poll cycle: batch-refresh history + prune |
| `src/memescreen/scanner/detector.py` (create) | history + stats → `AnomalyHit` (the 5 checks) |
| `src/memescreen/scanner/filters.py` (create) | strict safety/quality gate (spec §6) |
| `src/memescreen/scanner/alerter.py` (create) | Telegram format + send + dedupe + fallback |
| `src/memescreen/scanner/run.py` (create) | the loop; wires seed→poll→detect→filter→alert; signals; logging |
| `src/memescreen/cli.py` (modify) | `scan` subcommand → `scanner.run.main()` |
| `tests/scanner/…` + `tests/fixtures/…` | one test file per module + fixtures |

---

### Task 1: Scanner config constants + `MarketSnapshot` and `AnomalyHit` models

**Files:**
- Modify: `src/memescreen/config.py`
- Modify: `src/memescreen/models.py`
- Test: `tests/test_config.py` (append), `tests/test_models.py` (append)

**Interfaces:**
- Consumes: existing `config.env`.
- Produces:
  - `config` constants (all overridable via `.env`): `SPIKE_RATIO_MIN=8.0`, `ACCEL_MIN=3.0`, `BUY_RATIO_MIN=0.6`, `MIN_BUYS=15`, `VOL_M5_FLOOR=3000.0`, `FRESH_WINDOW_MIN=90.0`, `BASELINE_WINDOW_MIN=45.0`, `BASELINE_EXCLUDE_MIN=10.0`, `LIQ_REF_MIN=30.0`, `LIQ_REF_TOL_MIN=10.0`, `LIQ_HOLD_MIN=0.9`, `DETECT_MIN_HISTORY_MIN=45.0`, `LP_LOCKED_MIN=90.0`, `MIN_LIQUIDITY=20000.0`, `MIN_AGE_MIN=60.0`, `HOLDER_GROWTH_MIN=0.05`, `HOLDER_GROWTH_ABS=25`, `POLL_INTERVAL_SEC=60`, `SEED_INTERVAL_SEC=180`, `UNIVERSE_CAP=1500`, `ALERT_COOLDOWN_H=6.0`, `HISTORY_RETENTION_H=3.0`, `RISK_CACHE_TTL_H=6.0`, `BATCH_SIZE=30`, `DB_PATH="data/memescreen.db"`.
  - `models.MarketSnapshot` — one poll row, all fields except `address`/`fetched_at` are `float | None` / `int | None`:
    `address: str`, `fetched_at: datetime`, `price_usd`, `liquidity_usd`, `market_cap_usd`, `vol_m5`, `vol_h1`, `vol_h6`, `vol_h24`, `buys_m5: int | None`, `sells_m5: int | None`, `pair_created_at: datetime | None`, `dex: str | None`.
  - `models.AnomalyHit` — exactly spec §4: `address: str`, `detected_at: datetime`, `spike_ratio: float`, `accel: float`, `buy_ratio: float`, `buys_m5: int`, `vol_m5: float`, `vol_h1: float`, `liq_now: float`, `liq_change: float`, `first_seen_at: datetime`, `anomaly_score: int`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_config.py`:

```python
def test_scanner_defaults_present(monkeypatch):
    import importlib
    import memescreen.config as config
    importlib.reload(config)
    assert config.SPIKE_RATIO_MIN == 8.0
    assert config.MIN_BUYS == 15
    assert config.UNIVERSE_CAP == 1500
    assert config.POLL_INTERVAL_SEC == 60
    assert config.DB_PATH == "data/memescreen.db"
    assert config.HOLDER_GROWTH_ABS == 25


def test_scanner_env_override(monkeypatch):
    import importlib
    monkeypatch.setenv("SPIKE_RATIO_MIN", "12")
    monkeypatch.setenv("UNIVERSE_CAP", "500")
    import memescreen.config as config
    importlib.reload(config)
    assert config.SPIKE_RATIO_MIN == 12.0
    assert config.UNIVERSE_CAP == 500
```

Append to `tests/test_models.py`:

```python
def test_market_snapshot_optional_fields():
    from memescreen.models import MarketSnapshot
    s = MarketSnapshot(address="m", fetched_at=datetime.now(UTC))
    assert s.price_usd is None and s.buys_m5 is None and s.dex is None


def test_anomaly_hit_shape():
    from memescreen.models import AnomalyHit
    h = AnomalyHit(
        address="m", detected_at=datetime.now(UTC), spike_ratio=12.0, accel=4.0,
        buy_ratio=0.8, buys_m5=40, vol_m5=47000.0, vol_h1=210000.0,
        liq_now=63000.0, liq_change=1.04, first_seen_at=datetime.now(UTC),
        anomaly_score=82,
    )
    assert h.anomaly_score == 82
```

(`tests/test_models.py` already imports `from datetime import UTC, datetime` — confirm; add if missing.)

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_config.py tests/test_models.py -q`
Expected: FAIL — `AttributeError: module 'memescreen.config' has no attribute 'SPIKE_RATIO_MIN'`, `ImportError` for `MarketSnapshot`.

- [ ] **Step 3: Add constants to `config.py`**

Append after the `RUGCHECK_DANGER_MAX` line:

```python
# --- scanner: detection (spec §5) ---
SPIKE_RATIO_MIN: float = env("SPIKE_RATIO_MIN", 8.0, float)
ACCEL_MIN: float = env("ACCEL_MIN", 3.0, float)
BUY_RATIO_MIN: float = env("BUY_RATIO_MIN", 0.6, float)
MIN_BUYS: int = env("MIN_BUYS", 15, int)
VOL_M5_FLOOR: float = env("VOL_M5_FLOOR", 3_000.0, float)
FRESH_WINDOW_MIN: float = env("FRESH_WINDOW_MIN", 90.0, float)
BASELINE_WINDOW_MIN: float = env("BASELINE_WINDOW_MIN", 45.0, float)
BASELINE_EXCLUDE_MIN: float = env("BASELINE_EXCLUDE_MIN", 10.0, float)
LIQ_REF_MIN: float = env("LIQ_REF_MIN", 30.0, float)
LIQ_REF_TOL_MIN: float = env("LIQ_REF_TOL_MIN", 10.0, float)
LIQ_HOLD_MIN: float = env("LIQ_HOLD_MIN", 0.9, float)
DETECT_MIN_HISTORY_MIN: float = env("DETECT_MIN_HISTORY_MIN", 45.0, float)

# --- scanner: strict filter gate (spec §6) ---
LP_LOCKED_MIN: float = env("LP_LOCKED_MIN", 90.0, float)
MIN_LIQUIDITY: float = env("MIN_LIQUIDITY", 20_000.0, float)
MIN_AGE_MIN: float = env("MIN_AGE_MIN", 60.0, float)
HOLDER_GROWTH_MIN: float = env("HOLDER_GROWTH_MIN", 0.05, float)
HOLDER_GROWTH_ABS: int = env("HOLDER_GROWTH_ABS", 25, int)

# --- scanner: ops (spec §10, §12) ---
POLL_INTERVAL_SEC: int = env("POLL_INTERVAL_SEC", 60, int)
SEED_INTERVAL_SEC: int = env("SEED_INTERVAL_SEC", 180, int)
UNIVERSE_CAP: int = env("UNIVERSE_CAP", 1500, int)
ALERT_COOLDOWN_H: float = env("ALERT_COOLDOWN_H", 6.0, float)
HISTORY_RETENTION_H: float = env("HISTORY_RETENTION_H", 3.0, float)
RISK_CACHE_TTL_H: float = env("RISK_CACHE_TTL_H", 6.0, float)
BATCH_SIZE: int = env("BATCH_SIZE", 30, int)
DB_PATH: str = env("DB_PATH", "data/memescreen.db", str)
```

- [ ] **Step 4: Add dataclasses to `models.py`**

Append (module already has `from dataclasses import dataclass, field` and `from datetime import datetime`):

```python
@dataclass
class MarketSnapshot:
    """One poll-cycle reading for one token. Feeds `token_history`."""
    address: str
    fetched_at: datetime
    price_usd: float | None = None
    liquidity_usd: float | None = None
    market_cap_usd: float | None = None
    vol_m5: float | None = None
    vol_h1: float | None = None
    vol_h6: float | None = None
    vol_h24: float | None = None
    buys_m5: int | None = None
    sells_m5: int | None = None
    pair_created_at: datetime | None = None
    dex: str | None = None


@dataclass
class AnomalyHit:
    address: str
    detected_at: datetime
    spike_ratio: float
    accel: float
    buy_ratio: float
    buys_m5: int
    vol_m5: float
    vol_h1: float
    liq_now: float
    liq_change: float
    first_seen_at: datetime
    anomaly_score: int
```

- [ ] **Step 5: Run — expect pass**

Run: `python -m pytest tests/test_config.py tests/test_models.py -q`
Expected: PASS. Then `python -m pytest -q` — full suite still green (75).

- [ ] **Step 6: Commit**

```bash
git add src/memescreen/config.py src/memescreen/models.py tests/test_config.py tests/test_models.py
git commit -m "feat(scanner): config constants and MarketSnapshot/AnomalyHit models"
```

---

### Task 2: DexScreener batch snapshot + feed addresses

**Files:**
- Modify: `src/memescreen/providers/dexscreener.py`
- Test: `tests/providers/test_dexscreener.py` (append)
- Create: `tests/fixtures/dexscreener_batch.json`, `tests/fixtures/dexscreener_boosts.json`, `tests/fixtures/dexscreener_profiles.json`

**Interfaces:**
- Consumes: `config.DEXSCREENER_BASE`, `config.BATCH_SIZE`, `providers.base.request_json` / `ProviderError`, `models.MarketSnapshot`, existing `_pick_primary_pair` / `_to_float`.
- Produces:
  - `fetch_snapshots(addresses: list[str]) -> dict[str, MarketSnapshot]` — chunks `addresses` into groups of `config.BATCH_SIZE`, one `GET {DEXSCREENER_BASE}/tokens/v1/solana/{comma-joined}` per chunk. The response is a flat list of pair objects across all requested tokens; group by `baseToken.address`, pick the highest-liquidity pair per token via `_pick_primary_pair`, map to `MarketSnapshot`. Tokens with no returned pair are simply absent from the result dict. A `ProviderError` on one chunk is caught and logged (via `logging.getLogger(__name__).warning`), that chunk contributes nothing, other chunks proceed.
  - `fetch_feed_addresses() -> set[str]` — calls `GET /token-boosts/top/v1`, `GET /token-boosts/latest/v1`, `GET /token-profiles/latest/v1`; each returns a list of objects with `chainId` and `tokenAddress`; collect `tokenAddress` where `chainId == "solana"`. Any single feed call raising `ProviderError` is caught and skipped; the union of what succeeded is returned (possibly empty).

- [ ] **Step 1: Create fixtures**

Try live capture first (`curl -s "https://api.dexscreener.com/tokens/v1/solana/EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm,So11111111111111111111111111111111111111112"`, and the three feed URLs). If unavailable or shaped differently, use these and adjust field access in Step 3.

`tests/fixtures/dexscreener_batch.json` — a flat list of pairs for two tokens (`AAA…` with two pairs of differing liquidity, `BBB…` with one):

```json
[
  {"chainId":"solana","dexId":"raydium","pairAddress":"p-aaa-low","baseToken":{"address":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","name":"Aaa","symbol":"AAA"},
   "priceUsd":"0.0010","txns":{"m5":{"buys":3,"sells":1}},"volume":{"m5":100.0,"h1":2000.0,"h6":9000.0,"h24":30000.0},"liquidity":{"usd":5000.0},"marketCap":800000,"pairCreatedAt":1722470400000},
  {"chainId":"solana","dexId":"raydium","pairAddress":"p-aaa-main","baseToken":{"address":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","name":"Aaa","symbol":"AAA"},
   "priceUsd":"0.0011","txns":{"m5":{"buys":40,"sells":6}},"volume":{"m5":8000.0,"h1":60000.0,"h6":120000.0,"h24":900000.0},"liquidity":{"usd":250000.0},"marketCap":1000000,"pairCreatedAt":1722470400000},
  {"chainId":"solana","dexId":"orca","pairAddress":"p-bbb","baseToken":{"address":"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB","name":"Bbb","symbol":"BBB"},
   "priceUsd":"2.5","txns":{"m5":{"buys":10,"sells":9}},"volume":{"m5":500.0,"h1":6000.0,"h6":36000.0,"h24":150000.0},"liquidity":{"usd":90000.0},"marketCap":5000000,"pairCreatedAt":1716065147000}
]
```

`tests/fixtures/dexscreener_boosts.json`:

```json
[{"chainId":"solana","tokenAddress":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
 {"chainId":"solana","tokenAddress":"CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"},
 {"chainId":"ethereum","tokenAddress":"0xdeadbeef"}]
```

`tests/fixtures/dexscreener_profiles.json`:

```json
[{"chainId":"solana","tokenAddress":"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"},
 {"chainId":"solana","tokenAddress":"DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"}]
```

- [ ] **Step 2: Write failing tests**

Append to `tests/providers/test_dexscreener.py`:

```python
import json as _json
from pathlib import Path as _Path

import respx as _respx
import httpx as _httpx

from memescreen.providers.dexscreener import fetch_snapshots, fetch_feed_addresses

_FIX = _Path(__file__).parent.parent / "fixtures"


def _load(name):
    return _json.loads((_FIX / name).read_text())


@_respx.mock
def test_fetch_snapshots_groups_and_picks_primary_pair():
    _respx.get(url__regex=r"https://api\.dexscreener\.com/tokens/v1/solana/.+").mock(
        return_value=_httpx.Response(200, json=_load("dexscreener_batch.json"))
    )
    out = fetch_snapshots(["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                           "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"])
    assert set(out) == {"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                        "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"}
    a = out["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"]
    assert a.liquidity_usd == 250000.0          # highest-liquidity pair won
    assert a.vol_m5 == 8000.0 and a.buys_m5 == 40 and a.sells_m5 == 6
    assert a.vol_h1 == 60000.0 and a.vol_h6 == 120000.0
    assert a.dex == "raydium"


@_respx.mock
def test_fetch_snapshots_chunks_by_batch_size(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "BATCH_SIZE", 2)
    route = _respx.get(url__regex=r"https://api\.dexscreener\.com/tokens/v1/solana/.+").mock(
        return_value=_httpx.Response(200, json=[])
    )
    fetch_snapshots(["A" * 44, "B" * 44, "C" * 44, "D" * 44, "E" * 44])
    assert route.call_count == 3            # 2 + 2 + 1


@_respx.mock
def test_fetch_snapshots_one_bad_chunk_does_not_sink_the_rest(monkeypatch):
    from memescreen import config
    monkeypatch.setattr(config, "BATCH_SIZE", 2)
    responses = [_httpx.Response(500), _httpx.Response(200, json=_load("dexscreener_batch.json"))]
    _respx.get(url__regex=r"https://api\.dexscreener\.com/tokens/v1/solana/.+").mock(
        side_effect=responses
    )
    out = fetch_snapshots(["X" * 44, "Y" * 44,
                           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                           "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"])
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" in out


@_respx.mock
def test_fetch_feed_addresses_solana_only_union():
    _respx.get("https://api.dexscreener.com/token-boosts/top/v1").mock(
        return_value=_httpx.Response(200, json=_load("dexscreener_boosts.json")))
    _respx.get("https://api.dexscreener.com/token-boosts/latest/v1").mock(
        return_value=_httpx.Response(200, json=[]))
    _respx.get("https://api.dexscreener.com/token-profiles/latest/v1").mock(
        return_value=_httpx.Response(200, json=_load("dexscreener_profiles.json")))
    addrs = fetch_feed_addresses()
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" in addrs
    assert "DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD" in addrs
    assert "0xdeadbeef" not in addrs


@_respx.mock
def test_fetch_feed_addresses_tolerates_one_feed_down():
    _respx.get("https://api.dexscreener.com/token-boosts/top/v1").mock(
        return_value=_httpx.Response(500))
    _respx.get("https://api.dexscreener.com/token-boosts/latest/v1").mock(
        return_value=_httpx.Response(200, json=_load("dexscreener_boosts.json")))
    _respx.get("https://api.dexscreener.com/token-profiles/latest/v1").mock(
        return_value=_httpx.Response(200, json=[]))
    addrs = fetch_feed_addresses()
    assert "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC" in addrs
```

- [ ] **Step 3: Implement — append to `dexscreener.py`**

Add imports at top: `import logging`, `from memescreen.models import MarketSnapshot`. Add `_log = logging.getLogger(__name__)`.

```python
def _snapshot_from_pair(address: str, pair: dict, fetched_at: datetime) -> MarketSnapshot:
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    txns_m5 = (pair.get("txns") or {}).get("m5") or {}
    created_ms = pair.get("pairCreatedAt")
    created = (
        datetime.fromtimestamp(created_ms / 1000, tz=UTC)
        if isinstance(created_ms, (int, float)) and not isinstance(created_ms, bool)
        else None
    )
    return MarketSnapshot(
        address=address,
        fetched_at=fetched_at,
        price_usd=_to_float(pair.get("priceUsd")),
        liquidity_usd=_to_float(liquidity.get("usd")),
        market_cap_usd=_to_float(pair.get("marketCap")),
        vol_m5=_to_float(volume.get("m5")),
        vol_h1=_to_float(volume.get("h1")),
        vol_h6=_to_float(volume.get("h6")),
        vol_h24=_to_float(volume.get("h24")),
        buys_m5=int(txns_m5["buys"]) if isinstance(txns_m5.get("buys"), (int, float)) else None,
        sells_m5=int(txns_m5["sells"]) if isinstance(txns_m5.get("sells"), (int, float)) else None,
        pair_created_at=created,
        dex=pair.get("dexId"),
    )


def _chunks(seq: list[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_snapshots(addresses: list[str]) -> dict[str, "MarketSnapshot"]:
    from memescreen import config as _cfg
    now = datetime.now(UTC)
    out: dict[str, MarketSnapshot] = {}
    for chunk in _chunks(list(addresses), max(1, _cfg.BATCH_SIZE)):
        url = f"{config.DEXSCREENER_BASE}/tokens/v1/solana/{','.join(chunk)}"
        try:
            payload = request_json(_SOURCE, "GET", url)
        except ProviderError as exc:
            _log.warning("dexscreener batch chunk failed: %s", exc.reason)
            continue
        if not isinstance(payload, list):
            continue
        by_token: dict[str, list[dict]] = {}
        for pair in payload:
            addr = ((pair or {}).get("baseToken") or {}).get("address")
            if addr:
                by_token.setdefault(addr, []).append(pair)
        for addr, pairs in by_token.items():
            out[addr] = _snapshot_from_pair(addr, _pick_primary_pair(pairs), now)
    return out


def fetch_feed_addresses() -> set[str]:
    paths = ("/token-boosts/top/v1", "/token-boosts/latest/v1", "/token-profiles/latest/v1")
    found: set[str] = set()
    for path in paths:
        try:
            payload = request_json(_SOURCE, "GET", f"{config.DEXSCREENER_BASE}{path}")
        except ProviderError as exc:
            _log.warning("dexscreener feed %s failed: %s", path, exc.reason)
            continue
        for item in payload if isinstance(payload, list) else []:
            if (item or {}).get("chainId") == "solana" and item.get("tokenAddress"):
                found.add(item["tokenAddress"])
    return found
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/providers/test_dexscreener.py -q` then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/providers/dexscreener.py tests/providers/test_dexscreener.py tests/fixtures/dexscreener_batch.json tests/fixtures/dexscreener_boosts.json tests/fixtures/dexscreener_profiles.json
git commit -m "feat(scanner): DexScreener batch snapshots and feed addresses"
```

---

### Task 3: Birdeye feed addresses (trending + new-listing)

**Files:**
- Modify: `src/memescreen/providers/birdeye.py`
- Test: `tests/providers/test_birdeye.py` (append)
- Create: `tests/fixtures/birdeye_trending.json`, `tests/fixtures/birdeye_new_listing.json`

**Interfaces:**
- Consumes: `config.BIRDEYE_API_KEY`, `config.BIRDEYE_BASE`, existing `_headers()`, `request_json` / `ProviderError`.
- Produces:
  - `fetch_feed_addresses() -> set[str]` — returns `set()` immediately if `config.BIRDEYE_API_KEY` is falsy. Otherwise `GET {BIRDEYE_BASE}/defi/token_trending` (params `sort_by=rank`, `sort_type=asc`, `limit=20`) and `GET {BIRDEYE_BASE}/defi/v2/tokens/new_listing` (params `limit=20`), both with `_headers()`. Each response carries a list of token objects under `data.tokens` (trending) / `data.items` (new_listing); collect each object's `address`. A `ProviderError` on either call is caught and that source contributes nothing. **Exact paths / param names / list keys are best-effort from Birdeye docs — Step 1 captures live shape; adjust the two URLs and the `data.<key>[].address` access to match, keeping the signature identical.**

- [ ] **Step 1: Create fixtures**

`tests/fixtures/birdeye_trending.json`:

```json
{"success": true, "data": {"tokens": [
  {"address": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "symbol": "AAA", "rank": 1},
  {"address": "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE", "symbol": "EEE", "rank": 2}
]}}
```

`tests/fixtures/birdeye_new_listing.json`:

```json
{"success": true, "data": {"items": [
  {"address": "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF", "symbol": "FFF"},
  {"address": "GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG", "symbol": "GGG"}
]}}
```

- [ ] **Step 2: Write failing tests**

Append to `tests/providers/test_birdeye.py` (reuse its `_reload_with_key` helper / `_load`):

```python
@respx.mock
def test_feed_addresses_union_of_trending_and_new_listing(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(url__regex=r"https://public-api\.birdeye\.so/defi/token_trending.*").mock(
        return_value=httpx.Response(200, json=_load("birdeye_trending.json")))
    respx.get(url__regex=r"https://public-api\.birdeye\.so/defi/v2/tokens/new_listing.*").mock(
        return_value=httpx.Response(200, json=_load("birdeye_new_listing.json")))
    addrs = birdeye.fetch_feed_addresses()
    assert "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE" in addrs
    assert "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF" in addrs


def test_feed_addresses_empty_without_key(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, None)
    assert birdeye.fetch_feed_addresses() == set()


@respx.mock
def test_feed_addresses_tolerates_one_endpoint_down(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(url__regex=r".*/defi/token_trending.*").mock(return_value=httpx.Response(429))
    respx.get(url__regex=r".*/defi/v2/tokens/new_listing.*").mock(
        return_value=httpx.Response(200, json=_load("birdeye_new_listing.json")))
    addrs = birdeye.fetch_feed_addresses()
    assert "GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG" in addrs
```

- [ ] **Step 3: Implement — append to `birdeye.py`**

Add `import logging` and `_log = logging.getLogger(__name__)`.

```python
def _feed_call(path: str, params: dict, list_key: str) -> set[str]:
    try:
        payload = request_json(
            _SOURCE, "GET", f"{config.BIRDEYE_BASE}{path}",
            params=params, headers=_headers(),
        )
    except ProviderError as exc:
        _log.warning("birdeye feed %s failed: %s", path, exc.reason)
        return set()
    items = (payload.get("data") or {}).get(list_key) or []
    return {it["address"] for it in items if isinstance(it, dict) and it.get("address")}


def fetch_feed_addresses() -> set[str]:
    if not config.BIRDEYE_API_KEY:
        return set()
    out: set[str] = set()
    out |= _feed_call("/defi/token_trending",
                      {"sort_by": "rank", "sort_type": "asc", "limit": 20}, "tokens")
    out |= _feed_call("/defi/v2/tokens/new_listing", {"limit": 20}, "items")
    return out
```

Add `from memescreen.providers.base import ProviderError` to the existing import line (`from memescreen.providers.base import request_json` → `import request_json, ProviderError`).

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/providers/test_birdeye.py -q` then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/providers/birdeye.py tests/providers/test_birdeye.py tests/fixtures/birdeye_trending.json tests/fixtures/birdeye_new_listing.json
git commit -m "feat(scanner): Birdeye trending + new-listing feed addresses"
```

---

### Task 4: RugCheck 6h in-process cache

**Files:**
- Create: `src/memescreen/scanner/__init__.py` (empty), `src/memescreen/scanner/riskcache.py`
- Create: `tests/scanner/__init__.py` (empty), `tests/scanner/test_riskcache.py`

**Interfaces:**
- Consumes: `rugcheck.fetch_risk_data`, `config.RISK_CACHE_TTL_H`, `ProviderError`.
- Produces: `RiskCache` class:
  - `RiskCache(fetch=rugcheck.fetch_risk_data, ttl_h=None, clock=...)` — `ttl_h` defaults to `config.RISK_CACHE_TTL_H`; `clock` defaults to `lambda: datetime.now(UTC)` (injectable for tests).
  - `.get(address: str) -> RiskData | None` — returns a cached `RiskData` if the entry is younger than `ttl_h`; otherwise calls `fetch(address)`, stores `(clock(), result)`, returns it. On `ProviderError` from `fetch`: if a stale entry exists, log a warning and return the stale entry; else return `None`. Never raises.
  - `.stats() -> tuple[int, int]` — `(hits, misses)` counters (for logging).

- [ ] **Step 1: Write failing tests**

`tests/scanner/test_riskcache.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from memescreen.models import RiskData
from memescreen.providers.base import ProviderError
from memescreen.scanner.riskcache import RiskCache

_RD = RiskData(rugcheck_score=5, mint_authority_enabled=False,
               freeze_authority_enabled=False, lp_locked_pct=100.0)


class _Clock:
    def __init__(self): self.now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    def __call__(self): return self.now
    def advance(self, **kw): self.now += timedelta(**kw)


def test_second_call_within_ttl_is_cached():
    calls = []
    cache = RiskCache(fetch=lambda a: calls.append(a) or _RD, ttl_h=6.0, clock=_Clock())
    assert cache.get("m").lp_locked_pct == 100.0
    assert cache.get("m").lp_locked_pct == 100.0
    assert calls == ["m"]
    assert cache.stats() == (1, 1)


def test_refetches_after_ttl():
    clk = _Clock()
    calls = []
    cache = RiskCache(fetch=lambda a: calls.append(a) or _RD, ttl_h=6.0, clock=clk)
    cache.get("m")
    clk.advance(hours=7)
    cache.get("m")
    assert calls == ["m", "m"]


def test_provider_error_with_no_prior_entry_returns_none():
    def boom(a): raise ProviderError("rugcheck", "500 err")
    cache = RiskCache(fetch=boom, ttl_h=6.0, clock=_Clock())
    assert cache.get("m") is None


def test_provider_error_falls_back_to_stale_entry():
    clk = _Clock()
    state = {"ok": True}
    def fetch(a):
        if state["ok"]:
            return _RD
        raise ProviderError("rugcheck", "500 err")
    cache = RiskCache(fetch=fetch, ttl_h=6.0, clock=clk)
    cache.get("m")
    state["ok"] = False
    clk.advance(hours=7)
    assert cache.get("m") is _RD          # stale served
```

- [ ] **Step 2: Run — expect failure** (`ModuleNotFoundError: memescreen.scanner`).

- [ ] **Step 3: Implement**

`src/memescreen/scanner/__init__.py`: empty.

`src/memescreen/scanner/riskcache.py`:

```python
"""6-hour in-process cache over rugcheck.fetch_risk_data. Resets on restart —
at most one extra RugCheck call per token per process lifetime."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Callable

from memescreen import config
from memescreen.models import RiskData
from memescreen.providers import rugcheck
from memescreen.providers.base import ProviderError

_log = logging.getLogger(__name__)


class RiskCache:
    def __init__(
        self,
        fetch: Callable[[str], RiskData] = rugcheck.fetch_risk_data,
        ttl_h: float | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._fetch = fetch
        self._ttl = timedelta(hours=ttl_h if ttl_h is not None else config.RISK_CACHE_TTL_H)
        self._clock = clock
        self._entries: dict[str, tuple[datetime, RiskData]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, address: str) -> RiskData | None:
        now = self._clock()
        entry = self._entries.get(address)
        if entry is not None and now - entry[0] < self._ttl:
            self._hits += 1
            return entry[1]
        self._misses += 1
        try:
            data = self._fetch(address)
        except ProviderError as exc:
            if entry is not None:
                _log.warning("rugcheck fetch failed for %s (%s); serving stale",
                             address, exc.reason)
                return entry[1]
            return None
        self._entries[address] = (now, data)
        return data

    def stats(self) -> tuple[int, int]:
        return self._hits, self._misses
```

- [ ] **Step 4: Run — expect pass.** Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/scanner/__init__.py src/memescreen/scanner/riskcache.py tests/scanner/__init__.py tests/scanner/test_riskcache.py
git commit -m "feat(scanner): 6h in-process RugCheck cache"
```

---

### Task 5: `scanner/store.py` — SQLite schema, queries, prune

**Files:**
- Create: `src/memescreen/scanner/store.py`
- Create: `tests/scanner/test_store.py`

**Interfaces:**
- Consumes: stdlib `sqlite3`, `config.HISTORY_RETENTION_H`, `config.UNIVERSE_CAP`, `config.ALERT_COOLDOWN_H`, `models.MarketSnapshot`, `models.AnomalyHit`.
- Produces a `Store` class wrapping one `sqlite3.Connection`:
  - `Store(path: str = ":memory:")` — opens, `PRAGMA journal_mode=WAL` (skipped for `:memory:`), creates all tables if absent.
  - `close()`, context-manager (`__enter__`/`__exit__` → close).
  - **token_history**: `append_snapshots(snaps: Iterable[MarketSnapshot]) -> None`; `history(address: str, since: datetime) -> list[dict]` (rows as dicts, ascending `ts`, each dict has `ts` as `datetime` plus the numeric columns); `earliest_ts(address) -> datetime | None`; `prune_history(now: datetime) -> int` (delete rows older than `now - HISTORY_RETENTION_H`, return deleted count).
  - **universe**: `upsert_seen(addresses: set[str], now: datetime) -> None` (insert new with `first_seen=last_seen_in_feed=now`; for existing set `last_seen_in_feed=now`); `touch_activity(addresses: Iterable[str], now: datetime) -> None` (set `last_activity_ts=now` for those present); `universe_addresses() -> list[str]`; `first_seen(address) -> datetime | None`; `evict(now: datetime, cap: int) -> int` — delete rows whose `last_seen_in_feed` and `last_activity_ts` are both older than `now - HISTORY_RETENTION_H`; then if still over `cap`, delete the lowest `coalesce(last_activity_ts, first_seen)` rows down to `cap`; return total deleted.
  - **hits**: `record_hit(hit: AnomalyHit, passed_gate: bool, fail_reason: str | None) -> None`.
  - **alerts_sent**: `recent_alert(address: str, now: datetime, cooldown_h: float) -> bool`; `record_alert(address: str, now: datetime, anomaly_score: int) -> None`.
  - **hit_context** (for the holder-growth filter baseline): `hit_context(address) -> dict | None` (keys `first_holder_count: int | None`, `first_holder_count_at: datetime | None`, `first_seen_at: datetime`); `ensure_hit_context(address, now, holder_count) -> None` (insert once; never overwrite).

Table DDL (exact):

```sql
CREATE TABLE IF NOT EXISTS token_history (
  address TEXT NOT NULL, ts TEXT NOT NULL,
  price_usd REAL, liquidity_usd REAL, market_cap_usd REAL,
  vol_m5 REAL, vol_h1 REAL, vol_h6 REAL, vol_h24 REAL,
  buys_m5 INTEGER, sells_m5 INTEGER,
  PRIMARY KEY (address, ts)
);
CREATE INDEX IF NOT EXISTS ix_history_addr_ts ON token_history(address, ts);
CREATE TABLE IF NOT EXISTS universe (
  address TEXT PRIMARY KEY, first_seen TEXT NOT NULL,
  last_seen_in_feed TEXT, last_activity_ts TEXT
);
CREATE TABLE IF NOT EXISTS hits (
  address TEXT NOT NULL, detected_at TEXT NOT NULL, anomaly_score INTEGER,
  spike_ratio REAL, accel REAL, buy_ratio REAL,
  passed_gate INTEGER NOT NULL, fail_reason TEXT
);
CREATE TABLE IF NOT EXISTS alerts_sent (
  address TEXT NOT NULL, sent_at TEXT NOT NULL, anomaly_score INTEGER
);
CREATE TABLE IF NOT EXISTS hit_context (
  address TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL,
  first_holder_count INTEGER, first_holder_count_at TEXT
);
```

- [ ] **Step 1: Write failing tests** — `tests/scanner/test_store.py`:

```python
from datetime import UTC, datetime, timedelta

from memescreen.models import AnomalyHit, MarketSnapshot
from memescreen.scanner.store import Store

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _snap(addr, ts, **kw):
    base = dict(price_usd=1.0, liquidity_usd=50000.0, market_cap_usd=1e6,
                vol_m5=100.0, vol_h1=1200.0, vol_h6=6000.0, vol_h24=24000.0,
                buys_m5=5, sells_m5=4)
    base.update(kw)
    return MarketSnapshot(address=addr, fetched_at=ts, **base)


def test_append_and_read_history_ascending():
    with Store(":memory:") as s:
        s.append_snapshots([_snap("m", T0 + timedelta(minutes=i), vol_m5=float(i))
                            for i in (2, 0, 1)])
        rows = s.history("m", since=T0 - timedelta(hours=1))
        assert [r["vol_m5"] for r in rows] == [0.0, 1.0, 2.0]
        assert isinstance(rows[0]["ts"], datetime)


def test_prune_history_drops_old_rows():
    with Store(":memory:") as s:
        s.append_snapshots([_snap("m", T0 - timedelta(hours=5)),
                            _snap("m", T0 - timedelta(minutes=30))])
        deleted = s.prune_history(now=T0)          # retention default 3h
        assert deleted == 1
        assert len(s.history("m", since=T0 - timedelta(days=1))) == 1


def test_universe_upsert_touch_and_first_seen():
    with Store(":memory:") as s:
        s.upsert_seen({"a", "b"}, now=T0)
        s.upsert_seen({"b", "c"}, now=T0 + timedelta(minutes=3))
        assert set(s.universe_addresses()) == {"a", "b", "c"}
        assert s.first_seen("b") == T0


def test_universe_evict_stale_then_cap():
    with Store(":memory:") as s:
        s.upsert_seen({"old"}, now=T0 - timedelta(hours=10))
        s.upsert_seen({"k1", "k2", "k3"}, now=T0)
        s.touch_activity(["k1", "k2", "k3"], now=T0)
        deleted = s.evict(now=T0, cap=2)
        addrs = set(s.universe_addresses())
        assert "old" not in addrs and len(addrs) == 2


def test_alerts_cooldown():
    with Store(":memory:") as s:
        assert s.recent_alert("m", now=T0, cooldown_h=6.0) is False
        s.record_alert("m", now=T0, anomaly_score=80)
        assert s.recent_alert("m", now=T0 + timedelta(hours=2), cooldown_h=6.0) is True
        assert s.recent_alert("m", now=T0 + timedelta(hours=7), cooldown_h=6.0) is False


def test_record_hit_and_hit_context_once():
    h = AnomalyHit(address="m", detected_at=T0, spike_ratio=10.0, accel=4.0,
                   buy_ratio=0.8, buys_m5=40, vol_m5=9000.0, vol_h1=60000.0,
                   liq_now=60000.0, liq_change=1.0, first_seen_at=T0, anomaly_score=77)
    with Store(":memory:") as s:
        s.record_hit(h, passed_gate=False, fail_reason="holders flat")
        s.ensure_hit_context("m", now=T0, holder_count=100)
        s.ensure_hit_context("m", now=T0 + timedelta(hours=8), holder_count=999)
        ctx = s.hit_context("m")
        assert ctx["first_holder_count"] == 100      # not overwritten
```

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Implement `src/memescreen/scanner/store.py`.** Use `datetime.isoformat()` on write and `datetime.fromisoformat()` on read for every timestamp. `append_snapshots` uses `INSERT OR REPLACE`. `history` returns `list[dict]` built from `cursor.description`. All writes `self._conn.commit()`. `evict` runs the two-phase delete described in the interface. Keep it one class, ~150 lines. Full implementation is straightforward from the DDL + interface; no ambiguity to resolve.

- [ ] **Step 4: Run — expect pass.** Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/scanner/store.py tests/scanner/test_store.py
git commit -m "feat(scanner): SQLite store — history, universe, hits, alerts, prune"
```

---

### Task 6: `scanner/universe.py` — rolling candidate set

**Files:**
- Create: `src/memescreen/scanner/universe.py`
- Create: `tests/scanner/test_universe.py`

**Interfaces:**
- Consumes: `dexscreener.fetch_feed_addresses`, `birdeye.fetch_feed_addresses`, a `Store`, `config.UNIVERSE_CAP`.
- Produces: `seed(store: Store, now: datetime | None = None) -> int` — union the two feed calls (each already fault-tolerant), `store.upsert_seen(union, now)`, `store.evict(now, config.UNIVERSE_CAP)`, return the resulting `len(store.universe_addresses())`. Logs a one-line summary (`feeds=N added universe=M`).

- [ ] **Step 1: Write failing tests** — `tests/scanner/test_universe.py`:

```python
from datetime import UTC, datetime
from memescreen.scanner import universe
from memescreen.scanner.store import Store

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_seed_unions_feeds_and_populates_universe(monkeypatch):
    monkeypatch.setattr(universe.dexscreener, "fetch_feed_addresses", lambda: {"a", "b"})
    monkeypatch.setattr(universe.birdeye, "fetch_feed_addresses", lambda: {"b", "c"})
    with Store(":memory:") as s:
        n = universe.seed(s, now=T0)
        assert set(s.universe_addresses()) == {"a", "b", "c"}
        assert n == 3


def test_seed_survives_a_feed_raising(monkeypatch):
    def boom(): raise RuntimeError("should have been caught inside the provider")
    monkeypatch.setattr(universe.dexscreener, "fetch_feed_addresses", lambda: {"a"})
    monkeypatch.setattr(universe.birdeye, "fetch_feed_addresses", boom)
    with Store(":memory:") as s:
        # provider-level fault tolerance is Task 2/3; here we assert seed() itself
        # wraps the calls so one bad feed cannot abort a cycle
        n = universe.seed(s, now=T0)
        assert set(s.universe_addresses()) == {"a"}
```

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Implement**

```python
"""Maintain the rolling candidate universe: pull feeds, upsert, evict."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from memescreen import config
from memescreen.providers import birdeye, dexscreener
from memescreen.scanner.store import Store

_log = logging.getLogger(__name__)


def _safe(fetch) -> set[str]:
    try:
        return set(fetch())
    except Exception as exc:  # provider already tolerant; belt-and-braces
        _log.warning("feed call %s failed: %s", getattr(fetch, "__name__", fetch), exc)
        return set()


def seed(store: Store, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    feeds = _safe(dexscreener.fetch_feed_addresses) | _safe(birdeye.fetch_feed_addresses)
    store.upsert_seen(feeds, now)
    store.evict(now, config.UNIVERSE_CAP)
    total = len(store.universe_addresses())
    _log.info("seed: feeds=%d universe=%d", len(feeds), total)
    return total
```

- [ ] **Step 4: Run — expect pass.** Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/scanner/universe.py tests/scanner/test_universe.py
git commit -m "feat(scanner): rolling universe seed/evict"
```

---

### Task 7: `scanner/poller.py` — one poll cycle

**Files:**
- Create: `src/memescreen/scanner/poller.py`
- Create: `tests/scanner/test_poller.py`

**Interfaces:**
- Consumes: `dexscreener.fetch_snapshots`, a `Store`.
- Produces: `poll_once(store: Store, now: datetime | None = None) -> int` — read `store.universe_addresses()`; if empty, return 0. Call `dexscreener.fetch_snapshots(addrs)` (already chunked + fault-tolerant). `store.append_snapshots(snapshots.values())`. `store.touch_activity(snapshots.keys(), now)`. `store.prune_history(now)`. Return `len(snapshots)`. Logs `polled=N pruned=M`.

- [ ] **Step 1: Write failing tests** — `tests/scanner/test_poller.py`:

```python
from datetime import UTC, datetime, timedelta
from memescreen.models import MarketSnapshot
from memescreen.scanner import poller
from memescreen.scanner.store import Store

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_poll_once_writes_history_and_touches_activity(monkeypatch):
    with Store(":memory:") as s:
        s.upsert_seen({"a", "b"}, now=T0)
        snaps = {
            "a": MarketSnapshot(address="a", fetched_at=T0, vol_m5=10.0, liquidity_usd=1.0),
            "b": MarketSnapshot(address="b", fetched_at=T0, vol_m5=20.0, liquidity_usd=2.0),
        }
        monkeypatch.setattr(poller.dexscreener, "fetch_snapshots", lambda addrs: snaps)
        n = poller.poll_once(s, now=T0)
        assert n == 2
        assert len(s.history("a", since=T0 - timedelta(hours=1))) == 1


def test_poll_once_empty_universe_is_noop(monkeypatch):
    with Store(":memory:") as s:
        monkeypatch.setattr(poller.dexscreener, "fetch_snapshots",
                            lambda addrs: (_ for _ in ()).throw(AssertionError("called")))
        assert poller.poll_once(s, now=T0) == 0
```

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Implement**

```python
"""One poll cycle: batch-refresh the universe into token_history, then prune."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from memescreen.providers import dexscreener
from memescreen.scanner.store import Store

_log = logging.getLogger(__name__)


def poll_once(store: Store, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    addrs = store.universe_addresses()
    if not addrs:
        return 0
    snaps = dexscreener.fetch_snapshots(addrs)
    store.append_snapshots(snaps.values())
    store.touch_activity(snaps.keys(), now)
    pruned = store.prune_history(now)
    _log.info("poll: polled=%d pruned=%d", len(snaps), pruned)
    return len(snaps)
```

- [ ] **Step 4: Run — expect pass.** Then `python -m pytest -q`. **Milestone: history fills.**

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/scanner/poller.py tests/scanner/test_poller.py
git commit -m "feat(scanner): poll cycle — batch snapshot to history + prune"
```

---

### Task 8: `scanner/detector.py` — the five anomaly checks

**Files:**
- Create: `src/memescreen/scanner/detector.py`
- Create: `tests/scanner/test_detector.py`

**Interfaces:**
- Consumes: `statistics.median`, a `Store`, `config` detection constants, `models.AnomalyHit`.
- Produces:
  - `detect(store: Store, address: str, now: datetime) -> AnomalyHit | None` — implements spec §5 exactly against `store.history(address, since=now - FRESH_WINDOW_MIN*2)` and the latest row. Returns `None` unless **all five** checks pass. `first_seen_at` from `store.first_seen(address)` (fallback: earliest history ts).
  - `detect_all(store: Store, now: datetime | None = None) -> list[AnomalyHit]` — run `detect` for every `store.universe_addresses()`; skip (no exception) any token with `< DETECT_MIN_HISTORY_MIN` of history; return the hits, highest `anomaly_score` first.

**Check logic (exact — from spec §5):**

```
latest = last history row for address (by ts). Required non-null on latest:
    vol_m5, vol_h1, vol_h6, buys_m5, sells_m5, liquidity_usd.
    Any missing -> return None (cannot assess).

history_span = latest.ts - earliest row ts ; if < DETECT_MIN_HISTORY_MIN min -> None

# Check 1 — short-window spike
window = rows with (latest.ts - BASELINE_WINDOW_MIN) <= ts <= (latest.ts - BASELINE_EXCLUDE_MIN)
samples = [r.vol_m5 for r in window if r.vol_m5 is not None]
if len(samples) < 3 -> None
baseline_m5 = median(samples)
spike_ratio = latest.vol_m5 / max(baseline_m5, VOL_M5_FLOOR)
pass1 = spike_ratio >= SPIKE_RATIO_MIN and latest.vol_m5 >= VOL_M5_FLOOR

# Check 2 — hour-scale acceleration
accel = latest.vol_h1 / max(latest.vol_h6 / 6.0, VOL_M5_FLOOR)
pass2 = accel >= ACCEL_MIN

# Check 3 — buy-driven
denom = max(latest.buys_m5 + latest.sells_m5, 1)
buy_ratio = latest.buys_m5 / denom
pass3 = buy_ratio >= BUY_RATIO_MIN and latest.buys_m5 >= MIN_BUYS

# Check 4 — liquidity holding
target_ts = latest.ts - LIQ_REF_MIN minutes
ref = history row whose ts is closest to target_ts AND within LIQ_REF_TOL_MIN minutes
if ref is None:
    liq_change = 1.0            # "liq trend unknown" -> treat as pass
else:
    liq_change = latest.liquidity_usd / ref.liquidity_usd   (ref.liquidity_usd falsy -> 1.0)
pass4 = liq_change >= LIQ_HOLD_MIN

# Check 5 — fresh transition
recent = rows with ts >= latest.ts - FRESH_WINDOW_MIN minutes, excluding latest
for each such row r with r.vol_m5 not None:
    r_window = window-of-baseline computed the same way but anchored at r.ts
    (SIMPLIFICATION for tractability: use the SAME baseline_m5 from check 1 as the
     reference; r_spike = r.vol_m5 / max(baseline_m5, VOL_M5_FLOOR))
    if r_spike >= SPIKE_RATIO_MIN -> not fresh -> pass5 = False
pass5 defaults True if no such row tripped
# (cooldown / alerts_sent is enforced in filters.py, not here — keep detector pure of alert state)

hit only if pass1 and pass2 and pass3 and pass4 and pass5

anomaly_score = round(100 * clamp01(
    0.45 * min(spike_ratio / (3*SPIKE_RATIO_MIN), 1.0)
  + 0.25 * min(accel / (3*ACCEL_MIN), 1.0)
  + 0.30 * min((buy_ratio - BUY_RATIO_MIN) / (1.0 - BUY_RATIO_MIN), 1.0)
))
```

- [ ] **Step 1: Write failing tests** — `tests/scanner/test_detector.py`. Build synthetic history with a helper that inserts N rows of quiet baseline then a spike row, and assert `detect` fires; then one test per check that nudges exactly that check's inputs past/under its threshold and asserts fire/no-fire. At minimum:
  - `test_clean_spike_is_detected` — 40 min of `vol_m5≈100`, then latest `vol_m5=9000, vol_h1=60000, vol_h6=90000, buys_m5=40, sells_m5=6, liquidity_usd` flat → `AnomalyHit`, `spike_ratio` ≈ 90, `anomaly_score` > 0.
  - `test_single_print_blip_rejected_by_accel` — same but `vol_h1=8000, vol_h6=45000` → `accel` ≈ 1.07 → `None`.
  - `test_sell_driven_spike_rejected` — `buys_m5=6, sells_m5=40` → `None`.
  - `test_draining_liquidity_rejected` — ref-row `liquidity_usd=100000`, latest `40000` → `liq_change=0.4` → `None`.
  - `test_not_fresh_when_already_spiking_30min_ago` — a row 30 min back also has `vol_m5=9000` → `pass5` False → `None`.
  - `test_insufficient_history_returns_none` — only 20 min of rows → `None`.
  - `test_detect_all_ranks_by_score` — two tokens both hit, assert descending `anomaly_score`.

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Implement `detector.py`** per the exact logic block above. Pure functions; only I/O is `store.history` / `store.first_seen` / `store.universe_addresses`. Add a module docstring pointing at spec §5.

- [ ] **Step 4: Run — expect pass.** Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/scanner/detector.py tests/scanner/test_detector.py
git commit -m "feat(scanner): anomaly detector — five corroboration checks"
```

---

### Task 9: `scanner/filters.py` — strict safety/quality gate

**Files:**
- Create: `src/memescreen/scanner/filters.py`
- Create: `tests/scanner/test_filters.py`

**Interfaces:**
- Consumes: a `Store`, a `RiskCache`, `birdeye.fetch_holder_data`, `config` filter constants + `RUGCHECK_DANGER_MAX`, `models.AnomalyHit` / `RiskData` / `HolderData`.
- Produces:
  - `FilterContext` dataclass — carries what the alert message needs: `risk: RiskData | None`, `holders: HolderData | None`, `holder_growth: int | None` (new holders since first context), `age_min: float | None`, `dex: str | None`.
  - `evaluate(hit: AnomalyHit, store: Store, risk_cache: RiskCache, latest_snapshot: MarketSnapshot) -> tuple[bool, str, FilterContext]` — runs the eight §6 filters in order; returns `(passed, reason, ctx)` where `reason` is `""` on pass or the first failing filter's short reason. **Fail-closed:** if `risk_cache.get(...)` returns `None`, or `birdeye.fetch_holder_data(...)` returns `None` / raises, the corresponding filter fails with reason `"risk data unavailable"` / `"holder data unavailable"`. Always calls `store.record_hit(hit, passed, reason or None)` before returning. Holder-growth baseline via `store.hit_context` / `store.ensure_hit_context` (first ever hit for a token records the baseline and fails this filter with `"no holder baseline yet"`).

**Filter order & thresholds (spec §6):**

```
1. cooldown: store.recent_alert(addr, now, ALERT_COOLDOWN_H) -> fail "cooldown"
2. min liquidity: hit.liq_now >= MIN_LIQUIDITY -> else fail "liquidity <20k"
3. min age: age_min >= MIN_AGE_MIN (age from latest_snapshot.pair_created_at) -> else fail "age <60m"
4. risk = risk_cache.get(addr); None -> fail "risk data unavailable"
5. LP: risk.lp_locked_pct is not None and >= LP_LOCKED_MIN -> else fail "LP not locked"
6. authorities: risk.mint_authority_enabled is False AND risk.freeze_authority_enabled is False
                -> else fail "authority live"
7. rugcheck: not risk.danger_flags AND (risk.rugcheck_score is None or
             risk.rugcheck_score <= RUGCHECK_DANGER_MAX) -> else fail "rugcheck danger"
8. holders: h = birdeye.fetch_holder_data(addr) (guard ProviderError -> None)
   h is None or h.holder_count is None -> fail "holder data unavailable"
   ctx_row = store.hit_context(addr)
   if ctx_row is None or ctx_row["first_holder_count"] is None:
       store.ensure_hit_context(addr, now, h.holder_count); fail "no holder baseline yet"
   grew = h.holder_count - ctx_row["first_holder_count"]
   pass if grew >= HOLDER_GROWTH_ABS or
           h.holder_count >= ctx_row["first_holder_count"] * (1 + HOLDER_GROWTH_MIN)
   else fail "holders flat"
```

- [ ] **Step 1: Write failing tests** — `tests/scanner/test_filters.py`. A `_hit(**kw)` builder and a `_snap(**kw)` builder. Use a real `Store(":memory:")`. Monkeypatch `filters.birdeye.fetch_holder_data` and pass a fake `RiskCache` (an object with `.get`). Cases:
  - all-pass (with a pre-seeded `hit_context` showing growth) → `(True, "", ctx)` and a `hits` row with `passed_gate=1`.
  - each filter failing in isolation → `(False, "<that reason>", _)`.
  - `risk_cache.get` returns `None` → `(False, "risk data unavailable", _)`.
  - `birdeye.fetch_holder_data` returns `None` → `(False, "holder data unavailable", _)`.
  - first-ever hit (no `hit_context`) → `(False, "no holder baseline yet", _)` and a `hit_context` row now exists.
  - every call records a `hits` row (`store` query count increments).

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Implement `filters.py`** per the block above. Wrap the `birdeye.fetch_holder_data` call in `try/except ProviderError -> None`. Compute `age_min` from `latest_snapshot.pair_created_at` (None → fail check 3 with `"age unknown"`).

- [ ] **Step 4: Run — expect pass.** Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/scanner/filters.py tests/scanner/test_filters.py
git commit -m "feat(scanner): strict filter gate (fail-closed)"
```

---

### Task 10: `scanner/alerter.py` — Telegram format, send, dedupe, fallback

**Files:**
- Create: `src/memescreen/scanner/alerter.py`
- Create: `tests/scanner/test_alerter.py`

**Interfaces:**
- Consumes: `config.TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, `providers.base.request_json` / `ProviderError`, a `Store`, `models.AnomalyHit`, `filters.FilterContext`.
- Produces:
  - `telegram_configured() -> bool` — both secrets present.
  - `format_message(hit: AnomalyHit, ctx: FilterContext) -> str` — spec §7 layout (plain text, no markdown; include symbol if `ctx` carries it — add `symbol` to `FilterContext` in Task 9 if not already; else use address). Lines: header with `anomaly_score`, 5m vol + `spike_ratio`×, 1h vol + `accel`×, buys/sells + `buy_ratio`%, liquidity + `liq_change`, holders `+N since first seen`, age + dex, safety line (LP % / mint / freeze), rugcheck score, `Contract:`, `Chart: https://dexscreener.com/solana/<addr>`, `Run: screen <addr>`.
  - `send(hit: AnomalyHit, ctx: FilterContext, store: Store, now: datetime) -> bool` —
    - if `store.recent_alert(hit.address, now, config.ALERT_COOLDOWN_H)` → return `False` (deduped; no send).
    - if not `telegram_configured()` → print message to stdout, append to `data/missed_alerts.log`, **do not** write `alerts_sent`, return `False`.
    - `POST https://api.telegram.org/bot{token}/sendMessage` with `json_body={"chat_id": chat_id, "text": msg, "disable_web_page_preview": True}` via `request_json("telegram", "POST", url, json_body=...)`. `request_json` already retries once on 5xx/timeout.
    - on success → `store.record_alert(hit.address, now, hit.anomaly_score)`, return `True`.
    - on `ProviderError` → print message to stdout + append to `data/missed_alerts.log`, **do not** write `alerts_sent` (so a later cycle can retry), return `False`.

- [ ] **Step 1: Write failing tests** — `tests/scanner/test_alerter.py`:
  - `format_message` contains the address, `dexscreener.com/solana/<addr>`, the score, and `screen <addr>`.
  - `send` with Telegram 200 → `respx` route called once with a body containing `chat_id` and `text`; `store.record_alert` written; returns `True`; a second `send` in cooldown returns `False` and makes no HTTP call.
  - `send` with Telegram 500 (both attempts) → returns `False`, `alerts_sent` NOT written, `missed_alerts.log` got a line (point the log at a tmp path via monkeypatching a module-level `MISSED_LOG` or `config`).
  - `send` with secrets missing → no HTTP call, returns `False`, stdout has the message, `alerts_sent` not written.
  - Use `monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "t")` / `"TELEGRAM_CHAT_ID", "c"` (the `conftest.py` autouse fixture restores config after each test).

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Implement `alerter.py`.** Module-level `MISSED_LOG = "data/missed_alerts.log"` (overridable via `config` if you prefer — add `MISSED_LOG` to `config.py` in Task 1's list if so; otherwise keep it here and let the test monkeypatch the module attr). Ensure `data/` exists before appending (`os.makedirs(os.path.dirname(path), exist_ok=True)`).

- [ ] **Step 4: Run — expect pass.** Then `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/scanner/alerter.py tests/scanner/test_alerter.py
git commit -m "feat(scanner): Telegram alerter with dedupe and offline fallback"
```

---

### Task 11: `scanner/run.py` + `cli.py scan` wiring

**Files:**
- Create: `src/memescreen/scanner/run.py`
- Modify: `src/memescreen/cli.py`
- Create: `tests/scanner/test_run.py`
- Modify: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: everything above; stdlib `signal`, `time`, `logging`, `logging.handlers.RotatingFileHandler`.
- Produces:
  - `run.build_logger() -> logging.Logger` — root logger at INFO: a `StreamHandler(sys.stdout)` and a `RotatingFileHandler("data/memescreen.log", maxBytes=2_000_000, backupCount=3)`. Format `"%(asctime)s %(levelname)s %(name)s %(message)s"`. Idempotent (no duplicate handlers on re-call).
  - `run.cycle(store, risk_cache, now, *, do_seed: bool) -> None` — if `do_seed`: `universe.seed(store, now)`. Always: `poller.poll_once(store, now)`; `hits = detector.detect_all(store, now)`; for each hit, fetch its latest snapshot (`store.history(addr, since=now - 10min)[-1]` → build a `MarketSnapshot`-ish; simplest: `detector` already read it — have `detect` attach nothing extra, and `run.cycle` re-reads `store.history` last row into a `MarketSnapshot` via a small `store.latest_snapshot(address) -> MarketSnapshot | None` helper — ADD that to `store.py` in Task 5's interface list). Then `passed, reason, ctx = filters.evaluate(hit, store, risk_cache, latest)`; if `passed`: `alerter.send(hit, ctx, store, now)`.
    Wrap the whole body in `try/except Exception: logger.exception("cycle failed")` so one bad cycle never kills the loop.
  - `run.main(argv: list[str] | None = None) -> int` — `build_logger()`; open `Store(config.DB_PATH)` (ensure `data/` dir exists); `risk_cache = RiskCache()`; warn if `not alerter.telegram_configured()` ("running in terminal-only mode"). Install a `SIGINT` handler that sets a `_stop` flag. Loop on `time.monotonic()`: run `cycle` immediately, then sleep in ≤1s slices until the next `POLL_INTERVAL_SEC` boundary (checking `_stop` each slice); track a separate `last_seed` monotonic mark and pass `do_seed=True` when `SEED_INTERVAL_SEC` elapsed. On `_stop`: `store.prune_history(now)`, `store.close()`, log "stopped", return 0.
  - `cli.py` — replace the `scan` stub branch body with `from memescreen.scanner import run; return run.main()`. Keep the subparser line; update its help text to drop "(Plan 2)".

- [ ] **Step 1: Write failing tests**

`tests/scanner/test_run.py`:

```python
from datetime import UTC, datetime
from memescreen.scanner import run
from memescreen.scanner.store import Store
from memescreen.scanner.riskcache import RiskCache

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_cycle_runs_seed_poll_detect_without_error(monkeypatch):
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 0)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [])
    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=True)  # no raise


def test_cycle_swallows_a_failing_step(monkeypatch, caplog):
    monkeypatch.setattr(run.poller, "poll_once",
                        lambda s, now: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)  # no raise
    assert "cycle failed" in caplog.text


def test_build_logger_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(run.config, "DB_PATH", str(tmp_path / "x.db"))
    a = run.build_logger(); b = run.build_logger()
    assert a is b and len(a.handlers) == len(b.handlers)


def test_hit_is_filtered_and_alerted(monkeypatch):
    from memescreen.models import AnomalyHit
    hit = AnomalyHit(address="m", detected_at=T0, spike_ratio=20.0, accel=5.0,
                     buy_ratio=0.9, buys_m5=50, vol_m5=9000.0, vol_h1=60000.0,
                     liq_now=60000.0, liq_change=1.0, first_seen_at=T0, anomaly_score=88)
    sent = []
    monkeypatch.setattr(run.universe, "seed", lambda s, now: 0)
    monkeypatch.setattr(run.poller, "poll_once", lambda s, now: 1)
    monkeypatch.setattr(run.detector, "detect_all", lambda s, now: [hit])
    monkeypatch.setattr(run, "_latest_or_none", lambda s, a: object())
    monkeypatch.setattr(run.filters, "evaluate",
                        lambda h, s, rc, snap: (True, "", run.filters.FilterContext(
                            risk=None, holders=None, holder_growth=30, age_min=120.0, dex="raydium")))
    monkeypatch.setattr(run.alerter, "send", lambda h, ctx, s, now: sent.append(h.address) or True)
    with Store(":memory:") as s:
        run.cycle(s, RiskCache(fetch=lambda a: None), T0, do_seed=False)
    assert sent == ["m"]
```

Append to `tests/test_cli.py`:

```python
def test_scan_subcommand_invokes_run(monkeypatch):
    called = {}
    import memescreen.scanner.run as run_mod
    monkeypatch.setattr(run_mod, "main", lambda argv=None: called.setdefault("hit", True) or 0)
    from memescreen import cli
    assert cli.main(["scan"]) == 0
    assert called.get("hit") is True
```

(Delete the old `test_scan_is_not_available_yet` test — it asserted the stub. Its replacement is above.)

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Implement `run.py`** and the `cli.py` edit. Add `store.latest_snapshot(address)` to `store.py` (a one-query helper returning a `MarketSnapshot` from the newest `token_history` row, or `None`). In `run.py` keep a private `_latest_or_none(store, address)` wrapper so the test can monkeypatch it.

- [ ] **Step 4: Run — expect pass.** Then `python -m pytest -q`. **Milestone: end-to-end scan wired.**

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/scanner/run.py src/memescreen/scanner/store.py src/memescreen/cli.py tests/scanner/test_run.py tests/test_cli.py
git commit -m "feat(scanner): run loop, signal handling, logging, cli scan wiring"
```

---

### Task 12: End-to-end integration test + README + smoke

**Files:**
- Create: `tests/scanner/test_scan_integration.py`
- Modify: `README.md`

**Interfaces:** none new — this task only proves the wired path and documents it.

- [ ] **Step 1: Write the integration test**

`tests/scanner/test_scan_integration.py` — no monkeypatching of scanner internals; `respx`-mock every outward URL:
- DexScreener feeds (3 URLs) → return one Solana token address.
- DexScreener batch → return, across successive calls, a history-building series: first calls give ~40 minutes of quiet `vol_m5≈100` rows for that token (drive `now` forward by constructing `MarketSnapshot`s directly is cleaner — so actually: seed the `Store` with quiet history via `store.append_snapshots`, then mock only the batch call used by the final poll to return the spike row).
- RugCheck report → clean (`lp_locked_pct=100`, authorities null, no danger).
- Birdeye `token_overview` + `v3/token/holder` → `holder`=200 first (baseline via `ensure_hit_context`)… since first hit fails on "no holder baseline yet", the test asserts THAT outcome (a `hits` row with `passed_gate=0`, reason `"no holder baseline yet"`, no Telegram call) — this is the correct strict behavior and is what the integration test should lock.
- A second scenario: pre-seed `hit_context` with `first_holder_count=100`, Birdeye returns `holder`=200 → filter passes → Telegram `sendMessage` mocked 200 → assert the mocked Telegram route was called once with a body containing the token address, and `alerts_sent` has a row.

Set `config.BIRDEYE_API_KEY="k"`, `TELEGRAM_BOT_TOKEN="t"`, `TELEGRAM_CHAT_ID="c"` via monkeypatch (conftest restores).

- [ ] **Step 2: Run — expect pass** (`python -m pytest tests/scanner/test_scan_integration.py -q`), then full suite `python -m pytest -q`.

- [ ] **Step 3: Update `README.md`** — add a `## scan` section:

```markdown
## scan — continuous volume-spike scanner

    memescreen scan

Long-running. Polls a wide universe of Solana tokens every ~60s, learns each
token's own normal 5-minute volume, and alerts when one spikes ≥8x its own
baseline **and** the spike is buy-driven, accelerating on the hour scale, not
draining liquidity, and fresh — then only if the token passes a strict safety
gate (LP locked ≥90%, mint & freeze renounced, ≥$20k liquidity, ≥60 min old,
holder count rising alongside volume, no RugCheck "danger").

Alerts go to Telegram when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set
in `.env`; otherwise the scanner prints them and logs to `data/missed_alerts.log`.
Needs `BIRDEYE_API_KEY` for the holder-growth check — without it, every hit
fails that check (fail-closed) and nothing alerts.

State lives in `data/memescreen.db` (SQLite). Stop with Ctrl+C — it finishes
the current cycle, prunes, and exits cleanly.

**This is not a money printer.** Most Solana volume spikes are manufactured to
trip exactly this kind of screener. The scanner makes you faster and better-
informed; it does not make you right. Thresholds live in `.env` (see
`config.py`) — expect to tune `SPIKE_RATIO_MIN`, `MIN_LIQUIDITY`, and
`MIN_AGE_MIN` against real results.
```

- [ ] **Step 4: Manual smoke (network; optional but do it if a key is available)**

Run `memescreen scan` for ~2 minutes with `BIRDEYE_API_KEY` set and Telegram unset. Expect: log lines `seed: feeds=… universe=…`, `poll: polled=… pruned=…` each cycle, no traceback, Ctrl+C → `stopped`. Note counts in the report. If no network/key: say so; the integration test is the real gate.

- [ ] **Step 5: Commit**

```bash
git add tests/scanner/test_scan_integration.py README.md
git commit -m "test(scanner): end-to-end integration; document scan"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
|---|---|
| §3.3 funnel (seed → poll → detect → filter → alert) | 6, 7, 8, 9, 10, 11 |
| §4 `AnomalyHit` (+ `MarketSnapshot` for the history row) | 1 |
| §5 five detection checks + `anomaly_score` | 8 |
| §6 eight-filter strict gate, fail-closed, every hit logged | 9 |
| §7 Telegram format, delivery guarantees, startup guard | 10 |
| §10 scanner config constants | 1 |
| §11 persistence — `token_history` / `universe` / `hits` / `alerts_sent` (+ `hit_context` for the holder baseline the spec's §6 implies) | 5 |
| §12 rate-limit budget (BATCH_SIZE 30, sequential, RugCheck cached, Birdeye only on hits) | 2, 4, 9 |
| §13 resilience — per-cycle try/except, Ctrl+C graceful, logging to stdout + rotating file | 11 |
| §14 test surfaces — `test_detector` / `test_filters` / `test_alerter` + provider fixture tests | 8, 9, 10, 2, 3 |
| §16 build steps 5–11 + the provider extensions step 4 implied | all |

**Deliberate deviations / decisions (recorded):**
- **`MarketSnapshot`** is new (spec §4 lists only `AnomalyHit`). The spec's §11 `token_history` columns require per-poll `vol_m5/h1/h6/h24` + `buys_m5/sells_m5`, which `MarketData` does not carry; a separate dataclass keeps the `screen` path untouched.
- **RugCheck cache is in-process, not SQLite** (spec §8 says "cache per token 6h" without prescribing where). Resets on restart — at most one extra call per token per process. Simpler to build and test than a DB table; the rate budget still holds.
- **`hit_context` table + "no holder baseline yet" fail on first hit.** Spec §6 says "holder_count up ≥5% vs first sighting" but the scanner never polls Birdeye at universe entry (too expensive per §12). Baseline is captured on a token's first *hit* instead, and that first hit fails the holder filter closed. Conservative, matches §2's strict philosophy, deterministic.
- **Detector check 5 ("fresh transition") simplification** — re-uses check 1's `baseline_m5` as the reference for prior rows rather than recomputing a trailing-window baseline anchored at each prior row. Documented inline; exact per-row baselines add cost for negligible accuracy at these thresholds.
- **Sync HTTP throughout** — see Tech Stack rationale.
- Birdeye/DexScreener-feed endpoint paths remain doc-derived (same caveat Plan 1 shipped with); each is fixture-tested so a shape change fails a test rather than a 2 a.m. run.

**2. Placeholder scan:** No `TBD`/`TODO`/"handle edge cases"/"similar to Task N". Task 5 Step 3 and Task 8 Step 3 describe implementations against a fully-specified DDL / logic block rather than pasting the full body — the interface + DDL + logic block leave no decision open, which is the bar for "no placeholder". Every test step has literal code.

**3. Type consistency:**
- `MarketSnapshot` / `AnomalyHit` fields (Task 1) — consumed identically in Tasks 2, 5, 7, 8, 9, 11.
- `Store` method names (Task 5) — every call site in Tasks 6, 7, 8, 9, 10, 11 uses a name from Task 5's interface list; `latest_snapshot` is added to that list in Task 11 Step 3 with a note.
- `RiskCache.get -> RiskData | None` (Task 4) — Task 9 filter check 4 handles the `None`.
- `dexscreener.fetch_snapshots -> dict[str, MarketSnapshot]` / `fetch_feed_addresses -> set[str]` (Task 2) — Tasks 6, 7 consume exactly these.
- `birdeye.fetch_feed_addresses -> set[str]` (Task 3) — Task 6 consumes.
- `filters.evaluate -> tuple[bool, str, FilterContext]` (Task 9) — Task 11 `run.cycle` unpacks three values; `FilterContext` fields consumed by `alerter.format_message` (Task 10).
- `alerter.send(hit, ctx, store, now) -> bool` (Task 10) — called with exactly those args in Task 11.
- `cli.py scan` branch (Task 11) — the old `test_scan_is_not_available_yet` is explicitly removed in the same task that changes the behavior.

## Execution Handoff

**Plan complete. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.

**2. Inline Execution** — batch execution with checkpoints via executing-plans.

**Which approach?**
