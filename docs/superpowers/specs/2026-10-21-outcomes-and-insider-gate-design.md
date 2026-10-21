# Outcome tracking + insider/bundle gate — design

**Date:** 2026-10-21
**Status:** approved in chat, awaiting spec review
**Scope:** Phase 1 of "find gems earlier". Phase 2 (leading-signal alerts,
holder-baseline fix) is out of scope and will be designed from Phase 1 data.

## 1. Problem

After ~6 weeks live, the user reports two failure modes:

- **B — alerts arrive late.** Both alert types trigger on moves that already
  happened: NEW PUMP requires `price_change_h1 >= PUMP_PRICE_PCT`
  (`momentum.py:39`); VOLUME SPIKE requires multi-poll corroboration, pair age
  `>= MIN_AGE_MIN`, and always fails a token's *first* hit on
  "no holder baseline yet" (`filters.py` step 8).
- **C — coins dump right after entry.** Nothing checks *who* holds the token.
  Bundled launches (one operator, many wallets) look like healthy volume and
  holder spread. NEW PUMP runs no safety gate at all.

And underneath both: **memescreen never records what happened after an
alert**, so no signal or threshold can be evaluated. Every tuning change so
far has been a guess.

## 2. Goals / non-goals

**Goals**
1. Record the forward price path of every alert (and every gate reject) so
   signal quality and lateness can be measured.
2. Surface insider/bundle/dev concentration on every alert and drop the
   extreme cases, including on NEW PUMP.
3. A `!results` Discord command that reports the above per alert type.

**Non-goals**
- Making alerts earlier (Phase 2; needs Phase 1 data).
- Any auto-trading or execution. Alert-only, unchanged.
- New paid APIs. Everything here uses data already fetched (RugCheck report,
  DexScreener poll).

## 3. Success criteria

- After ≥7 days live, `!results 7` shows, per alert type: count, median return
  at +15m / +1h / +6h, % that peaked ≥ +50%, % that fell ≥ −50% / rugged,
  median age and 1h-change at alert time, and passed-vs-rejected comparison.
- Every VOLUME SPIKE and NEW PUMP alert shows an insider/dev line when RugCheck
  provides the data.
- Tokens over the insider/dev thresholds do not produce VOLUME SPIKE or
  NEW PUMP alerts.
- All existing tests still pass; new code is covered by unit tests.

## 4. Component A — outcome tracking

### 4.1 Data: `alert_outcomes` table (in `memescreen.db`, `Store`)

```sql
CREATE TABLE IF NOT EXISTS alert_outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL,
  kind TEXT NOT NULL,            -- 'spike' | 'spike_reject' | 'pump' | 'pump_reject' | 'wallet'
  reason TEXT,                   -- reject reason, or wallet label; NULL otherwise
  alerted_at TEXT NOT NULL,
  symbol TEXT,
  -- at alert time
  price0 REAL, mcap0 REAL, liq0 REAL,
  age_min0 REAL,                 -- pair age when alerted (lateness)
  change_h1_0 REAL,              -- 1h % change when alerted (lateness)
  insider_pct REAL, insider_net INTEGER, creator_pct REAL,
  -- filled in later
  price_15m REAL, price_1h REAL, price_6h REAL,
  peak_price REAL, trough_price REAL,
  last_price REAL, last_seen_at TEXT,
  rugged INTEGER NOT NULL DEFAULT 0,
  closed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_outcomes_open ON alert_outcomes(closed, address);
CREATE INDEX IF NOT EXISTS ix_outcomes_time ON alert_outcomes(alerted_at);
```

`price0` is the latest polled price at alert time. For `wallet` alerts the mint
may not yet be tracked; `price0` may be NULL and is back-filled from the first
poll that sees the token (see 4.3).

**Dedup:** at most one *open* row per `(address, kind)`. Recording a second
event for the same pair while one is open is a no-op. Stops a repeatedly
rejected token (e.g. `cooldown`) from flooding the table.

### 4.2 Recording (`scanner/outcomes.py`)

`outcomes.record(store, *, address, kind, now, snap, reason=None, risk=None)`
— called from:

| Call site | kind |
|---|---|
| `run.cycle`, after `alerter.send` returns True | `spike` |
| `run.cycle`, reject branch | `spike_reject` |
| `momentum.scan`, after `send_pump` returns True | `pump` |
| `momentum.scan`, insider/dev gate drop (new, §5.3) | `pump_reject` |
| `walletwatch.scan`, after `send_wallet_buy` returns True | `wallet` |

`record` also calls `store.upsert_seen({address}, now)` so the poller tracks
the token (needed for wallet buys of untracked mints).

Recording is best-effort: any exception is logged and swallowed. It must never
block or fail an alert.

### 4.3 Updating (each cycle, after `poller.poll_once`)

`outcomes.update(store, now)`:

For each open row, take `store.latest_snapshot(address)`:
- If a snapshot is newer than `last_seen_at`: set `last_price`, `last_seen_at`;
  update `peak_price = max(...)`, `trough_price = min(...)`; back-fill `price0`
  / `mcap0` / `liq0` if NULL.
- Fill each checkpoint the first time `now >= alerted_at + 15m / 1h / 6h`,
  using `last_price`. (Checkpoints are "latest price as of that cycle". With a
  60s poll interval that is close enough.)
- **Rugged** = any of: liquidity ≤ 20% of `liq0`; price ≤ 10% of `price0`;
  no new snapshot for ≥ 30 min while open (pulled / delisted).
- **Close** the row when `now >= alerted_at + 6h` or when rugged.
- Call `store.touch_activity` on every open row's address. This keeps alerted
  tokens out of `store.evict` (both the stale cut and the cap cut order by
  `last_activity_ts`) for the full 6h window, without changing eviction.

Peak/trough are tracked incrementally, not derived from `token_history`,
because `HISTORY_RETENTION_H` is 3h, shorter than the 6h window.

### 4.4 `!results [days]` (journal bot)

- Default `days=7`, max 30.
- `outcomes.summarize(rows) -> dict[kind, KindStats]` — pure.
  `KindStats`: `n`, `median_ret_15m/1h/6h` (as % vs `price0`, NULL-safe),
  `pct_peak_50` (peak ≥ 1.5× price0), `pct_down_50` (trough ≤ 0.5× price0 or
  rugged), `median_age_min0`, `median_change_h1_0`.
- `outcomes.format_results(stats, days) -> str` — pure, Discord-sized
  (< 2000 chars). One block per kind; `spike` and `spike_reject` shown
  side by side so a gate that throws away winners is obvious.
- Insider breakdown line: the same stats split by `insider_pct < 10 / 10–30 /
  ≥ 30 / unknown`. This is the data used to tune §5 thresholds.
- Rows count in a window if `alerted_at` falls inside it. Open rows count
  with whatever checkpoints they have so far.
- Wiring: the journal bot shares the process with `scan`. `handler.handle`
  gains an optional `results: Callable[[int], str] | None` argument;
  `bot.py` passes a function that opens a `Store(config.DB_PATH)`, queries,
  summarizes, and formats. `handler` stays pure and testable with a stub.
  If `results` is None (bot run standalone, no scan DB), reply
  "results need the scanner running in the same process."
- `!help` text updated.

## 5. Component B — insider/bundle/dev gate

### 5.1 Data (RugCheck, already fetched, keyless)

Verified against the live `/v1/tokens/{mint}/report` on 2026-10-21. These
fields are present but currently discarded by `_map_report`:

- `topHolders[].insider` (bool)
- `insiderNetworks[]` → `{size, activeAccounts, ...}`
- `graphInsidersDetected` (int)
- `creatorBalance` (raw units) and `token.supply` (raw units)
- `totalHolders` (int)

### 5.2 Model change

`RiskData` gains (all optional, default None):

- `insider_pct: float | None` = sum of `pct` over `topHolders` with
  `insider == True`. If no holder has the key, None (not 0).
  **Limitation:** only covers the top-20 holders RugCheck returns.
- `insider_network_size: int | None` = max `size` across `insiderNetworks`,
  falling back to `graphInsidersDetected`.
- `creator_pct: float | None` = `creatorBalance / token.supply * 100` when
  both are present and supply > 0.
- `total_holders: int | None`.

The `/report/summary` fallback lacks most of these, so they stay None (fail-open).

### 5.3 Gate

New config (env-overridable, matches existing style in `config.py`):

```
INSIDER_MAX_PCT = 30.0   # drop if insider_pct >= this
CREATOR_MAX_PCT = 15.0   # drop if creator_pct >= this
```

- **VOLUME SPIKE:** new step in `filters.evaluate`, right after the RugCheck
  danger check (step 7), before the Birdeye holder call. That way dropped
  tokens cost no Birdeye credit. Reasons: `"insiders 34%"`, `"dev holds 18%"`.
- **NEW PUMP:** in `momentum.scan`, after the existing top-10 check, using the
  same `risk_cache.get` result. Dropped tokens get a `pump_reject` outcome row
  and are *not* posted anywhere. The rejects channel stays spike-only, to
  avoid adding noise.
- **Fail-open:** a None value never drops a token. Brand-new pump.fun coins
  often lack insider analysis, and failing closed would kill NEW PUMP.
- Thresholds are deliberately loose to start (only extreme cases). Tightening
  is a Phase 1.5 decision made from the `!results` insider breakdown.

### 5.4 Display

Shared helper `alerter._insider_line(risk) -> str | None`, e.g.
`🧩 insiders 34% (net 48) · dev 6% · 1,204 holders`. Parts with None values
are omitted, and the whole line is omitted if all are None. It is added to VOLUME
SPIKE, rejects, and NEW PUMP messages. NEW PUMP's "unvetted — no safety gate ran"
wording changes to "light check only (insiders/dev) — DYOR".

## 6. Error handling

- Outcome record/update errors: logged with `logger.exception`, swallowed.
  `run.cycle` already guards the whole body; outcomes additionally wrap
  their own work so one bad row doesn't skip the rest of the cycle.
- RugCheck field parsing: every new field is parsed defensively (wrong type →
  None). A malformed field never raises out of `_map_report`.
- `!results` errors: reply "couldn't load results (see logs)" and log the
  exception. Never crash the bot.

## 7. Testing

- `tests/fixtures/rugcheck_report.json`: add the new fields (insider holders,
  `insiderNetworks`, `creatorBalance`, `token.supply`, `totalHolders`). Add a
  `rugcheck_report_insiders.json` variant that should be gated.
- `test_rugcheck`: new fields mapped; missing → None; summary fallback → None.
- `test_filters`: insider ≥ threshold drops with reason; dev ≥ threshold drops;
  None passes; the gate runs before the Birdeye call (respx asserts no call).
- `test_momentum`: insider-heavy token is not sent and gets a `pump_reject` row.
- `test_outcomes` (new): record dedup; update fills checkpoints at the right
  times (inject `now`); peak/trough; three rug conditions; close at 6h;
  `touch_activity` keeps rows from eviction; `summarize` medians with NULLs;
  `format_results` stays under 2000 chars with all kinds present.
- `test_handler`: `!results` with stub; `!results 3`; no `results` fn → message.
- Existing 337 tests stay green.

## 8. Rollout

Feature branch `feature/outcomes-insider-gate` → tests → merge `--no-ff` to
`main` → `git push` (Railway deploys). The `alert_outcomes` table is created by
`CREATE TABLE IF NOT EXISTS`, so no migration is needed. After deploy: confirm in
Railway logs that outcomes are recorded and updated, and that an alert shows the
insider line. Then wait ~7 days before Phase 2 design.

## 9. Known limitations

- `insider_pct` sees only RugCheck's top-20 holders, so a bundle spread
  thinner than that is undercounted.
- Checkpoint prices are cycle-granular (~60s), not exact.
- RugCheck's insider analysis can lag on minutes-old tokens. That is why the
  gate fails open.
- Survivorship: tokens that vanish are marked rugged, which is the correct
  pessimistic default, but a token that just drops off DexScreener's batch for
  30 min without rugging would be mislabelled. Rare; accepted.
