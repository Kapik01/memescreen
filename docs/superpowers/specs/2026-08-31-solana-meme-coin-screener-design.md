# Solana Meme Coin Screener — Design

**Date:** 2026-08-31
**Status:** Approved for planning
**Author:** Alexander Airapetian (with Claude)

## 1. Purpose & Scope

A personal, single-user tool for Solana meme coin trading research. Two
capabilities on one shared data layer:

1. **`screen <address>`** — point-in-time due-diligence report on one token.
   Deterministic flags + a 0–100 score + a `TRADE` / `CAUTION` / `AVOID`
   verdict. No LLM.
2. **`scan`** — an always-on poller that watches a wide universe of Solana
   tokens, learns each token's own normal trading rate, and sends a Telegram
   alert when a token's activity spikes out of nowhere *and* passes strict
   safety/quality filters.

### Explicit non-goals

- **No trade execution.** Alert-only. The tool never holds keys that can move
  funds.
- **No LLM in v1.** An LLM-written narrative summary is a possible later
  extension, not in scope now.
- **No web UI in v1.** CLI only. The code is structured so a FastAPI layer can
  be added later without touching providers or scoring (`orchestrator.screen()`
  and the scanner components are already decoupled from rendering).
- **No multi-chain.** Solana only.
- **No profit promise.** See §2.

## 2. Reality Check (design assumptions)

The `scan` feature exists to make the user *faster and better-informed*, not to
find guaranteed profit. Design decisions follow from these premises:

- **Most volume spikes on Solana meme coins are manufactured** (wash trading,
  volume bots, bundled buys) specifically to trigger screeners and create exit
  liquidity. Therefore the scanner does not alert on volume alone — it demands
  corroborating signals (buy-side dominance, holder growth, liquidity holding,
  multi-timescale acceleration).
- **Free polling APIs give 30–60s+ latency.** The tool will never beat
  co-located bots. "Fresh transition" detection (§5, check 5) exists to at least
  avoid alerting on moves that are already an hour old.
- **Manufactured vs. organic is the hard problem.** The "holders rising
  alongside volume" filter (§6) is the primary tell and is treated as
  non-negotiable under strict mode (fails closed if the data is unavailable).

## 3. Architecture

### 3.1 Module layout

```
memescreen/
  pyproject.toml
  .env                      # secrets, gitignored
  .env.example
  data/                     # SQLite db, gitignored
  src/memescreen/
    __init__.py
    cli.py                  # argparse; subcommands `screen`, `scan`
    config.py               # loads .env; all tunable thresholds
    models.py               # dataclasses
    orchestrator.py         # screen(address) -> TokenReport
    rules.py                # TokenReport -> flags + score + verdict
    render.py               # TokenReport -> terminal output (rich)
    providers/
      __init__.py
      base.py               # shared httpx.Client, retry, ProviderError
      dexscreener.py        # market data; batch fetch; feed endpoints
      rugcheck.py           # risk data
      birdeye.py            # holder data; trending / new-listing feeds
      helius.py             # metadata fallback; deployer lookup (stretch)
    scanner/
      __init__.py
      store.py              # SQLite schema + queries
      universe.py           # maintains rolling candidate set
      poller.py             # the poll loop: batch-refresh, write history
      detector.py           # history + stats -> AnomalyHit
      filters.py            # strict safety/quality gate
      alerter.py            # Telegram send + dedupe
      run.py                # `scan` entry: wires the funnel together
  tests/
    test_rules.py
    test_detector.py
    test_filters.py
    test_alerter.py
    providers/
      test_dexscreener.py
      test_rugcheck.py
      test_birdeye.py
    fixtures/               # committed real JSON responses
```

### 3.2 Data flow — `screen <address>`

`cli.py` → `orchestrator.screen(address)` calls the four providers
sequentially, each returning its dataclass or raising `ProviderError` →
orchestrator assembles a `TokenReport` from whatever came back (partial data is
valid) → `rules.evaluate(report)` attaches `flags`, `score`, `verdict` →
`render.py` prints. A provider failure never aborts the run; that section shows
"unavailable" and the score notes reduced confidence.

### 3.3 Data flow — `scan` (the funnel)

Cast wide with cheap calls; spend expensive calls only on survivors.

1. **Seed** (every `SEED_INTERVAL_SEC`, default 180s): pull addresses from
   DexScreener boosts + token-profiles and Birdeye trending + new-listing feeds.
   Upsert into `universe` with `last_seen_in_feed`. Evict tokens not in feeds
   and quiet (no history activity) for > `HISTORY_RETENTION_H`. Cap at
   `UNIVERSE_CAP` (default 1500), evicting lowest-activity first.
2. **Poll** (every `POLL_INTERVAL_SEC`, default 60s): batch-fetch market data
   for the whole universe via DexScreener's 30-address batch endpoint. Append
   one compact row per token to `token_history`.
3. **Detect** (in-process, no API): for each token with ≥ 45 min of history
   (enough to form the baseline in §5 check 1), run the anomaly checks (§5).
   Emit `AnomalyHit`s.
4. **Filter** (only on hits, typically 0–5/cycle): strict gate (§6). Uses data
   already in hand + one cached RugCheck call + one Birdeye holder call per
   *new* hit.
5. **Alert** (only on filter survivors, deduped): send Telegram message, write
   `alerts_sent` row after confirmed delivery.

## 4. Data model (`models.py`)

All fields `Optional` unless noted; partial data must be representable.

```python
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
    socials: list[str]                       # default []
    source: str = "dexscreener"

@dataclass
class RiskData:
    rugcheck_score: int | None
    mint_authority_enabled: bool | None
    freeze_authority_enabled: bool | None
    lp_locked_pct: float | None
    top_holders: list[HolderShare]           # default []
    risk_flags: list[str]                    # default []
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
    level: str        # "red" | "yellow" | "green"
    check: str        # e.g. "Liquidity"
    message: str

@dataclass
class TokenReport:
    address: str
    fetched_at: datetime
    market: MarketData | None
    risk: RiskData | None
    holders: HolderData | None
    provider_errors: dict[str, str]          # {"birdeye": "429 rate limited"}
    flags: list[Flag] = field(default_factory=list)
    score: int | None = None
    verdict: str | None = None               # "TRADE" | "CAUTION" | "AVOID"

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
    anomaly_score: int          # 0-100, for ranking
```

## 5. Anomaly detection (`detector.py`)

"Volume out of nowhere" = the token's current trading rate massively exceeds
*its own* recent normal, confirmed at more than one timescale, driven by buys,
with liquidity holding. No global volume threshold — baseline is always
per-token.

Inputs per token: latest DexScreener row (`vol_m5, vol_h1, vol_h6, vol_h24,
txns_m5.buys, txns_m5.sells, liq_now`) + the stored series of past `vol_m5` and
`liq` samples from `token_history`.

Detection runs only once a token has ≥ 45 min of history. All five checks must
pass to emit a hit. Each also contributes to `anomaly_score` (0–100) for
ranking alerts.

```
1. SHORT-WINDOW SPIKE
   baseline_m5 = median(vol_m5 samples over trailing 45 min, excluding last 10 min)
   spike_ratio = vol_m5 / max(baseline_m5, VOL_M5_FLOOR)
   require: spike_ratio >= SPIKE_RATIO_MIN (8)  AND  vol_m5 >= VOL_M5_FLOOR ($3,000)

2. HOUR-SCALE ACCELERATION            (rejects single-print blips)
   accel = vol_h1 / max(vol_h6 / 6, VOL_M5_FLOOR)
   require: accel >= ACCEL_MIN (3)

3. BUY-DRIVEN
   buy_ratio = txns_m5.buys / max(txns_m5.buys + txns_m5.sells, 1)
   require: buy_ratio >= BUY_RATIO_MIN (0.6)  AND  txns_m5.buys >= MIN_BUYS (15)

4. LIQUIDITY HOLDING
   liq_ref = liquidity_usd from the token_history row nearest to 30 min ago,
             within a ±10 min tolerance
   if no such row exists: skip this check (treat as pass), note "liq trend unknown"
   liq_change = liq_now / liq_ref
   require: liq_change >= 0.9            (spike + draining LP = rug in progress)

5. FRESH TRANSITION
   require: no token_history row in the last FRESH_WINDOW_MIN (90) minutes where
            spike_ratio was already >= SPIKE_RATIO_MIN
   require: token not in alerts_sent within ALERT_COOLDOWN_H (6)
```

`anomaly_score` blends normalized `spike_ratio`, `accel`, `buy_ratio`, and (if
Birdeye holder data present) holder growth rate into a 0–100 integer.

### Beginner traps this design avoids

- **Global threshold** — would flood on always-busy tokens and miss the sleeper
  going $200 → $20k. Baseline is per-token.
- **Single-window trigger** — one whale buy spikes `vol_m5` for one sample.
  Check 2 requires the hour to be elevated too.
- **Volume without direction** — a dump also has huge volume. Check 3 requires
  buys to dominate.
- **`vol_h24` as baseline** — too sluggish on a fresh spike; near-zero for
  stale tokens makes every ratio look infinite. Short window + absolute floors
  instead.
- **Re-alerting a sustained pump** — check 5 + cooldown fire once per move.

## 6. Strict filter gate (`filters.py`)

Runs only on anomaly hits. A hit becomes an alert only if **all** pass. At most
one cached RugCheck call + one Birdeye holder call per new token.

| Filter | Threshold (config key) | Rationale |
|---|---|---|
| LP locked/burned | `lp_locked_pct >= LP_LOCKED_MIN` (90) | Unlocked LP = dev pulls liquidity at will. |
| Mint authority | must be renounced (`mint_authority_enabled is False`) | Live mint = dev prints supply into the buy. |
| Freeze authority | must be renounced (`freeze_authority_enabled is False`) | Live freeze = dev locks wallets from selling. |
| Min liquidity | `liq_now >= MIN_LIQUIDITY` ($20,000) | Below this, own entry moves price 20%+ and exit is impossible. |
| Min age | `pair_age >= MIN_AGE_MIN` (60 min) | Filters 2-minute bundled-launch traps. |
| Holders rising | `holder_count` up ≥ `HOLDER_GROWTH_MIN` (5%) vs first sighting, **or** ≥ 25 new holders | Volume up but holders flat = wash trading. Primary manufactured-vs-organic tell. |
| RugCheck risk | no entry with `level == "danger"` in the report's `risks` array, **and** `score_normalised <= RUGCHECK_DANGER_MAX` (default 60) | Cheap catch-all for known patterns. |
| Not already alerted | no `alerts_sent` row within `ALERT_COOLDOWN_H` (6) | Dedupe. |

**Fail-closed:** if Birdeye is rate-limited and holder growth can't be
evaluated, the hit fails the gate (no alert) and the reason is logged.

Every hit — including gate failures — is written to the `hits` table with the
failing reason, as a tuning feedback loop.

## 7. Alerting (`alerter.py`)

Telegram Bot API: `POST https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/sendMessage`
to `TELEGRAM_CHAT_ID`. Both from `.env`.

**Message format:**

```
🚨 VOLUME SPIKE — $SYMBOL  (score 82/100)

5m vol   $47.2k   ·   18x its 45-min baseline
1h vol   $210k    ·   4.1x a typical hour
Buys     87  /  Sells 19    (82% buys)
Liquidity $63k  (holding, +4%)
Holders   +31 since first seen 40m ago
Age       2h 15m   ·   Raydium

Safety   LP burned 100% · mint renounced · freeze renounced
RugCheck score: 1 (low risk)

Contract: <full address>
Chart:    https://dexscreener.com/solana/<address>
Run: screen <address>  for full report
```

**Delivery guarantees:** on send failure, retry once, then fall back to a
terminal print + a line in `missed_alerts.log`. `alerts_sent` is written only
after a confirmed 200 response, so a Telegram outage never both loses the
signal *and* suppresses a later retry.

**Startup guard:** if `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are missing,
`scan` warns and runs in terminal-only mode instead of crashing.

## 8. Providers

Shared: `base.py` provides one `httpx.Client` (10s timeout, 1 retry on
5xx/timeout), and `ProviderError(source, reason)`. The orchestrator / funnel
catches it and records `provider_errors[source] = reason`.

| Provider | Key? | Endpoints | Produces |
|---|---|---|---|
| `dexscreener` | no | `GET /tokens/v1/solana/{addr}` (single & up-to-30 batch); `GET /token-boosts/top/v1`, `/token-boosts/latest/v1`, `/token-profiles/latest/v1` (feeds) | `MarketData`; feed address lists. 60 req/min. Pick highest-liquidity pair. |
| `rugcheck` | no | `GET /v1/tokens/{addr}/report`; fallback `GET /v1/tokens/{addr}/report/summary` on 401 | `RiskData`. Cache per token 6h. |
| `birdeye` | yes (`X-API-KEY`, `x-chain: solana`) | `/defi/token_overview`, `/defi/v3/token/holder`; `/defi/token_trending`, `/defi/v2/tokens/new_listing` (feeds) | `HolderData`; backfills null `MarketData` fields; feed address lists. |
| `helius` | yes | RPC `getAsset` | `MarketData.name/symbol` fallback. Deployer's-other-tokens lookup = stretch / phase 2. |

Exact Birdeye and Helius request paths and response shapes are confirmed
against their current docs during implementation (task 4 in §16); the table
names the capability, not a frozen contract. Each provider is covered by a
fixture test so a path/shape change surfaces as a failing test.

## 9. `screen` scoring rules (`rules.py`)

`evaluate(report: TokenReport)` produces `flags`, `score` (0–100), `verdict`.

**Flags** (examples; exact thresholds live in `config.py`):

| Check | RED | YELLOW | GREEN |
|---|---|---|---|
| Liquidity | < $10k | $10k–$30k | > $30k |
| LP locked | < 50% | 50–90% | ≥ 90% |
| Mint authority | enabled | — | renounced |
| Freeze authority | enabled | — | renounced |
| Top-10 holder concentration | > 60% | 35–60% | < 35% |
| Pair age | < 30 min | 30 min–6 h | > 6 h |
| Volume/liquidity ratio (24h) | > 20 (churny) or < 0.05 (dead) | — | 0.05–20 |
| RugCheck `risks` array | any entry `level == "danger"` | any entry `level == "warn"` | empty |

**Score:** start at 100; each RED subtracts a large weight, each YELLOW a small
weight (weights in `config.py`). Missing data for a check subtracts a small
"unknown" penalty and is noted.

**Verdict:** `AVOID` if any RED or score < 40; `CAUTION` if score 40–70 or any
YELLOW on an authority/LP check; `TRADE` if score > 70 and no RED. Verdict is
advisory, always shown with the flag list.

## 10. Configuration (`config.py`)

All values are module-level constants with `.env` overrides. Defaults:

```
# secrets (.env only, no default)
BIRDEYE_API_KEY, HELIUS_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# detection
SPIKE_RATIO_MIN=8
ACCEL_MIN=3
BUY_RATIO_MIN=0.6
MIN_BUYS=15
VOL_M5_FLOOR=3000
FRESH_WINDOW_MIN=90

# filters
LP_LOCKED_MIN=90
MIN_LIQUIDITY=20000
MIN_AGE_MIN=60
HOLDER_GROWTH_MIN=0.05
HOLDER_GROWTH_ABS=25
RUGCHECK_DANGER_MAX=60

# ops
POLL_INTERVAL_SEC=60
SEED_INTERVAL_SEC=180
UNIVERSE_CAP=1500
ALERT_COOLDOWN_H=6
HISTORY_RETENTION_H=3
HTTP_TIMEOUT_SEC=10
```

## 11. Persistence (`scanner/store.py`)

SQLite at `data/memescreen.db` (gitignored). Tables:

- **`token_history`** — `address, ts, price_usd, liquidity_usd, market_cap_usd,
  vol_m5, vol_h1, vol_h6, vol_h24, buys_m5, sells_m5, pair_created_at, dex,
  symbol`. The last three are what §6's age filter and the alert body read back
  off the latest row, so they must be persisted, not just fetched. Indexed on
  `(address, ts)`. Pruned every poll cycle to the last `HISTORY_RETENTION_H`
  hours (detector never looks back past `FRESH_WINDOW_MIN`). Steady-state size
  ≈ `UNIVERSE_CAP` × 180 ≈ 270k rows.
- **`universe`** — `address, first_seen, last_seen_in_feed, last_activity_ts`.
- **`hits`** — `address, detected_at, anomaly_score, spike_ratio, accel,
  buy_ratio, passed_gate (bool), fail_reason`. Never pruned (small, and it is
  the tuning record).
- **`alerts_sent`** — `address, sent_at, anomaly_score`. Never pruned.

## 12. Rate-limit budget

| Source | Limit | Scanner usage | Headroom |
|---|---|---|---|
| DexScreener | 60 req/min | 1500 ÷ 30 = 50 batch calls per 60s poll | ~10 req/min for seed + `screen` |
| Birdeye (free) | tight CU budget | 2 feed calls per 180s seed + 1 holder call per new hit | comfortable |
| RugCheck | be polite | 1 call per new hit, cached 6h | comfortable |
| Helius | free key | `screen` fallback only | negligible |

`POLL_INTERVAL_SEC`, `UNIVERSE_CAP`, and `SEED_INTERVAL_SEC` are the knobs for
trading coverage against headroom.

## 13. Resilience & ops

- Each poll cycle wrapped in try/except: one bad cycle (network blip,
  unexpected payload) logs and continues; it does not kill the process.
- `Ctrl+C` — finish the current cycle, run the prune, close the db, exit 0.
- No global crash on a single provider failure anywhere.
- Logging: stdlib `logging` to stdout + a rotating file in `data/`.

## 14. Testing

No live API calls in the suite.

| Test file | Covers |
|---|---|
| `test_detector.py` | Synthetic `token_history` series → assert each of the 5 checks fires / doesn't at boundaries. Highest-value surface. |
| `test_filters.py` | Table of `RiskData` / `HolderData` combos → pass/fail + reason. Includes fail-closed on missing Birdeye data. |
| `test_rules.py` | `screen` scoring & verdict at threshold boundaries; missing-data penalties. |
| `providers/test_*.py` | Parse a committed real JSON fixture per endpoint → assert the mapping to dataclasses. Catches API schema drift. |
| `test_alerter.py` | Mocked Telegram endpoint (`respx`); assert message payload and that `alerts_sent` is written only on confirmed 200; retry + fallback path. |

## 15. Dependencies

Runtime: `httpx`, `python-dotenv`, `rich`.
Dev: `pytest`, `respx`.
Stdlib: `sqlite3`, `argparse`, `logging`, `statistics`.

## 16. Build order (for the implementation plan)

1. `config.py`, `models.py`, `providers/base.py`.
2. `providers/dexscreener.py` + `providers/rugcheck.py` + fixtures/tests.
3. `orchestrator.py` + `rules.py` + `render.py` + `cli.py` `screen` subcommand.
   **Milestone: `screen <address>` works end to end.**
4. `providers/birdeye.py` + `providers/helius.py` (+ backfill wiring).
5. `scanner/store.py` (schema + queries + prune).
6. `scanner/universe.py` + `scanner/poller.py`. **Milestone: history fills.**
7. `scanner/detector.py` + tests.
8. `scanner/filters.py` + tests.
9. `scanner/alerter.py` + tests.
10. `scanner/run.py` + `cli.py` `scan` subcommand. **Milestone: end-to-end scan.**
11. Tuning pass against real runs; adjust `config.py` defaults.

## 17. Possible later extensions (out of scope now)

- FastAPI + web dashboard over `orchestrator.screen()`.
- LLM narrative summary of a `TokenReport`.
- Async concurrent provider calls + longer-horizon history cache for
  backtesting anomaly thresholds.
- Deployer-history analysis via Helius (serial-rugger detection).
- Outcome tracking: after an alert, record price at +15m/+1h/+4h to measure the
  scanner's real hit rate.
