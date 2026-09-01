# `screen` Deep-Dive Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `memescreen screen <address>` — a CLI that pulls Solana token data from DexScreener, RugCheck, Birdeye, and Helius and prints a flagged, scored, TRADE/CAUTION/AVOID due-diligence report.

**Architecture:** Provider modules each fetch from one API and return a normalized dataclass, raising `ProviderError` on failure. An orchestrator calls all providers, tolerates partial failure, assembles a `TokenReport`, and hands it to a pure `rules.evaluate()` that attaches flags + score + verdict. A renderer turns the report into terminal text. `cli.py` wires it together. This is Plan 1 of 2; the `scan` scanner is Plan 2 and reuses `providers/` and `models.py` unchanged.

**Tech Stack:** Python 3.11+, `httpx` (HTTP), `python-dotenv` (secrets), `rich` (terminal formatting), `pytest` + `respx` (tests). SQLite/`argparse`/`logging` from stdlib.

**Spec:** `docs/superpowers/specs/2026-08-31-solana-meme-coin-screener-design.md`

## Global Constraints

- Python `>= 3.11` (uses `X | None` unions, `datetime.UTC`).
- Runtime dependencies limited to: `httpx`, `python-dotenv`, `rich`. Dev-only: `pytest`, `respx`.
- No live network calls in the test suite — every HTTP interaction is mocked with `respx` against a committed fixture.
- Solana only. No chain parameter anywhere.
- No code that can move funds. No private keys. Read-only API calls only.
- Secrets come only from `.env` / environment: `BIRDEYE_API_KEY`, `HELIUS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Never hardcode, never log their values.
- All tunable numbers live in `config.py` as module-level constants with `.env` overrides — never inline literals in logic modules.
- Package layout is `src/`-based: importable as `memescreen`, console script `memescreen`.
- Every timestamp is timezone-aware UTC (`datetime.now(UTC)`).

---

### Task 1: Project scaffold, config, and data models

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/memescreen/__init__.py`
- Create: `src/memescreen/config.py`
- Create: `src/memescreen/models.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `memescreen.config` module with constants (all `float`/`int`/`str`/`str | None`):
    - Secrets: `BIRDEYE_API_KEY`, `HELIUS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — `str | None`
    - Bases: `DEXSCREENER_BASE`, `RUGCHECK_BASE`, `BIRDEYE_BASE` — `str`; `HELIUS_RPC` — `str | None`
    - `HTTP_TIMEOUT_SEC: float = 10.0`, `HTTP_RETRIES: int = 1`
    - Flag thresholds: `FLAG_LIQ_RED=10_000.0`, `FLAG_LIQ_GREEN=30_000.0`, `FLAG_LP_LOCKED_RED=50.0`, `FLAG_LP_LOCKED_GREEN=90.0`, `FLAG_TOP10_RED=60.0`, `FLAG_TOP10_YELLOW=35.0`, `FLAG_AGE_RED_MIN=30.0`, `FLAG_AGE_GREEN_HR=6.0`, `FLAG_VOL_LIQ_HIGH=20.0`, `FLAG_VOL_LIQ_LOW=0.05`
    - Score weights: `SCORE_RED_WEIGHT=25`, `SCORE_YELLOW_WEIGHT=8`, `SCORE_UNKNOWN_WEIGHT=5`
    - Verdict cutoffs: `VERDICT_AVOID_MAX=40`, `VERDICT_TRADE_MIN=70`
    - `RUGCHECK_DANGER_MAX=60.0`
    - `env(key: str, default: T, cast: Callable[[str], T]) -> T` helper (public, reused by Plan 2)
  - `memescreen.models` module with frozen-ish dataclasses:
    - `HolderShare(address: str, pct: float)`
    - `MarketData(name, symbol, price_usd, liquidity_usd, volume_24h_usd, market_cap_usd, pair_created_at, dex, socials, source="dexscreener")` — all `X | None` except `socials: list[str]` (default `[]`) and `source: str`
    - `RiskData(rugcheck_score, mint_authority_enabled, freeze_authority_enabled, lp_locked_pct, top_holders, risk_flags, source="rugcheck")` — scalars `X | None`; `top_holders: list[HolderShare]` default `[]`; `risk_flags: list[str]` default `[]`
    - `HolderData(holder_count, top10_pct, top20_pct, creator_pct, source="birdeye")` — all `X | None` except `source`
    - `Flag(level: str, check: str, message: str)` — `level` in `{"red","yellow","green","unknown"}`
    - `TokenReport(address: str, fetched_at: datetime, market: MarketData | None, risk: RiskData | None, holders: HolderData | None, provider_errors: dict[str, str], flags: list[Flag] = [], score: int | None = None, verdict: str | None = None)`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "memescreen"
version = "0.1.0"
description = "Solana meme coin due-diligence screener"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "python-dotenv>=1.0",
    "rich>=13.7",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "respx>=0.21"]

[project.scripts]
memescreen = "memescreen.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
*.egg-info/
.env
data/
.venv/
venv/
```

- [ ] **Step 3: Create `.env.example`**

```dotenv
# Copy to .env and fill in. .env is gitignored.
# Birdeye free API key: https://docs.birdeye.so  (holder depth; optional)
BIRDEYE_API_KEY=
# Helius free API key: https://dashboard.helius.dev  (metadata fallback; optional)
HELIUS_API_KEY=
# Telegram bot (used by Plan 2 scanner only; leave blank for now)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

- [ ] **Step 4: Create `src/memescreen/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 5: Write `tests/__init__.py` (empty) and the failing config test**

`tests/__init__.py`: empty file.

`tests/test_config.py`:

```python
import importlib


def test_defaults_present(monkeypatch):
    monkeypatch.delenv("BIRDEYE_API_KEY", raising=False)
    import memescreen.config as config
    importlib.reload(config)
    assert config.HTTP_TIMEOUT_SEC == 10.0
    assert config.FLAG_LIQ_RED == 10_000.0
    assert config.VERDICT_TRADE_MIN == 70
    assert config.BIRDEYE_API_KEY is None
    assert config.DEXSCREENER_BASE == "https://api.dexscreener.com"


def test_env_override(monkeypatch):
    monkeypatch.setenv("FLAG_LIQ_RED", "5000")
    monkeypatch.setenv("BIRDEYE_API_KEY", "abc123")
    import memescreen.config as config
    importlib.reload(config)
    assert config.FLAG_LIQ_RED == 5000.0
    assert config.BIRDEYE_API_KEY == "abc123"


def test_env_helper_casts():
    import memescreen.config as config
    assert config.env("NOPE_MISSING", 3, int) == 3
```

- [ ] **Step 6: Run the config test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.config'` (after `pip install -e ".[dev]"`).

- [ ] **Step 7: Implement `src/memescreen/config.py`**

```python
"""All tunable configuration. Values come from module defaults, overridable
via environment variables (and a .env file loaded at import time)."""
from __future__ import annotations

import os
from typing import Callable, TypeVar

from dotenv import load_dotenv

load_dotenv()

T = TypeVar("T")


def env(key: str, default: T, cast: Callable[[str], T]) -> T:
    """Return os.environ[key] passed through `cast`, or `default` if unset/blank."""
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return cast(raw.strip())


def _opt_str(key: str) -> str | None:
    raw = os.environ.get(key)
    return raw.strip() if raw and raw.strip() else None


# --- secrets (never logged) ---
BIRDEYE_API_KEY: str | None = _opt_str("BIRDEYE_API_KEY")
HELIUS_API_KEY: str | None = _opt_str("HELIUS_API_KEY")
TELEGRAM_BOT_TOKEN: str | None = _opt_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str | None = _opt_str("TELEGRAM_CHAT_ID")

# --- API bases ---
DEXSCREENER_BASE: str = env("DEXSCREENER_BASE", "https://api.dexscreener.com", str)
RUGCHECK_BASE: str = env("RUGCHECK_BASE", "https://api.rugcheck.xyz", str)
BIRDEYE_BASE: str = env("BIRDEYE_BASE", "https://public-api.birdeye.so", str)
HELIUS_RPC: str | None = (
    f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    if HELIUS_API_KEY
    else None
)

# --- HTTP ---
HTTP_TIMEOUT_SEC: float = env("HTTP_TIMEOUT_SEC", 10.0, float)
HTTP_RETRIES: int = env("HTTP_RETRIES", 1, int)

# --- deep-dive flag thresholds ---
FLAG_LIQ_RED: float = env("FLAG_LIQ_RED", 10_000.0, float)
FLAG_LIQ_GREEN: float = env("FLAG_LIQ_GREEN", 30_000.0, float)
FLAG_LP_LOCKED_RED: float = env("FLAG_LP_LOCKED_RED", 50.0, float)
FLAG_LP_LOCKED_GREEN: float = env("FLAG_LP_LOCKED_GREEN", 90.0, float)
FLAG_TOP10_RED: float = env("FLAG_TOP10_RED", 60.0, float)
FLAG_TOP10_YELLOW: float = env("FLAG_TOP10_YELLOW", 35.0, float)
FLAG_AGE_RED_MIN: float = env("FLAG_AGE_RED_MIN", 30.0, float)
FLAG_AGE_GREEN_HR: float = env("FLAG_AGE_GREEN_HR", 6.0, float)
FLAG_VOL_LIQ_HIGH: float = env("FLAG_VOL_LIQ_HIGH", 20.0, float)
FLAG_VOL_LIQ_LOW: float = env("FLAG_VOL_LIQ_LOW", 0.05, float)

# --- deep-dive scoring ---
SCORE_RED_WEIGHT: int = env("SCORE_RED_WEIGHT", 25, int)
SCORE_YELLOW_WEIGHT: int = env("SCORE_YELLOW_WEIGHT", 8, int)
SCORE_UNKNOWN_WEIGHT: int = env("SCORE_UNKNOWN_WEIGHT", 5, int)
VERDICT_AVOID_MAX: int = env("VERDICT_AVOID_MAX", 40, int)
VERDICT_TRADE_MIN: int = env("VERDICT_TRADE_MIN", 70, int)

RUGCHECK_DANGER_MAX: float = env("RUGCHECK_DANGER_MAX", 60.0, float)
```

- [ ] **Step 8: Run the config test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 9: Write the failing models test**

`tests/test_models.py`:

```python
from datetime import UTC, datetime

from memescreen.models import (
    Flag,
    HolderData,
    HolderShare,
    MarketData,
    RiskData,
    TokenReport,
)


def test_marketdata_defaults():
    m = MarketData(
        name="Doge", symbol="DOGE", price_usd=0.01, liquidity_usd=50_000.0,
        volume_24h_usd=100_000.0, market_cap_usd=1_000_000.0,
        pair_created_at=datetime(2026, 8, 1, tzinfo=UTC), dex="raydium",
    )
    assert m.socials == []
    assert m.source == "dexscreener"


def test_riskdata_holdershare():
    r = RiskData(
        rugcheck_score=1, mint_authority_enabled=False,
        freeze_authority_enabled=False, lp_locked_pct=100.0,
        top_holders=[HolderShare(address="abc", pct=12.5)],
        risk_flags=["Low liquidity"],
    )
    assert r.top_holders[0].pct == 12.5
    assert r.source == "rugcheck"


def test_tokenreport_mutable_result_fields():
    tr = TokenReport(
        address="mint111", fetched_at=datetime.now(UTC), market=None,
        risk=None, holders=None, provider_errors={},
    )
    assert tr.flags == []
    assert tr.score is None
    assert tr.verdict is None
    tr.flags.append(Flag(level="red", check="Liquidity", message="too low"))
    tr.score = 42
    assert tr.score == 42


def test_holderdata_optional():
    h = HolderData(holder_count=None, top10_pct=None, top20_pct=None, creator_pct=None)
    assert h.source == "birdeye"
```

- [ ] **Step 10: Run the models test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.models'`.

- [ ] **Step 11: Implement `src/memescreen/models.py`**

```python
"""Normalized data structures shared by providers, orchestrator, rules, and
(in Plan 2) the scanner. Every provider maps its raw API payload onto one of
these so downstream code never touches vendor JSON shapes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

FlagLevel = str  # "red" | "yellow" | "green" | "unknown"


@dataclass
class HolderShare:
    address: str
    pct: float


@dataclass
class MarketData:
    name: str | None
    symbol: str | None
    price_usd: float | None
    liquidity_usd: float | None
    volume_24h_usd: float | None
    market_cap_usd: float | None
    pair_created_at: datetime | None
    dex: str | None
    socials: list[str] = field(default_factory=list)
    source: str = "dexscreener"


@dataclass
class RiskData:
    rugcheck_score: int | None
    mint_authority_enabled: bool | None
    freeze_authority_enabled: bool | None
    lp_locked_pct: float | None
    top_holders: list[HolderShare] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    source: str = "rugcheck"


@dataclass
class HolderData:
    holder_count: int | None
    top10_pct: float | None
    top20_pct: float | None
    creator_pct: float | None
    source: str = "birdeye"


@dataclass
class Flag:
    level: FlagLevel
    check: str
    message: str


@dataclass
class TokenReport:
    address: str
    fetched_at: datetime
    market: MarketData | None
    risk: RiskData | None
    holders: HolderData | None
    provider_errors: dict[str, str]
    flags: list[Flag] = field(default_factory=list)
    score: int | None = None
    verdict: str | None = None
```

- [ ] **Step 12: Run the models test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (4 tests).

- [ ] **Step 13: Commit**

```bash
git add pyproject.toml .gitignore .env.example src/memescreen tests
git commit -m "feat: project scaffold, config, and data models"
```

---

### Task 2: HTTP layer (`providers/base.py`)

**Files:**
- Create: `src/memescreen/providers/__init__.py`
- Create: `src/memescreen/providers/base.py`
- Create: `tests/providers/__init__.py`
- Create: `tests/providers/test_base.py`

**Interfaces:**
- Consumes: `memescreen.config` (`HTTP_TIMEOUT_SEC`, `HTTP_RETRIES`).
- Produces:
  - `ProviderError(Exception)` with attributes `source: str`, `reason: str`; `str()` renders `"{source}: {reason}"`.
  - `get_client() -> httpx.Client` — process-wide singleton, `timeout=config.HTTP_TIMEOUT_SEC`, `headers={"User-Agent": "memescreen/0.1"}`.
  - `request_json(source: str, method: str, url: str, *, params: dict | None = None, headers: dict | None = None, json_body: dict | None = None) -> Any` — performs the request, retries `config.HTTP_RETRIES` times on `httpx.TimeoutException`, `httpx.TransportError`, or HTTP 5xx; raises `ProviderError(source, reason)` on 4xx, exhausted retries, or non-JSON body. `reason` for an HTTP error starts with the status code as text, e.g. `"401 Unauthorized"`.

- [ ] **Step 1: Write the failing test**

`tests/providers/__init__.py`: empty file.

`tests/providers/test_base.py`:

```python
import httpx
import pytest
import respx

from memescreen.providers.base import ProviderError, request_json


@respx.mock
def test_success_returns_parsed_json():
    respx.get("https://x.test/ok").mock(
        return_value=httpx.Response(200, json={"hello": "world"})
    )
    assert request_json("x", "GET", "https://x.test/ok") == {"hello": "world"}


@respx.mock
def test_4xx_raises_provider_error_with_status_in_reason():
    respx.get("https://x.test/nope").mock(return_value=httpx.Response(404))
    with pytest.raises(ProviderError) as ei:
        request_json("x", "GET", "https://x.test/nope")
    assert ei.value.source == "x"
    assert ei.value.reason.startswith("404")


@respx.mock
def test_retries_once_on_500_then_succeeds():
    route = respx.get("https://x.test/flaky").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": True})]
    )
    assert request_json("x", "GET", "https://x.test/flaky") == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_gives_up_after_retries_on_persistent_500():
    respx.get("https://x.test/down").mock(return_value=httpx.Response(500))
    with pytest.raises(ProviderError) as ei:
        request_json("x", "GET", "https://x.test/down")
    assert ei.value.reason.startswith("500")


@respx.mock
def test_non_json_body_raises_provider_error():
    respx.get("https://x.test/html").mock(
        return_value=httpx.Response(200, text="<html>nope</html>")
    )
    with pytest.raises(ProviderError):
        request_json("x", "GET", "https://x.test/html")


@respx.mock
def test_timeout_raises_provider_error():
    respx.get("https://x.test/slow").mock(side_effect=httpx.TimeoutException("slow"))
    with pytest.raises(ProviderError) as ei:
        request_json("x", "GET", "https://x.test/slow")
    assert "timeout" in ei.value.reason.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/providers/test_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.providers'`.

- [ ] **Step 3: Implement `src/memescreen/providers/__init__.py` and `base.py`**

`src/memescreen/providers/__init__.py`: empty file.

`src/memescreen/providers/base.py`:

```python
"""Shared HTTP plumbing for all providers: one client, a retry policy, and a
single exception type the orchestrator catches."""
from __future__ import annotations

import json
from typing import Any

import httpx

from memescreen import config

_client: httpx.Client | None = None


class ProviderError(Exception):
    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=config.HTTP_TIMEOUT_SEC,
            headers={"User-Agent": "memescreen/0.1"},
        )
    return _client


def request_json(
    source: str,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    json_body: dict | None = None,
) -> Any:
    client = get_client()
    attempts = config.HTTP_RETRIES + 1
    last_reason = "unknown error"
    for attempt in range(attempts):
        try:
            resp = client.request(
                method, url, params=params, headers=headers, json=json_body
            )
        except httpx.TimeoutException:
            last_reason = "timeout"
            continue
        except httpx.TransportError as exc:
            last_reason = f"transport error: {exc}"
            continue

        if resp.status_code >= 500:
            last_reason = f"{resp.status_code} {resp.reason_phrase}".strip()
            continue
        if resp.status_code >= 400:
            raise ProviderError(
                source, f"{resp.status_code} {resp.reason_phrase}".strip()
            )
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError):
            raise ProviderError(source, "response body was not valid JSON")

    raise ProviderError(source, last_reason)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/providers/test_base.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/providers tests/providers
git commit -m "feat: shared HTTP layer with retry and ProviderError"
```

---

### Task 3: DexScreener provider (`providers/dexscreener.py`)

**Files:**
- Create: `src/memescreen/providers/dexscreener.py`
- Create: `tests/providers/test_dexscreener.py`
- Create: `tests/fixtures/dexscreener_wif.json`
- Create: `tests/fixtures/dexscreener_empty.json`

**Interfaces:**
- Consumes: `config.DEXSCREENER_BASE`, `providers.base.request_json`, `providers.base.ProviderError`, `models.MarketData`.
- Produces:
  - `fetch_market_data(address: str) -> MarketData` — `GET {DEXSCREENER_BASE}/tokens/v1/solana/{address}`. Response is a JSON array of pair objects. Raises `ProviderError("dexscreener", "no pairs found")` if the array is empty. Otherwise selects the pair with the greatest `liquidity.usd` (missing → `0`) and maps it.
  - `_pick_primary_pair(pairs: list[dict]) -> dict` (module-private, still unit-tested).

- [ ] **Step 1: Capture / create fixtures**

Try to capture a live response first:

```bash
curl -s "https://api.dexscreener.com/tokens/v1/solana/EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm" \
  -o tests/fixtures/dexscreener_wif.json
python -c "import json,sys; json.load(open('tests/fixtures/dexscreener_wif.json')); print('valid json')"
```

If the network is unavailable or the shape differs from below, use this committed fixture and adjust field access in Step 3 to match whatever the live payload shows.

`tests/fixtures/dexscreener_wif.json`:

```json
[
  {
    "chainId": "solana",
    "dexId": "raydium",
    "url": "https://dexscreener.com/solana/pairlowliq",
    "pairAddress": "pairlowliq",
    "baseToken": {"address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "name": "dogwifhat", "symbol": "WIF"},
    "quoteToken": {"address": "So11111111111111111111111111111111111111112", "name": "Wrapped SOL", "symbol": "SOL"},
    "priceUsd": "0.00090",
    "txns": {"m5": {"buys": 1, "sells": 0}, "h1": {"buys": 5, "sells": 4}, "h24": {"buys": 50, "sells": 40}},
    "volume": {"m5": 100.0, "h1": 2000.0, "h6": 9000.0, "h24": 30000.0},
    "liquidity": {"usd": 5000.0, "base": 1000000, "quote": 3.1},
    "fdv": 800000,
    "marketCap": 800000,
    "pairCreatedAt": 1722470400000,
    "info": {"socials": [{"type": "twitter", "url": "https://x.com/dogwifcoin"}], "websites": [{"url": "https://dogwifcoin.org"}]}
  },
  {
    "chainId": "solana",
    "dexId": "raydium",
    "url": "https://dexscreener.com/solana/pairmain",
    "pairAddress": "pairmain",
    "baseToken": {"address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "name": "dogwifhat", "symbol": "WIF"},
    "quoteToken": {"address": "So11111111111111111111111111111111111111112", "name": "Wrapped SOL", "symbol": "SOL"},
    "priceUsd": "0.00100",
    "txns": {"m5": {"buys": 12, "sells": 3}, "h1": {"buys": 120, "sells": 80}, "h24": {"buys": 3000, "sells": 2500}},
    "volume": {"m5": 5000.0, "h1": 60000.0, "h6": 250000.0, "h24": 1200000.0},
    "liquidity": {"usd": 250000.0, "base": 50000000, "quote": 160.0},
    "fdv": 1000000,
    "marketCap": 1000000,
    "pairCreatedAt": 1722470400000,
    "info": {"socials": [{"type": "twitter", "url": "https://x.com/dogwifcoin"}], "websites": [{"url": "https://dogwifcoin.org"}]}
  }
]
```

`tests/fixtures/dexscreener_empty.json`:

```json
[]
```

- [ ] **Step 2: Write the failing test**

`tests/providers/test_dexscreener.py`:

```python
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from memescreen.providers.base import ProviderError
from memescreen.providers.dexscreener import _pick_primary_pair, fetch_market_data

FIX = Path(__file__).parent.parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


def _load(name):
    return json.loads((FIX / name).read_text())


def test_pick_primary_pair_takes_highest_liquidity():
    pairs = _load("dexscreener_wif.json")
    assert _pick_primary_pair(pairs)["pairAddress"] == "pairmain"


def test_pick_primary_pair_tolerates_missing_liquidity():
    pairs = [{"pairAddress": "a"}, {"pairAddress": "b", "liquidity": {"usd": 1.0}}]
    assert _pick_primary_pair(pairs)["pairAddress"] == "b"


@respx.mock
def test_fetch_market_data_maps_primary_pair():
    respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{ADDR}").mock(
        return_value=httpx.Response(200, json=_load("dexscreener_wif.json"))
    )
    m = fetch_market_data(ADDR)
    assert m.symbol == "WIF"
    assert m.name == "dogwifhat"
    assert m.price_usd == 0.001
    assert m.liquidity_usd == 250000.0
    assert m.volume_24h_usd == 1200000.0
    assert m.market_cap_usd == 1000000.0
    assert m.dex == "raydium"
    assert m.pair_created_at == datetime.fromtimestamp(1722470400, tz=UTC)
    assert "https://x.com/dogwifcoin" in m.socials
    assert "https://dogwifcoin.org" in m.socials
    assert m.source == "dexscreener"


@respx.mock
def test_fetch_market_data_empty_array_raises():
    respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{ADDR}").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(ProviderError) as ei:
        fetch_market_data(ADDR)
    assert ei.value.reason == "no pairs found"


@respx.mock
def test_fetch_market_data_missing_optional_fields_are_none():
    minimal = [{
        "dexId": "raydium",
        "baseToken": {"name": "X", "symbol": "X"},
        "priceUsd": "0.5",
    }]
    respx.get(f"https://api.dexscreener.com/tokens/v1/solana/{ADDR}").mock(
        return_value=httpx.Response(200, json=minimal)
    )
    m = fetch_market_data(ADDR)
    assert m.liquidity_usd is None
    assert m.volume_24h_usd is None
    assert m.pair_created_at is None
    assert m.socials == []
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/providers/test_dexscreener.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.providers.dexscreener'`.

- [ ] **Step 4: Implement `src/memescreen/providers/dexscreener.py`**

```python
"""DexScreener -> MarketData. No API key required. 60 req/min."""
from __future__ import annotations

from datetime import UTC, datetime

from memescreen import config
from memescreen.models import MarketData
from memescreen.providers.base import ProviderError, request_json

_SOURCE = "dexscreener"


def _pick_primary_pair(pairs: list[dict]) -> dict:
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0.0)


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _socials(info: dict) -> list[str]:
    urls: list[str] = []
    for item in info.get("socials") or []:
        if item.get("url"):
            urls.append(item["url"])
    for item in info.get("websites") or []:
        if item.get("url"):
            urls.append(item["url"])
    return urls


def fetch_market_data(address: str) -> MarketData:
    url = f"{config.DEXSCREENER_BASE}/tokens/v1/solana/{address}"
    payload = request_json(_SOURCE, "GET", url)
    if not isinstance(payload, list) or not payload:
        raise ProviderError(_SOURCE, "no pairs found")

    pair = _pick_primary_pair(payload)
    base = pair.get("baseToken") or {}
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    info = pair.get("info") or {}

    created_ms = pair.get("pairCreatedAt")
    created = (
        datetime.fromtimestamp(created_ms / 1000, tz=UTC)
        if isinstance(created_ms, (int, float))
        else None
    )

    return MarketData(
        name=base.get("name"),
        symbol=base.get("symbol"),
        price_usd=_to_float(pair.get("priceUsd")),
        liquidity_usd=_to_float(liquidity.get("usd")),
        volume_24h_usd=_to_float(volume.get("h24")),
        market_cap_usd=_to_float(pair.get("marketCap")),
        pair_created_at=created,
        dex=pair.get("dexId"),
        socials=_socials(info),
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/providers/test_dexscreener.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add src/memescreen/providers/dexscreener.py tests/providers/test_dexscreener.py tests/fixtures/dexscreener_wif.json tests/fixtures/dexscreener_empty.json
git commit -m "feat: DexScreener market-data provider"
```

---

### Task 4: RugCheck provider (`providers/rugcheck.py`)

**Files:**
- Create: `src/memescreen/providers/rugcheck.py`
- Create: `tests/providers/test_rugcheck.py`
- Create: `tests/fixtures/rugcheck_report.json`
- Create: `tests/fixtures/rugcheck_summary.json`

**Interfaces:**
- Consumes: `config.RUGCHECK_BASE`, `providers.base.request_json`, `providers.base.ProviderError`, `models.RiskData`, `models.HolderShare`.
- Produces:
  - `fetch_risk_data(address: str) -> RiskData` — `GET {RUGCHECK_BASE}/v1/tokens/{address}/report`. If that raises `ProviderError` whose `reason` starts with `"401"` or `"403"`, retry `GET .../report/summary` and map the smaller shape. Mapping rules:
    - `mint_authority_enabled`: `True` if `mintAuthority` (or `token.mintAuthority`) is a non-null/non-empty string, `False` if explicitly `null`, else `None`.
    - `freeze_authority_enabled`: same rule for `freezeAuthority` / `token.freezeAuthority`.
    - `lp_locked_pct`: `markets[0].lp.lpLockedPct` (full report) or top-level `lpLockedPct` (summary), else `None`.
    - `top_holders`: `[HolderShare(address=h["address"], pct=float(h["pct"])) for h in topHolders]` (full only; summary has none → `[]`).
    - `risk_flags`: `[r["name"] for r in risks if r.get("name")]`.
    - `rugcheck_score`: `int(round(score_normalised))` if present else `None`.

- [ ] **Step 1: Capture / create fixtures**

```bash
curl -s "https://api.rugcheck.xyz/v1/tokens/EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm/report" \
  -o tests/fixtures/rugcheck_report.json
curl -s "https://api.rugcheck.xyz/v1/tokens/EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm/report/summary" \
  -o tests/fixtures/rugcheck_summary.json
```

If unavailable or shaped differently, use these committed fixtures and adjust field access in Step 3.

`tests/fixtures/rugcheck_report.json`:

```json
{
  "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
  "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
  "mintAuthority": null,
  "freezeAuthority": null,
  "token": {"mintAuthority": null, "freezeAuthority": null, "decimals": 6},
  "tokenMeta": {"name": "dogwifhat", "symbol": "WIF"},
  "topHolders": [
    {"address": "Hxxxaaa", "pct": 4.2, "insider": false},
    {"address": "Hxxxbbb", "pct": 3.1, "insider": false},
    {"address": "Hxxxccc", "pct": 2.0, "insider": false}
  ],
  "risks": [
    {"name": "Low amount of LP Providers", "level": "warn", "score": 20},
    {"name": "Single holder ownership", "level": "warn", "score": 10}
  ],
  "markets": [
    {"lp": {"lpLockedPct": 100.0, "lpLocked": 12345.0}}
  ],
  "score": 120,
  "score_normalised": 12,
  "rugged": false
}
```

`tests/fixtures/rugcheck_summary.json`:

```json
{
  "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
  "tokenType": "",
  "risks": [{"name": "Low amount of LP Providers", "level": "warn", "score": 20}],
  "score": 120,
  "score_normalised": 12,
  "lpLockedPct": 100.0
}
```

- [ ] **Step 2: Write the failing test**

`tests/providers/test_rugcheck.py`:

```python
import json
from pathlib import Path

import httpx
import pytest
import respx

from memescreen.providers.base import ProviderError
from memescreen.providers.rugcheck import fetch_risk_data

FIX = Path(__file__).parent.parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
REPORT_URL = f"https://api.rugcheck.xyz/v1/tokens/{ADDR}/report"
SUMMARY_URL = f"https://api.rugcheck.xyz/v1/tokens/{ADDR}/report/summary"


def _load(name):
    return json.loads((FIX / name).read_text())


@respx.mock
def test_full_report_mapping():
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report.json"))
    )
    r = fetch_risk_data(ADDR)
    assert r.mint_authority_enabled is False
    assert r.freeze_authority_enabled is False
    assert r.lp_locked_pct == 100.0
    assert r.rugcheck_score == 12
    assert [h.pct for h in r.top_holders] == [4.2, 3.1, 2.0]
    assert "Low amount of LP Providers" in r.risk_flags


@respx.mock
def test_mint_authority_present_means_enabled():
    data = _load("rugcheck_report.json")
    data["mintAuthority"] = "SomeAuthorityPubkey"
    respx.get(REPORT_URL).mock(return_value=httpx.Response(200, json=data))
    r = fetch_risk_data(ADDR)
    assert r.mint_authority_enabled is True


@respx.mock
def test_falls_back_to_summary_on_401():
    respx.get(REPORT_URL).mock(return_value=httpx.Response(401))
    respx.get(SUMMARY_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_summary.json"))
    )
    r = fetch_risk_data(ADDR)
    assert r.lp_locked_pct == 100.0
    assert r.rugcheck_score == 12
    assert r.top_holders == []
    assert r.mint_authority_enabled is None


@respx.mock
def test_non_auth_error_propagates():
    respx.get(REPORT_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ProviderError):
        fetch_risk_data(ADDR)
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/providers/test_rugcheck.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.providers.rugcheck'`.

- [ ] **Step 4: Implement `src/memescreen/providers/rugcheck.py`**

```python
"""RugCheck -> RiskData. Public report endpoints need no key; on 401/403 the
lighter /report/summary endpoint is tried instead."""
from __future__ import annotations

from memescreen import config
from memescreen.models import HolderShare, RiskData
from memescreen.providers.base import ProviderError, request_json

_SOURCE = "rugcheck"


def _authority_enabled(*values) -> bool | None:
    """values: candidate raw fields in priority order. Non-null string -> True,
    explicit None present -> False, nothing present -> None."""
    saw_key = False
    for present, value in values:
        if not present:
            continue
        saw_key = True
        if isinstance(value, str) and value.strip():
            return True
        if value is None:
            return False
    return False if saw_key else None


def _map_report(data: dict) -> RiskData:
    token = data.get("token") or {}
    mint = _authority_enabled(
        ("mintAuthority" in data, data.get("mintAuthority")),
        ("mintAuthority" in token, token.get("mintAuthority")),
    )
    freeze = _authority_enabled(
        ("freezeAuthority" in data, data.get("freezeAuthority")),
        ("freezeAuthority" in token, token.get("freezeAuthority")),
    )

    lp_locked = None
    markets = data.get("markets") or []
    if markets:
        lp_locked = ((markets[0].get("lp") or {}).get("lpLockedPct"))
    if lp_locked is None and "lpLockedPct" in data:
        lp_locked = data.get("lpLockedPct")

    holders = []
    for h in data.get("topHolders") or []:
        if h.get("address") is not None and h.get("pct") is not None:
            holders.append(HolderShare(address=h["address"], pct=float(h["pct"])))

    flags = [r["name"] for r in (data.get("risks") or []) if r.get("name")]

    score_norm = data.get("score_normalised")
    score = int(round(score_norm)) if isinstance(score_norm, (int, float)) else None

    return RiskData(
        rugcheck_score=score,
        mint_authority_enabled=mint,
        freeze_authority_enabled=freeze,
        lp_locked_pct=float(lp_locked) if lp_locked is not None else None,
        top_holders=holders,
        risk_flags=flags,
    )


def fetch_risk_data(address: str) -> RiskData:
    report_url = f"{config.RUGCHECK_BASE}/v1/tokens/{address}/report"
    try:
        data = request_json(_SOURCE, "GET", report_url)
    except ProviderError as exc:
        if exc.reason.startswith("401") or exc.reason.startswith("403"):
            summary_url = f"{config.RUGCHECK_BASE}/v1/tokens/{address}/report/summary"
            data = request_json(_SOURCE, "GET", summary_url)
        else:
            raise
    return _map_report(data)
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/providers/test_rugcheck.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/memescreen/providers/rugcheck.py tests/providers/test_rugcheck.py tests/fixtures/rugcheck_report.json tests/fixtures/rugcheck_summary.json
git commit -m "feat: RugCheck risk-data provider with summary fallback"
```

---

### Task 5: Birdeye provider (`providers/birdeye.py`)

**Files:**
- Create: `src/memescreen/providers/birdeye.py`
- Create: `tests/providers/test_birdeye.py`
- Create: `tests/fixtures/birdeye_overview.json`
- Create: `tests/fixtures/birdeye_holders.json`

**Interfaces:**
- Consumes: `config.BIRDEYE_API_KEY`, `config.BIRDEYE_BASE`, `providers.base.request_json`, `providers.base.ProviderError`, `models.HolderData`.
- Produces:
  - `fetch_holder_data(address: str) -> HolderData | None` — returns `None` immediately if `config.BIRDEYE_API_KEY` is falsy (feature simply skipped). Otherwise sends header `{"X-API-KEY": key, "x-chain": "solana"}` to:
    - `GET {BIRDEYE_BASE}/defi/token_overview?address={address}` → `holder_count` from `data.holder`.
    - `GET {BIRDEYE_BASE}/defi/v3/token/holder?address={address}&limit=20` → list of holder items under `data.items`, each with a percentage field. `top10_pct` = sum of first 10 items' percentages; `top20_pct` = sum of first 20; `creator_pct` left `None` in v1 (needs a creator-address cross-reference — out of scope for Plan 1).
  - A `ProviderError` from either call propagates (orchestrator records it); only a missing key yields `None`.
  - **Field-path caveat:** the exact JSON keys below (`data.holder`, `data.items[].percentage`) are best guesses from Birdeye docs. Step 1 captures the live shape; if keys differ, adjust `_overview_holder_count` / `_sum_pct` and the fixtures to match, keeping the function signature identical.

- [ ] **Step 1: Capture / create fixtures**

```bash
curl -s -H "X-API-KEY: $BIRDEYE_API_KEY" -H "x-chain: solana" \
  "https://public-api.birdeye.so/defi/token_overview?address=EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm" \
  -o tests/fixtures/birdeye_overview.json
curl -s -H "X-API-KEY: $BIRDEYE_API_KEY" -H "x-chain: solana" \
  "https://public-api.birdeye.so/defi/v3/token/holder?address=EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm&limit=20" \
  -o tests/fixtures/birdeye_holders.json
```

If unavailable, use these committed fixtures (and reconcile keys later):

`tests/fixtures/birdeye_overview.json`:

```json
{"success": true, "data": {"address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "symbol": "WIF", "holder": 210000, "liquidity": 250000.0, "v24hUSD": 1200000.0}}
```

`tests/fixtures/birdeye_holders.json`:

```json
{"success": true, "data": {"items": [
  {"owner": "h1", "percentage": 4.2}, {"owner": "h2", "percentage": 3.1},
  {"owner": "h3", "percentage": 2.0}, {"owner": "h4", "percentage": 1.8},
  {"owner": "h5", "percentage": 1.5}, {"owner": "h6", "percentage": 1.2},
  {"owner": "h7", "percentage": 1.1}, {"owner": "h8", "percentage": 1.0},
  {"owner": "h9", "percentage": 0.9}, {"owner": "h10", "percentage": 0.8},
  {"owner": "h11", "percentage": 0.7}, {"owner": "h12", "percentage": 0.6},
  {"owner": "h13", "percentage": 0.5}, {"owner": "h14", "percentage": 0.4},
  {"owner": "h15", "percentage": 0.3}, {"owner": "h16", "percentage": 0.3},
  {"owner": "h17", "percentage": 0.2}, {"owner": "h18", "percentage": 0.2},
  {"owner": "h19", "percentage": 0.1}, {"owner": "h20", "percentage": 0.1}
]}}
```

- [ ] **Step 2: Write the failing test**

`tests/providers/test_birdeye.py`:

```python
import importlib
import json
from pathlib import Path

import httpx
import pytest
import respx

FIX = Path(__file__).parent.parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
OVERVIEW = f"https://public-api.birdeye.so/defi/token_overview"
HOLDERS = f"https://public-api.birdeye.so/defi/v3/token/holder"


def _load(name):
    return json.loads((FIX / name).read_text())


def _reload_with_key(monkeypatch, key):
    if key is None:
        monkeypatch.delenv("BIRDEYE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("BIRDEYE_API_KEY", key)
    import memescreen.config as config
    importlib.reload(config)
    import memescreen.providers.birdeye as birdeye
    importlib.reload(birdeye)
    return birdeye


def test_returns_none_without_key(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, None)
    assert birdeye.fetch_holder_data(ADDR) is None


@respx.mock
def test_maps_overview_and_holders(monkeypatch):
    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(OVERVIEW).mock(
        return_value=httpx.Response(200, json=_load("birdeye_overview.json"))
    )
    respx.get(HOLDERS).mock(
        return_value=httpx.Response(200, json=_load("birdeye_holders.json"))
    )
    h = birdeye.fetch_holder_data(ADDR)
    assert h.holder_count == 210000
    assert round(h.top10_pct, 1) == 18.4
    assert round(h.top20_pct, 1) == 21.5
    assert h.source == "birdeye"


@respx.mock
def test_rate_limit_propagates(monkeypatch):
    from memescreen.providers.base import ProviderError

    birdeye = _reload_with_key(monkeypatch, "testkey")
    respx.get(OVERVIEW).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderError):
        birdeye.fetch_holder_data(ADDR)
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/providers/test_birdeye.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.providers.birdeye'`.

- [ ] **Step 4: Implement `src/memescreen/providers/birdeye.py`**

```python
"""Birdeye -> HolderData. Requires a free API key; when absent the caller
gets None and the feature is silently skipped."""
from __future__ import annotations

from memescreen import config
from memescreen.models import HolderData
from memescreen.providers.base import request_json

_SOURCE = "birdeye"


def _headers() -> dict:
    return {"X-API-KEY": config.BIRDEYE_API_KEY or "", "x-chain": "solana"}


def _overview_holder_count(payload: dict) -> int | None:
    data = payload.get("data") or {}
    value = data.get("holder")
    return int(value) if isinstance(value, (int, float)) else None


def _sum_pct(items: list[dict], n: int) -> float | None:
    if not items:
        return None
    total = 0.0
    for item in items[:n]:
        pct = item.get("percentage")
        if isinstance(pct, (int, float)):
            total += float(pct)
    return total


def fetch_holder_data(address: str) -> HolderData | None:
    if not config.BIRDEYE_API_KEY:
        return None

    overview = request_json(
        _SOURCE, "GET", f"{config.BIRDEYE_BASE}/defi/token_overview",
        params={"address": address}, headers=_headers(),
    )
    holders = request_json(
        _SOURCE, "GET", f"{config.BIRDEYE_BASE}/defi/v3/token/holder",
        params={"address": address, "limit": 20}, headers=_headers(),
    )
    items = (holders.get("data") or {}).get("items") or []

    return HolderData(
        holder_count=_overview_holder_count(overview),
        top10_pct=_sum_pct(items, 10),
        top20_pct=_sum_pct(items, 20),
        creator_pct=None,
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/providers/test_birdeye.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/memescreen/providers/birdeye.py tests/providers/test_birdeye.py tests/fixtures/birdeye_overview.json tests/fixtures/birdeye_holders.json
git commit -m "feat: Birdeye holder-data provider (optional, key-gated)"
```

---

### Task 6: Helius metadata fallback (`providers/helius.py`)

**Files:**
- Create: `src/memescreen/providers/helius.py`
- Create: `tests/providers/test_helius.py`
- Create: `tests/fixtures/helius_getasset.json`

**Interfaces:**
- Consumes: `config.HELIUS_API_KEY`, `config.HELIUS_RPC`, `providers.base.request_json`, `providers.base.ProviderError`.
- Produces:
  - `fetch_metadata_fallback(address: str) -> tuple[str | None, str | None] | None` — returns `None` if `config.HELIUS_RPC` is falsy. Otherwise `POST config.HELIUS_RPC` with body `{"jsonrpc": "2.0", "id": "memescreen", "method": "getAsset", "params": {"id": address}}` and returns `(name, symbol)` read from `result.content.metadata.name` / `result.content.metadata.symbol` (either may be `None`). Any `ProviderError` is swallowed and returns `None` — this is a best-effort fallback and must never break `screen`.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/helius_getasset.json`:

```json
{"jsonrpc": "2.0", "id": "memescreen", "result": {"id": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "content": {"metadata": {"name": "dogwifhat", "symbol": "WIF"}}}}
```

- [ ] **Step 2: Write the failing test**

`tests/providers/test_helius.py`:

```python
import importlib
import json
from pathlib import Path

import httpx
import respx

FIX = Path(__file__).parent.parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


def _load(name):
    return json.loads((FIX / name).read_text())


def _reload(monkeypatch, key):
    if key is None:
        monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    else:
        monkeypatch.setenv("HELIUS_API_KEY", key)
    import memescreen.config as config
    importlib.reload(config)
    import memescreen.providers.helius as helius
    importlib.reload(helius)
    return config, helius


def test_returns_none_without_key(monkeypatch):
    _config, helius = _reload(monkeypatch, None)
    assert helius.fetch_metadata_fallback(ADDR) is None


@respx.mock
def test_extracts_name_and_symbol(monkeypatch):
    config, helius = _reload(monkeypatch, "testkey")
    respx.post(config.HELIUS_RPC).mock(
        return_value=httpx.Response(200, json=_load("helius_getasset.json"))
    )
    assert helius.fetch_metadata_fallback(ADDR) == ("dogwifhat", "WIF")


@respx.mock
def test_swallows_errors(monkeypatch):
    config, helius = _reload(monkeypatch, "testkey")
    respx.post(config.HELIUS_RPC).mock(return_value=httpx.Response(500))
    assert helius.fetch_metadata_fallback(ADDR) is None
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/providers/test_helius.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.providers.helius'`.

- [ ] **Step 4: Implement `src/memescreen/providers/helius.py`**

```python
"""Helius getAsset -> (name, symbol). Best-effort metadata fallback only used
when DexScreener returned no name/symbol. Never raises."""
from __future__ import annotations

from memescreen import config
from memescreen.providers.base import ProviderError, request_json

_SOURCE = "helius"


def fetch_metadata_fallback(address: str) -> tuple[str | None, str | None] | None:
    if not config.HELIUS_RPC:
        return None
    body = {
        "jsonrpc": "2.0",
        "id": "memescreen",
        "method": "getAsset",
        "params": {"id": address},
    }
    try:
        payload = request_json(_SOURCE, "POST", config.HELIUS_RPC, json_body=body)
    except ProviderError:
        return None
    metadata = (
        ((payload or {}).get("result") or {}).get("content") or {}
    ).get("metadata") or {}
    return metadata.get("name"), metadata.get("symbol")
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/providers/test_helius.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/memescreen/providers/helius.py tests/providers/test_helius.py tests/fixtures/helius_getasset.json
git commit -m "feat: Helius metadata fallback provider"
```

---

### Task 7: Scoring rules (`rules.py`)

**Files:**
- Create: `src/memescreen/rules.py`
- Create: `tests/test_rules.py`

**Interfaces:**
- Consumes: `config` flag/score/verdict constants, `models.TokenReport`, `models.Flag`.
- Produces:
  - `evaluate(report: TokenReport) -> None` — mutates `report` in place, setting `report.flags` (list of `Flag`), `report.score` (int, clamped 0–100), `report.verdict` (`"TRADE" | "CAUTION" | "AVOID"`).
  - Checks run and the level rules (each appends exactly one `Flag`; missing input → `level="unknown"`):
    - **Liquidity** (`market.liquidity_usd`): `< FLAG_LIQ_RED` red; `< FLAG_LIQ_GREEN` yellow; else green.
    - **LP locked** (`risk.lp_locked_pct`): `< FLAG_LP_LOCKED_RED` red; `< FLAG_LP_LOCKED_GREEN` yellow; else green.
    - **Mint authority** (`risk.mint_authority_enabled`): `True` red; `False` green.
    - **Freeze authority** (`risk.freeze_authority_enabled`): `True` red; `False` green.
    - **Top-10 concentration** (`holders.top10_pct`): `> FLAG_TOP10_RED` red; `> FLAG_TOP10_YELLOW` yellow; else green.
    - **Pair age** (`market.pair_created_at`, compared to `report.fetched_at`): `< FLAG_AGE_RED_MIN` minutes red; `< FLAG_AGE_GREEN_HR` hours yellow; else green.
    - **Volume/liquidity ratio** (`market.volume_24h_usd / market.liquidity_usd`): `> FLAG_VOL_LIQ_HIGH` or `< FLAG_VOL_LIQ_LOW` yellow; else green. (No red for this check.)
    - **RugCheck risks** (`risk.risk_flags` + `risk.rugcheck_score`): any flag present *and* `rugcheck_score > RUGCHECK_DANGER_MAX` → red; any flag present but score at/below cutoff → yellow; empty → green.
  - Scoring: start at 100; each red `-SCORE_RED_WEIGHT`, each yellow `-SCORE_YELLOW_WEIGHT`, each unknown `-SCORE_UNKNOWN_WEIGHT`; clamp to `[0, 100]`.
  - Verdict: `"AVOID"` if any red flag OR `score < VERDICT_AVOID_MAX`. Else `"CAUTION"` if `score < VERDICT_TRADE_MIN` OR any yellow flag whose `check` is one of `{"Mint authority", "Freeze authority", "LP locked"}`. Else `"TRADE"`.

- [ ] **Step 1: Write the failing test**

`tests/test_rules.py`:

```python
from datetime import UTC, datetime, timedelta

from memescreen.models import HolderData, MarketData, RiskData, TokenReport
from memescreen.rules import evaluate

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _report(market=None, risk=None, holders=None):
    return TokenReport(
        address="mint", fetched_at=NOW, market=market, risk=risk,
        holders=holders, provider_errors={},
    )


def _market(**kw):
    base = dict(
        name="X", symbol="X", price_usd=1.0, liquidity_usd=50_000.0,
        volume_24h_usd=100_000.0, market_cap_usd=1_000_000.0,
        pair_created_at=NOW - timedelta(days=2), dex="raydium",
    )
    base.update(kw)
    return MarketData(**base)


def _risk(**kw):
    base = dict(
        rugcheck_score=5, mint_authority_enabled=False,
        freeze_authority_enabled=False, lp_locked_pct=100.0,
        top_holders=[], risk_flags=[],
    )
    base.update(kw)
    return RiskData(**base)


def _flag_levels(report):
    return {f.check: f.level for f in report.flags}


def test_clean_token_scores_high_and_trades():
    r = _report(_market(), _risk(), HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert r.verdict == "TRADE"
    assert r.score >= VERDICT_TRADE_MIN_VALUE()
    assert _flag_levels(r)["Liquidity"] == "green"


def VERDICT_TRADE_MIN_VALUE():
    from memescreen import config
    return config.VERDICT_TRADE_MIN


def test_live_mint_authority_forces_avoid():
    r = _report(_market(), _risk(mint_authority_enabled=True),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["Mint authority"] == "red"
    assert r.verdict == "AVOID"


def test_low_liquidity_is_red():
    r = _report(_market(liquidity_usd=4_000.0), _risk(),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["Liquidity"] == "red"
    assert r.verdict == "AVOID"


def test_unlocked_lp_yellow_gives_caution():
    r = _report(_market(), _risk(lp_locked_pct=70.0),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["LP locked"] == "yellow"
    assert r.verdict == "CAUTION"


def test_missing_risk_data_yields_unknown_flags_and_penalty():
    r = _report(_market(), None, HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    levels = _flag_levels(r)
    assert levels["LP locked"] == "unknown"
    assert levels["Mint authority"] == "unknown"
    assert r.score < 100


def test_pair_age_under_30_min_is_red():
    r = _report(_market(pair_created_at=NOW - timedelta(minutes=10)), _risk(),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["Pair age"] == "red"


def test_top10_concentration_over_60_is_red():
    r = _report(_market(), _risk(), HolderData(1000, 72.0, 80.0, None))
    evaluate(r)
    assert _flag_levels(r)["Top-10 concentration"] == "red"


def test_vol_liq_ratio_extreme_is_yellow_not_red():
    r = _report(_market(volume_24h_usd=5_000_000.0, liquidity_usd=50_000.0),
                _risk(), HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["Volume/liquidity ratio"] == "yellow"


def test_rugcheck_flag_with_high_score_is_red():
    r = _report(_market(), _risk(risk_flags=["Rugged"], rugcheck_score=85),
                HolderData(1000, 20.0, 30.0, None))
    evaluate(r)
    assert _flag_levels(r)["RugCheck risks"] == "red"


def test_score_clamped_to_zero_floor():
    r = _report(
        _market(liquidity_usd=100.0, pair_created_at=NOW - timedelta(minutes=1)),
        _risk(mint_authority_enabled=True, freeze_authority_enabled=True,
              lp_locked_pct=0.0, risk_flags=["Rugged"], rugcheck_score=99),
        HolderData(1000, 95.0, 99.0, None),
    )
    evaluate(r)
    assert r.score == 0
    assert r.verdict == "AVOID"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.rules'`.

- [ ] **Step 3: Implement `src/memescreen/rules.py`**

```python
"""Pure evaluation: TokenReport in, flags + score + verdict attached. No I/O."""
from __future__ import annotations

from memescreen import config
from memescreen.models import Flag, TokenReport

_AUTHORITY_LP_CHECKS = {"Mint authority", "Freeze authority", "LP locked"}


def _liquidity(report: TokenReport) -> Flag:
    v = report.market.liquidity_usd if report.market else None
    if v is None:
        return Flag("unknown", "Liquidity", "no liquidity data")
    if v < config.FLAG_LIQ_RED:
        return Flag("red", "Liquidity", f"${v:,.0f} — very low, easy to rug")
    if v < config.FLAG_LIQ_GREEN:
        return Flag("yellow", "Liquidity", f"${v:,.0f} — thin")
    return Flag("green", "Liquidity", f"${v:,.0f}")


def _lp_locked(report: TokenReport) -> Flag:
    v = report.risk.lp_locked_pct if report.risk else None
    if v is None:
        return Flag("unknown", "LP locked", "no LP lock data")
    if v < config.FLAG_LP_LOCKED_RED:
        return Flag("red", "LP locked", f"{v:.0f}% locked — dev can pull liquidity")
    if v < config.FLAG_LP_LOCKED_GREEN:
        return Flag("yellow", "LP locked", f"{v:.0f}% locked")
    return Flag("green", "LP locked", f"{v:.0f}% locked")


def _mint_authority(report: TokenReport) -> Flag:
    v = report.risk.mint_authority_enabled if report.risk else None
    if v is None:
        return Flag("unknown", "Mint authority", "unknown")
    if v:
        return Flag("red", "Mint authority", "enabled — dev can mint more supply")
    return Flag("green", "Mint authority", "renounced")


def _freeze_authority(report: TokenReport) -> Flag:
    v = report.risk.freeze_authority_enabled if report.risk else None
    if v is None:
        return Flag("unknown", "Freeze authority", "unknown")
    if v:
        return Flag("red", "Freeze authority", "enabled — dev can freeze your wallet")
    return Flag("green", "Freeze authority", "renounced")


def _top10(report: TokenReport) -> Flag:
    v = report.holders.top10_pct if report.holders else None
    if v is None:
        return Flag("unknown", "Top-10 concentration", "no holder data")
    if v > config.FLAG_TOP10_RED:
        return Flag("red", "Top-10 concentration", f"{v:.0f}% held by top 10")
    if v > config.FLAG_TOP10_YELLOW:
        return Flag("yellow", "Top-10 concentration", f"{v:.0f}% held by top 10")
    return Flag("green", "Top-10 concentration", f"{v:.0f}% held by top 10")


def _pair_age(report: TokenReport) -> Flag:
    created = report.market.pair_created_at if report.market else None
    if created is None:
        return Flag("unknown", "Pair age", "unknown")
    minutes = (report.fetched_at - created).total_seconds() / 60
    if minutes < config.FLAG_AGE_RED_MIN:
        return Flag("red", "Pair age", f"{minutes:.0f} min old — brand new")
    if minutes < config.FLAG_AGE_GREEN_HR * 60:
        return Flag("yellow", "Pair age", f"{minutes / 60:.1f} h old")
    return Flag("green", "Pair age", f"{minutes / 60:.0f} h old")


def _vol_liq(report: TokenReport) -> Flag:
    m = report.market
    if not m or m.volume_24h_usd is None or not m.liquidity_usd:
        return Flag("unknown", "Volume/liquidity ratio", "insufficient data")
    ratio = m.volume_24h_usd / m.liquidity_usd
    if ratio > config.FLAG_VOL_LIQ_HIGH or ratio < config.FLAG_VOL_LIQ_LOW:
        return Flag("yellow", "Volume/liquidity ratio", f"{ratio:.2f}x")
    return Flag("green", "Volume/liquidity ratio", f"{ratio:.2f}x")


def _rugcheck(report: TokenReport) -> Flag:
    if not report.risk:
        return Flag("unknown", "RugCheck risks", "no RugCheck data")
    flags = report.risk.risk_flags
    score = report.risk.rugcheck_score
    if not flags:
        return Flag("green", "RugCheck risks", "none")
    joined = ", ".join(flags[:3])
    if score is not None and score > config.RUGCHECK_DANGER_MAX:
        return Flag("red", "RugCheck risks", f"{joined} (score {score})")
    return Flag("yellow", "RugCheck risks", joined)


_CHECKS = (
    _liquidity, _lp_locked, _mint_authority, _freeze_authority,
    _top10, _pair_age, _vol_liq, _rugcheck,
)


def evaluate(report: TokenReport) -> None:
    flags = [check(report) for check in _CHECKS]
    score = 100
    for f in flags:
        if f.level == "red":
            score -= config.SCORE_RED_WEIGHT
        elif f.level == "yellow":
            score -= config.SCORE_YELLOW_WEIGHT
        elif f.level == "unknown":
            score -= config.SCORE_UNKNOWN_WEIGHT
    score = max(0, min(100, score))

    has_red = any(f.level == "red" for f in flags)
    caution_yellow = any(
        f.level == "yellow" and f.check in _AUTHORITY_LP_CHECKS for f in flags
    )
    if has_red or score < config.VERDICT_AVOID_MAX:
        verdict = "AVOID"
    elif score < config.VERDICT_TRADE_MIN or caution_yellow:
        verdict = "CAUTION"
    else:
        verdict = "TRADE"

    report.flags = flags
    report.score = score
    report.verdict = verdict
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_rules.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/rules.py tests/test_rules.py
git commit -m "feat: deep-dive scoring rules (flags, score, verdict)"
```

---

### Task 8: Orchestrator (`orchestrator.py`)

**Files:**
- Create: `src/memescreen/orchestrator.py`
- Create: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `providers.dexscreener.fetch_market_data`, `providers.rugcheck.fetch_risk_data`, `providers.birdeye.fetch_holder_data`, `providers.helius.fetch_metadata_fallback`, `providers.base.ProviderError`, `rules.evaluate`, `models.TokenReport`.
- Produces:
  - `screen(address: str) -> TokenReport` — calls the four providers, catching `ProviderError` from each and recording `provider_errors[source] = reason` (a `None` return from Birdeye/Helius is *not* an error). If `market` is present but `market.name` or `market.symbol` is `None`, calls `fetch_metadata_fallback` and fills the blanks. Builds the `TokenReport` with `fetched_at=datetime.now(UTC)`, runs `rules.evaluate(report)`, and returns it.

- [ ] **Step 1: Write the failing test**

`tests/test_orchestrator.py`:

```python
from datetime import UTC, datetime

import pytest

from memescreen import orchestrator
from memescreen.models import HolderData, MarketData, RiskData
from memescreen.providers.base import ProviderError


def _market(**kw):
    base = dict(
        name="dogwifhat", symbol="WIF", price_usd=0.001, liquidity_usd=250_000.0,
        volume_24h_usd=1_200_000.0, market_cap_usd=1_000_000.0,
        pair_created_at=datetime(2026, 8, 1, tzinfo=UTC), dex="raydium",
    )
    base.update(kw)
    return MarketData(**base)


def test_happy_path_assembles_and_evaluates(monkeypatch):
    monkeypatch.setattr(orchestrator.dexscreener, "fetch_market_data", lambda a: _market())
    monkeypatch.setattr(
        orchestrator.rugcheck, "fetch_risk_data",
        lambda a: RiskData(1, False, False, 100.0, [], []),
    )
    monkeypatch.setattr(
        orchestrator.birdeye, "fetch_holder_data",
        lambda a: HolderData(210000, 18.0, 22.0, None),
    )
    monkeypatch.setattr(orchestrator.helius, "fetch_metadata_fallback", lambda a: None)

    r = orchestrator.screen("mint111")
    assert r.provider_errors == {}
    assert r.market.symbol == "WIF"
    assert r.verdict in {"TRADE", "CAUTION", "AVOID"}
    assert r.score is not None
    assert r.fetched_at.tzinfo is not None


def test_provider_failure_is_recorded_not_raised(monkeypatch):
    def boom(a):
        raise ProviderError("rugcheck", "500 Server Error")

    monkeypatch.setattr(orchestrator.dexscreener, "fetch_market_data", lambda a: _market())
    monkeypatch.setattr(orchestrator.rugcheck, "fetch_risk_data", boom)
    monkeypatch.setattr(orchestrator.birdeye, "fetch_holder_data", lambda a: None)
    monkeypatch.setattr(orchestrator.helius, "fetch_metadata_fallback", lambda a: None)

    r = orchestrator.screen("mint111")
    assert r.risk is None
    assert r.provider_errors["rugcheck"] == "500 Server Error"
    assert r.verdict is not None  # still evaluated on partial data


def test_metadata_fallback_fills_missing_name(monkeypatch):
    monkeypatch.setattr(
        orchestrator.dexscreener, "fetch_market_data",
        lambda a: _market(name=None, symbol=None),
    )
    monkeypatch.setattr(orchestrator.rugcheck, "fetch_risk_data", lambda a: None
                        if False else RiskData(1, False, False, 100.0, [], []))
    monkeypatch.setattr(orchestrator.birdeye, "fetch_holder_data", lambda a: None)
    monkeypatch.setattr(
        orchestrator.helius, "fetch_metadata_fallback", lambda a: ("dogwifhat", "WIF")
    )

    r = orchestrator.screen("mint111")
    assert r.market.name == "dogwifhat"
    assert r.market.symbol == "WIF"


def test_dexscreener_down_still_returns_report(monkeypatch):
    def boom(a):
        raise ProviderError("dexscreener", "no pairs found")

    monkeypatch.setattr(orchestrator.dexscreener, "fetch_market_data", boom)
    monkeypatch.setattr(
        orchestrator.rugcheck, "fetch_risk_data",
        lambda a: RiskData(1, False, False, 100.0, [], []),
    )
    monkeypatch.setattr(orchestrator.birdeye, "fetch_holder_data", lambda a: None)
    monkeypatch.setattr(orchestrator.helius, "fetch_metadata_fallback", lambda a: None)

    r = orchestrator.screen("mint111")
    assert r.market is None
    assert r.provider_errors["dexscreener"] == "no pairs found"
    assert r.verdict is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.orchestrator'`.

- [ ] **Step 3: Implement `src/memescreen/orchestrator.py`**

```python
"""Assemble a TokenReport from every provider, tolerating partial failure,
then score it."""
from __future__ import annotations

from datetime import UTC, datetime

from memescreen import rules
from memescreen.models import TokenReport
from memescreen.providers import birdeye, dexscreener, helius, rugcheck
from memescreen.providers.base import ProviderError


def screen(address: str) -> TokenReport:
    errors: dict[str, str] = {}

    try:
        market = dexscreener.fetch_market_data(address)
    except ProviderError as exc:
        market, errors[exc.source] = None, exc.reason

    try:
        risk = rugcheck.fetch_risk_data(address)
    except ProviderError as exc:
        risk, errors[exc.source] = None, exc.reason

    try:
        holders = birdeye.fetch_holder_data(address)
    except ProviderError as exc:
        holders, errors[exc.source] = None, exc.reason

    if market is not None and (market.name is None or market.symbol is None):
        fallback = helius.fetch_metadata_fallback(address)
        if fallback is not None:
            name, symbol = fallback
            market.name = market.name or name
            market.symbol = market.symbol or symbol

    report = TokenReport(
        address=address,
        fetched_at=datetime.now(UTC),
        market=market,
        risk=risk,
        holders=holders,
        provider_errors=errors,
    )
    rules.evaluate(report)
    return report
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_orchestrator.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator assembles and scores TokenReport"
```

---

### Task 9: Renderer (`render.py`)

**Files:**
- Create: `src/memescreen/render.py`
- Create: `tests/test_render.py`

**Interfaces:**
- Consumes: `models.TokenReport`, `models.Flag`, `rich`.
- Produces:
  - `render_report(report: TokenReport) -> str` — returns a plain-text (already rendered, ANSI-stripped) block built with `rich.console.Console(record=True, width=100)` then `console.export_text()`. Contents:
    - Header line: `SYMBOL  (NAME)` or the address if symbol unknown.
    - Verdict line: `VERDICT: TRADE|CAUTION|AVOID  ·  score NN/100`.
    - A table of flags: one row per flag, `[icon] check — message`, where icon is `red→🔴 yellow→🟡 green→🟢 unknown→⚪`.
    - A "Market" block: price, liquidity, 24h volume, market cap, age, dex, socials — each line omitted if the value is `None`.
    - If `report.provider_errors` is non-empty, a "Data gaps" block listing `source: reason`.
    - Footer: the token address and a `https://dexscreener.com/solana/{address}` link.

- [ ] **Step 1: Write the failing test**

`tests/test_render.py`:

```python
from datetime import UTC, datetime, timedelta

from memescreen.models import Flag, HolderData, MarketData, RiskData, TokenReport
from memescreen.orchestrator import screen  # not used; ensures import graph is fine
from memescreen.render import render_report

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _full_report():
    r = TokenReport(
        address="EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        fetched_at=NOW,
        market=MarketData(
            name="dogwifhat", symbol="WIF", price_usd=0.00123,
            liquidity_usd=250_000.0, volume_24h_usd=1_200_000.0,
            market_cap_usd=1_000_000.0, pair_created_at=NOW - timedelta(hours=30),
            dex="raydium", socials=["https://x.com/dogwifcoin"],
        ),
        risk=RiskData(12, False, False, 100.0, [], ["Low amount of LP Providers"]),
        holders=HolderData(210000, 18.0, 22.0, None),
        provider_errors={},
    )
    r.flags = [
        Flag("green", "Liquidity", "$250,000"),
        Flag("yellow", "RugCheck risks", "Low amount of LP Providers"),
    ]
    r.score = 84
    r.verdict = "CAUTION"
    return r


def test_render_contains_key_lines():
    out = render_report(_full_report())
    assert "WIF" in out
    assert "dogwifhat" in out
    assert "CAUTION" in out
    assert "84/100" in out
    assert "Liquidity" in out
    assert "$250,000" in out
    assert "dexscreener.com/solana/EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm" in out
    assert "x.com/dogwifcoin" in out


def test_render_shows_data_gaps():
    r = _full_report()
    r.provider_errors = {"birdeye": "429 Too Many Requests"}
    r.holders = None
    out = render_report(r)
    assert "Data gaps" in out
    assert "birdeye" in out
    assert "429" in out


def test_render_omits_missing_market_lines():
    r = _full_report()
    r.market.market_cap_usd = None
    out = render_report(r)
    assert "Market cap" not in out


def test_render_handles_no_market_at_all():
    r = _full_report()
    r.market = None
    out = render_report(r)
    assert "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm" in out
    assert "CAUTION" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.render'`.

- [ ] **Step 3: Implement `src/memescreen/render.py`**

```python
"""TokenReport -> terminal text. Uses rich for layout but returns a plain
string so it is trivially testable and pipe-friendly."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from memescreen.models import TokenReport

_ICON = {"red": "🔴", "yellow": "🟡", "green": "🟢", "unknown": "⚪"}


def _fmt_usd(value: float | None) -> str | None:
    return f"${value:,.0f}" if value is not None else None


def _age(report: TokenReport) -> str | None:
    if not report.market or report.market.pair_created_at is None:
        return None
    minutes = (report.fetched_at - report.market.pair_created_at).total_seconds() / 60
    if minutes < 60:
        return f"{minutes:.0f} min"
    if minutes < 60 * 48:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / 60 / 24:.1f} d"


def render_report(report: TokenReport) -> str:
    console = Console(record=True, width=100)
    m = report.market

    symbol = (m.symbol if m and m.symbol else None) or report.address
    name = f"  ({m.name})" if m and m.name else ""
    console.print(f"[bold]{symbol}[/bold]{name}")
    console.print(
        f"VERDICT: {report.verdict}  ·  score {report.score}/100"
    )
    console.print("")

    table = Table(show_header=False, box=None, pad_edge=False)
    for f in report.flags:
        table.add_row(_ICON.get(f.level, "?"), f.check, "—", f.message)
    console.print(table)
    console.print("")

    market_lines = [
        ("Price", f"${m.price_usd:.8f}".rstrip("0") if m and m.price_usd else None),
        ("Liquidity", _fmt_usd(m.liquidity_usd) if m else None),
        ("24h volume", _fmt_usd(m.volume_24h_usd) if m else None),
        ("Market cap", _fmt_usd(m.market_cap_usd) if m else None),
        ("Age", _age(report)),
        ("DEX", m.dex if m else None),
    ]
    printed_any = False
    for label, value in market_lines:
        if value is not None:
            console.print(f"{label:<12} {value}")
            printed_any = True
    if m and m.socials:
        console.print(f"{'Socials':<12} " + "  ".join(m.socials))
        printed_any = True
    if printed_any:
        console.print("")

    if report.provider_errors:
        console.print("[bold]Data gaps[/bold]")
        for source, reason in report.provider_errors.items():
            console.print(f"  {source}: {reason}")
        console.print("")

    console.print(f"Contract: {report.address}")
    console.print(f"Chart:    https://dexscreener.com/solana/{report.address}")

    return console.export_text()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_render.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/render.py tests/test_render.py
git commit -m "feat: terminal renderer for TokenReport"
```

---

### Task 10: CLI entry point (`cli.py`)

**Files:**
- Create: `src/memescreen/cli.py`
- Create: `tests/test_cli.py`
- Create: `README.md`

**Interfaces:**
- Consumes: `orchestrator.screen`, `render.render_report`.
- Produces:
  - `main(argv: list[str] | None = None) -> int` — argparse with two subcommands:
    - `screen ADDRESS` — validates `ADDRESS` is base58-ish (`[1-9A-HJ-NP-Za-km-z]{32,44}`); on invalid prints an error to stderr and returns `2`. On valid, calls `orchestrator.screen`, prints `render_report(...)` to stdout, returns `0`.
    - `scan` — prints `"scan is delivered in Plan 2 and is not available yet."` to stderr and returns `2`.
  - No subcommand → argparse prints help, returns `2`.
  - Console-script `memescreen` (already wired in `pyproject.toml`) calls `main`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import pytest

from memescreen import cli
from memescreen.models import TokenReport
from datetime import UTC, datetime


def _fake_report(address):
    r = TokenReport(
        address=address, fetched_at=datetime.now(UTC), market=None, risk=None,
        holders=None, provider_errors={},
    )
    r.flags = []
    r.score = 50
    r.verdict = "CAUTION"
    return r


VALID = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


def test_screen_happy_path(monkeypatch, capsys):
    monkeypatch.setattr(cli.orchestrator, "screen", lambda a: _fake_report(a))
    rc = cli.main(["screen", VALID])
    out = capsys.readouterr().out
    assert rc == 0
    assert "CAUTION" in out
    assert VALID in out


def test_screen_rejects_bad_address(capsys):
    rc = cli.main(["screen", "not-an-address!!!"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "address" in err.lower()


def test_scan_is_not_available_yet(capsys):
    rc = cli.main(["scan"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Plan 2" in err


def test_no_subcommand_returns_2(capsys):
    rc = cli.main([])
    assert rc == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memescreen.cli'`.

- [ ] **Step 3: Implement `src/memescreen/cli.py`**

```python
"""Command-line entry point. `screen <address>` is the Plan 1 deliverable;
`scan` is a stub until Plan 2."""
from __future__ import annotations

import argparse
import re
import sys

from memescreen import orchestrator
from memescreen.render import render_report

_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memescreen")
    sub = parser.add_subparsers(dest="command")

    screen_p = sub.add_parser("screen", help="due-diligence report for one token")
    screen_p.add_argument("address", help="Solana token mint address")

    sub.add_parser("scan", help="(Plan 2) continuous volume-spike scanner")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "screen":
        if not _ADDRESS_RE.match(args.address):
            print(f"error: '{args.address}' is not a valid Solana address", file=sys.stderr)
            return 2
        report = orchestrator.screen(args.address)
        print(render_report(report))
        return 0

    if args.command == "scan":
        print("scan is delivered in Plan 2 and is not available yet.", file=sys.stderr)
        return 2

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Write `README.md`**

```markdown
# memescreen

Solana meme-coin due-diligence screener.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env      # optional: add BIRDEYE_API_KEY / HELIUS_API_KEY for extra data
```

## Usage

```bash
memescreen screen <TOKEN_MINT_ADDRESS>
```

Prints market data, safety flags, a 0–100 score, and a TRADE / CAUTION / AVOID
verdict. Works with no API keys (DexScreener + RugCheck); adding Birdeye/Helius
keys enriches holder data and metadata.

`memescreen scan` — continuous volume-spike scanner — arrives in Plan 2.

## Tests

```bash
pytest
```

## What the verdict means

It is advisory. AVOID = at least one disqualifying red flag or a very low score.
CAUTION = notable yellow flags or a middling score. TRADE = clean on every check
this tool can see. None of it is financial advice, and manufactured volume /
fake liquidity can still pass. Always read the flag list, not just the verdict.
```

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests from Tasks 1–10; ~45 tests).

- [ ] **Step 7: Manual smoke test (network required, optional)**

Run: `memescreen screen EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm`
Expected: a rendered report with a verdict line, flag table, and market block. If
DexScreener changed shape, a `Data gaps` block will name the failure — fix the
mapping in `providers/dexscreener.py` and re-run.

- [ ] **Step 8: Commit**

```bash
git add src/memescreen/cli.py tests/test_cli.py README.md
git commit -m "feat: CLI entry point with screen subcommand"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
|---|---|
| §1 `screen` capability, no LLM, CLI-only | Tasks 7–10 |
| §3.1 module layout (screen-side) | Tasks 1–10 (scanner/ dir deferred to Plan 2) |
| §3.2 data flow for `screen` | Task 8 |
| §4 data model (screen-side dataclasses) | Task 1 |
| §8 providers (DexScreener, RugCheck, Birdeye, Helius) | Tasks 3, 4, 5, 6 |
| §9 `screen` scoring rules — all 8 checks, score, verdict | Task 7 |
| §10 configuration constants (screen-relevant subset) | Task 1 |
| §14 testing — provider fixture tests, rules boundary tests, no live calls | every task |
| §15 dependencies | Task 1 (`pyproject.toml`) |
| §16 build order tasks 1–3 (foundation → providers → screen end to end) | this whole plan |

Deferred to Plan 2 (correctly out of scope here): §3.3 funnel, §5 detector, §6 filters, §7 alerter, §11 persistence, §12 rate-limit budget, §13 resilience loop, and the `AnomalyHit`/`Candidate` models. `config.py` in Task 1 intentionally includes only the screen-relevant constants; Plan 2 adds the detection/filter/ops constants.

**2. Placeholder scan:** No `TBD`/`TODO`/"handle edge cases"/"similar to Task N" present. Every code step has literal code. The Birdeye and Helius fixtures are marked "verify live shape in Step 1" with an explicit instruction to reconcile keys — that is a real, actionable instruction, not a placeholder.

**3. Type consistency:**
- `ProviderError(source, reason)` — defined Task 2, used identically in Tasks 3–6, 8.
- `fetch_market_data(address) -> MarketData` — Task 3; consumed in Task 8. ✓
- `fetch_risk_data(address) -> RiskData` — Task 4; consumed in Task 8. ✓
- `fetch_holder_data(address) -> HolderData | None` — Task 5; Task 8 treats `None` as non-error. ✓
- `fetch_metadata_fallback(address) -> tuple[str | None, str | None] | None` — Task 6; Task 8 unpacks only when not `None`. ✓
- `evaluate(report) -> None` mutating `flags/score/verdict` — Task 7; called in Task 8; fields read in Task 9. ✓
- `render_report(report) -> str` — Task 9; called in Task 10. ✓
- `Flag.level` values `{"red","yellow","green","unknown"}` — Task 1 defines, Task 7 produces, Task 9's `_ICON` maps all four. ✓
- `config` constant names identical between Task 1 definitions and Task 7 usage (`FLAG_LIQ_RED`, `FLAG_LP_LOCKED_GREEN`, `SCORE_RED_WEIGHT`, `VERDICT_TRADE_MIN`, `RUGCHECK_DANGER_MAX`, …). ✓

One deviation from the spec, intentional and noted: spec §4 lists `Flag.level` as `"red" | "yellow" | "green"`; this plan adds `"unknown"` so missing-data checks are visible in the report and carry the `SCORE_UNKNOWN_WEIGHT` penalty the spec's §9 already calls for ("Missing data for a check subtracts a small 'unknown' penalty and is noted"). Recorded here so Plan 2 and any spec update stay aligned.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-31-screen-deep-dive-tool.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
