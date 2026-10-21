# Outcome Tracking + Insider/Bundle Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the forward price path of every alert/reject (reported by a `!results` Discord command), and drop/label insider-heavy or dev-heavy tokens using RugCheck data we already fetch.

**Architecture:** RugCheck's discarded insider fields are mapped into `RiskData`. A shared `filters.insider_drop_reason` gates both VOLUME SPIKE (`filters.evaluate`) and NEW PUMP (`momentum.scan`). A new `scanner/outcomes.py` records one `alert_outcomes` row per alert or reject (in `memescreen.db`) and advances it each cycle from the latest polled snapshot for 6h. Pure `summarize`/`format_results` functions feed `!results`, which the co-hosted journal bot serves through an injected callable.

**Tech Stack:** Python 3.11+ (local 3.14, Railway 3.12), sqlite3, httpx, discord.py; tests are pytest + respx. Run tests with `py -m pytest`.

**Spec:** `docs/superpowers/specs/2026-10-21-outcomes-and-insider-gate-design.md`

## Global Constraints

- Alert-only. No trading or execution code anywhere.
- No new dependencies and no new paid APIs. RugCheck (keyless) and the existing DexScreener poll only.
- `INSIDER_MAX_PCT` default `30.0`, `CREATOR_MAX_PCT` default `15.0`, both env-overridable via `config.env`.
- Insider/dev gate is **fail-open**: a `None` value never drops a token.
- Outcome window `6h`; checkpoints `+15m`, `+1h`, `+6h`; rug = liq ≤ `20%` of `liq0`, or price ≤ `10%` of `price0`, or no snapshot for ≥ `30 min` measured against the newest `token_history` ts overall.
- Outcome recording/updating never raises into the caller (log + swallow).
- `!results` default `7` days, clamped to `1..30`; reply stays < 1900 chars.
- Gate drop reasons are stable strings: `insiders ≥30%`, `dev holds ≥15%` (built from the config values).
- Commits: `git -c user.name="memescreen" -c user.email="noreply@localhost" commit ...`, message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on branch `feature/outcomes-insider-gate` (already exists, spec committed).
- All 337 pre-existing tests stay green.

## Review Focus

1. **A token that keeps re-triggering** (a reject every 60s cycle, e.g. `holders flat`) must produce one open outcome row per `(address, kind)`, not hundreds. Pinned by `test_record_dedups_open_row` (Task 5).
2. **A scanner restart or DexScreener outage longer than 30 min** must not mark every open row rugged. Staleness is measured against the newest `token_history` ts. Pinned by `test_poller_outage_does_not_rug` (Task 5).
3. **Malformed / partial RugCheck payloads** (`insiderNetworks: null`, string numbers, `supply: 0`, summary endpoint) yield `None`, never an exception. Pinned by `test_malformed_insider_fields_become_none` (Task 1).
4. **A wallet-buy mint DexScreener never returns** must close quietly at 6h and be reported as `no price`, not crash or skew medians. Pinned by `test_never_priced_row_closes_at_6h_without_data` (Task 5) and `test_summarize_counts_no_price_rows` (Task 6).
5. **`!results` on an empty database / before any alert** gives a friendly message, not a crash or empty reply. Pinned by `test_format_results_empty` (Task 6) and `test_results_command_uses_injected_fn` (Task 7).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/memescreen/models.py` | modify | `RiskData` gains 4 optional insider fields |
| `src/memescreen/providers/rugcheck.py` | modify | map insider/creator/holder fields defensively |
| `src/memescreen/config.py` | modify | `INSIDER_MAX_PCT`, `CREATOR_MAX_PCT` |
| `src/memescreen/scanner/filters.py` | modify | `insider_drop_reason()`; new step 7b |
| `src/memescreen/scanner/alerter.py` | modify | `_insider_line()`; shown on spike / reject / pump; pump wording |
| `src/memescreen/scanner/momentum.py` | modify | insider gate on NEW PUMP; outcome recording |
| `src/memescreen/scanner/store.py` | modify | `alert_outcomes` table + 6 methods |
| `src/memescreen/scanner/outcomes.py` | **create** | record / advance / update / summarize / format / results_text |
| `src/memescreen/scanner/run.py` | modify | call `outcomes.update`, record spike + reject, pass store to walletwatch |
| `src/memescreen/scanner/walletwatch.py` | modify | optional `store` arg; record `wallet` outcomes |
| `src/memescreen/journal/messages.py` | modify | parse `!results [days]` |
| `src/memescreen/journal/handler.py` | modify | `results` callable arg; help text |
| `src/memescreen/journal/bot.py` | modify | inject `outcomes.results_text` when co-hosted |
| `tests/fixtures/rugcheck_report.json` | modify | add new fields (clean values) |
| `tests/fixtures/rugcheck_report_insiders.json` | **create** | insider-heavy report |
| `tests/scanner/test_outcomes.py` | **create** | outcome tests |
| existing test files | modify | new cases + 2 lambda signature updates |

---

### Task 1: Insider fields in `RiskData` + RugCheck mapping

**Files:**
- Modify: `src/memescreen/models.py` (class `RiskData`, ~line 33)
- Modify: `src/memescreen/providers/rugcheck.py`
- Modify: `tests/fixtures/rugcheck_report.json`
- Create: `tests/fixtures/rugcheck_report_insiders.json`
- Test: `tests/providers/test_rugcheck.py`

**Interfaces:**
- Produces: `RiskData.insider_pct: float | None`, `RiskData.insider_network_size: int | None`, `RiskData.creator_pct: float | None`, `RiskData.total_holders: int | None` (all default `None`).

- [ ] **Step 1: Update fixtures**

In `tests/fixtures/rugcheck_report.json`, replace the `"token"` line and add four top-level keys (keep everything else):

```json
  "token": {"mintAuthority": null, "freezeAuthority": null, "decimals": 6, "supply": 1000000000000000},
  "creator": "CreatorWa11et1111111111111111111111111111111",
  "creatorBalance": 20000000000000,
  "graphInsidersDetected": 0,
  "insiderNetworks": null,
  "totalHolders": 1204,
```

Create `tests/fixtures/rugcheck_report_insiders.json`:

```json
{
  "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
  "mintAuthority": null,
  "freezeAuthority": null,
  "token": {"mintAuthority": null, "freezeAuthority": null, "decimals": 6, "supply": 1000000000000000},
  "tokenMeta": {"name": "bundled", "symbol": "BNDL"},
  "creator": "CreatorWa11et1111111111111111111111111111111",
  "creatorBalance": 180000000000000,
  "graphInsidersDetected": 31,
  "insiderNetworks": [
    {"id": "a", "size": 48, "type": "transfer", "activeAccounts": 48},
    {"id": "b", "size": 6, "type": "trade", "activeAccounts": 6}
  ],
  "totalHolders": 312,
  "topHolders": [
    {"address": "I1", "pct": 12.5, "insider": true},
    {"address": "I2", "pct": 9.0, "insider": true},
    {"address": "I3", "pct": 8.5, "insider": true},
    {"address": "N1", "pct": 4.0, "insider": false}
  ],
  "risks": [],
  "markets": [{"lp": {"lpLockedPct": 100.0}}],
  "score": 50,
  "score_normalised": 5,
  "rugged": false
}
```

- [ ] **Step 2: Write the failing tests** (append to `tests/providers/test_rugcheck.py`)

```python
@respx.mock
def test_clean_report_maps_insider_fields():
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report.json"))
    )
    r = fetch_risk_data(ADDR)
    assert r.insider_pct == 0.0            # holders carry the key, none flagged
    assert r.insider_network_size == 0     # no networks -> graphInsidersDetected
    assert r.creator_pct == pytest.approx(2.0)
    assert r.total_holders == 1204


@respx.mock
def test_insider_heavy_report():
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report_insiders.json"))
    )
    r = fetch_risk_data(ADDR)
    assert r.insider_pct == pytest.approx(30.0)
    assert r.insider_network_size == 48    # largest network wins
    assert r.creator_pct == pytest.approx(18.0)
    assert r.total_holders == 312


@respx.mock
def test_summary_fallback_leaves_insider_fields_none():
    respx.get(REPORT_URL).mock(return_value=httpx.Response(403))
    respx.get(SUMMARY_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_summary.json"))
    )
    r = fetch_risk_data(ADDR)
    assert r.insider_pct is None
    assert r.insider_network_size is None
    assert r.creator_pct is None
    assert r.total_holders is None


@respx.mock
def test_malformed_insider_fields_become_none():
    data = _load("rugcheck_report.json")
    data["topHolders"] = [{"address": "x", "pct": 5.0}]   # no insider key
    data["insiderNetworks"] = {"not": "a list"}
    data["graphInsidersDetected"] = "lots"
    data["creatorBalance"] = "123"
    data["token"]["supply"] = 0
    data["totalHolders"] = None
    respx.get(REPORT_URL).mock(return_value=httpx.Response(200, json=data))
    r = fetch_risk_data(ADDR)
    assert r.insider_pct is None
    assert r.insider_network_size is None
    assert r.creator_pct is None
    assert r.total_holders is None
```

- [ ] **Step 3: Run to verify they fail**

Run: `py -m pytest tests/providers/test_rugcheck.py -v`
Expected: the 4 new tests FAIL with `AttributeError: 'RiskData' object has no attribute 'insider_pct'`.

- [ ] **Step 4: Add the model fields**

In `src/memescreen/models.py`, inside `RiskData`, insert between `danger_flags` and `source`:

```python
    # Insider / bundle / dev concentration from RugCheck's full report
    # (spec 2026-10-21 §5). None = RugCheck didn't say (summary endpoint,
    # brand-new token) -- never treated as "clean" or "dirty".
    insider_pct: float | None = None           # sum of pct over top holders flagged insider
    insider_network_size: int | None = None    # largest linked-wallet network
    creator_pct: float | None = None           # creator wallet's % of supply
    total_holders: int | None = None
```

- [ ] **Step 5: Map them in `rugcheck.py`**

Add these helpers above `_map_report`:

```python
def _num(v) -> float | None:
    """A JSON number -> float. Strings, bools, None and junk -> None (a
    malformed field must never raise out of the mapper)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _insider_pct(raw_holders) -> float | None:
    """Sum of `pct` over top holders RugCheck flags `insider: true`. None when
    no holder carries the `insider` key at all (analysis absent, not zero)."""
    saw_key = False
    total = 0.0
    for h in raw_holders or []:
        if not isinstance(h, dict) or "insider" not in h:
            continue
        saw_key = True
        pct = _num(h.get("pct"))
        if h.get("insider") is True and pct is not None:
            total += pct
    return total if saw_key else None


def _insider_network_size(data: dict) -> int | None:
    nets = data.get("insiderNetworks")
    sizes = []
    if isinstance(nets, list):
        for n in nets:
            size = _num(n.get("size")) if isinstance(n, dict) else None
            if size is not None:
                sizes.append(int(size))
    if sizes:
        return max(sizes)
    detected = _num(data.get("graphInsidersDetected"))
    return int(detected) if detected is not None else None


def _creator_pct(data: dict, token: dict) -> float | None:
    balance = _num(data.get("creatorBalance"))
    supply = _num(token.get("supply"))
    if balance is None or supply is None or supply <= 0:
        return None
    return balance / supply * 100.0
```

In `_map_report`, change the `return RiskData(...)` to add the four fields:

```python
    total_holders = _num(data.get("totalHolders"))

    return RiskData(
        rugcheck_score=score,
        mint_authority_enabled=mint,
        freeze_authority_enabled=freeze,
        lp_locked_pct=float(lp_locked) if lp_locked is not None else None,
        top_holders=holders,
        risk_flags=flags,
        danger_flags=danger,
        insider_pct=_insider_pct(data.get("topHolders")),
        insider_network_size=_insider_network_size(data),
        creator_pct=_creator_pct(data, token),
        total_holders=int(total_holders) if total_holders is not None else None,
    )
```

Add `import pytest` to the test file's imports if it isn't there (it is: line 6).

- [ ] **Step 6: Run tests**

Run: `py -m pytest tests/providers/test_rugcheck.py -v` → all PASS.
Run: `py -m pytest -q` → all PASS (337 + 4).

- [ ] **Step 7: Commit**

```bash
git add src/memescreen/models.py src/memescreen/providers/rugcheck.py tests/fixtures/rugcheck_report.json tests/fixtures/rugcheck_report_insiders.json tests/providers/test_rugcheck.py
git -c user.name="memescreen" -c user.email="noreply@localhost" commit -m "feat(rugcheck): map insider, creator and holder-count fields

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Insider/dev gate on VOLUME SPIKE

**Files:**
- Modify: `src/memescreen/config.py` (after `RUGCHECK_DANGER_MAX`, ~line 60)
- Modify: `src/memescreen/scanner/filters.py`
- Test: `tests/scanner/test_filters.py`

**Interfaces:**
- Consumes: `RiskData.insider_pct`, `RiskData.creator_pct` (Task 1).
- Produces: `config.INSIDER_MAX_PCT: float`, `config.CREATOR_MAX_PCT: float`, `filters.insider_drop_reason(risk: RiskData | None) -> str | None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/scanner/test_filters.py`)

```python
# --- 7b. insider / dev concentration (fail-open) ----------------------------

def test_insider_drop_reason_thresholds():
    assert filters.insider_drop_reason(None) is None
    assert filters.insider_drop_reason(_risk()) is None                      # all None
    assert filters.insider_drop_reason(_risk(insider_pct=29.9)) is None
    assert filters.insider_drop_reason(_risk(insider_pct=30.0)) == "insiders ≥30%"
    assert filters.insider_drop_reason(_risk(creator_pct=14.9)) is None
    assert filters.insider_drop_reason(_risk(creator_pct=15.0)) == "dev holds ≥15%"


def test_insider_heavy_token_is_dropped_before_birdeye(monkeypatch):
    with Store(":memory:") as store:
        _passing_setup(store, monkeypatch)
        birdeye_calls = []
        monkeypatch.setattr(
            filters.birdeye, "fetch_holder_data",
            lambda addr: birdeye_calls.append(addr) or _holders(),
        )
        risk_cache = _RiskCacheStub(_risk(insider_pct=42.0))

        passed, reason, ctx = filters.evaluate(_hit(), store, risk_cache, _snap())

        assert (passed, reason) == (False, "insiders ≥30%")
        assert ctx.risk.insider_pct == 42.0
        assert birdeye_calls == []          # gate ran before the paid call


def test_dev_heavy_token_is_dropped(monkeypatch):
    with Store(":memory:") as store:
        _passing_setup(store, monkeypatch)
        risk_cache = _RiskCacheStub(_risk(creator_pct=20.0))
        passed, reason, _ = filters.evaluate(_hit(), store, risk_cache, _snap())
        assert (passed, reason) == (False, "dev holds ≥15%")


def test_unknown_insider_data_passes(monkeypatch):
    with Store(":memory:") as store:
        risk_cache = _passing_setup(store, monkeypatch)   # _risk(): insider fields None
        passed, reason, _ = filters.evaluate(_hit(), store, risk_cache, _snap())
        assert (passed, reason) == (True, "")


def test_insider_threshold_is_env_tunable(monkeypatch):
    monkeypatch.setattr(config, "INSIDER_MAX_PCT", 10.0)
    assert filters.insider_drop_reason(_risk(insider_pct=12.0)) == "insiders ≥10%"
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -m pytest tests/scanner/test_filters.py -v -k "insider or dev_heavy"`
Expected: FAIL with `AttributeError: module 'memescreen.scanner.filters' has no attribute 'insider_drop_reason'`.

- [ ] **Step 3: Add config**

In `src/memescreen/config.py`, directly after the `RUGCHECK_DANGER_MAX` line:

```python
# Insider / bundle / dev gate (spec 2026-10-21 §5.3). Drops VOLUME SPIKE and
# NEW PUMP candidates whose flagged-insider top holders, or creator wallet,
# hold at least this % of supply. Fail-open: unknown values never drop.
INSIDER_MAX_PCT: float = env("INSIDER_MAX_PCT", 30.0, float)
CREATOR_MAX_PCT: float = env("CREATOR_MAX_PCT", 15.0, float)
```

- [ ] **Step 4: Add the helper and the gate step in `filters.py`**

Make sure `RiskData` is imported from `memescreen.models` (add it to the existing models import). Add above `evaluate`:

```python
def insider_drop_reason(risk: RiskData | None) -> str | None:
    """Spec 2026-10-21 §5.3. A drop reason when insiders or the creator hold
    too much supply, else None. Fail-open: a None reading never drops --
    RugCheck often has no insider analysis for minutes-old tokens. Reasons
    name the threshold (not the token's value) so the cycle tally groups."""
    if risk is None:
        return None
    if risk.insider_pct is not None and risk.insider_pct >= config.INSIDER_MAX_PCT:
        return f"insiders ≥{config.INSIDER_MAX_PCT:g}%"
    if risk.creator_pct is not None and risk.creator_pct >= config.CREATOR_MAX_PCT:
        return f"dev holds ≥{config.CREATOR_MAX_PCT:g}%"
    return None
```

In `evaluate`, directly after the step-7 block (the second `return _done(False, "rugcheck danger")`) and before the `# 8. holder growth` comment:

```python
    # 7b. insider / bundle / dev concentration (fail-open). Before the Birdeye
    # call so a dropped token costs no Birdeye credit.
    insider_reason = insider_drop_reason(risk)
    if insider_reason is not None:
        return _done(False, insider_reason)
```

- [ ] **Step 5: Run tests**

Run: `py -m pytest tests/scanner/test_filters.py -v` → all PASS.
Run: `py -m pytest -q` → all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/memescreen/config.py src/memescreen/scanner/filters.py tests/scanner/test_filters.py
git -c user.name="memescreen" -c user.email="noreply@localhost" commit -m "feat(filters): insider/dev concentration gate (fail-open)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Insider line on alerts + NEW PUMP gate

**Files:**
- Modify: `src/memescreen/scanner/alerter.py` (`format_message`, `send_reject`, `send_pump`)
- Modify: `src/memescreen/scanner/momentum.py` (`scan`, module docstring)
- Test: `tests/scanner/test_alerter.py`, `tests/scanner/test_momentum.py`

**Interfaces:**
- Consumes: `filters.insider_drop_reason` (Task 2), `RiskData` insider fields (Task 1).
- Produces: `alerter._insider_line(risk: RiskData | None) -> str | None`; `alerter.send_pump(snap, top10_pct, age_min, now, risk: RiskData | None = None) -> bool`. Task 5 adds outcome recording into the `momentum.scan` branches created here.

- [ ] **Step 1: Write the failing alerter tests** (append to `tests/scanner/test_alerter.py`)

```python
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
```

(`_hit`, `_ctx`, `_pump_snap`, `_PUMP_HOOK`, `_PUMP_POST`, `_OK`, `_NOW` already exist in this test file. If `test_send_pump_posts_the_new_pump_line` uses a `_narrative` stub, mirror it here. Check its decorators and copy them exactly.)

- [ ] **Step 2: Write the failing momentum tests** (append to `tests/scanner/test_momentum.py`)

```python
def test_scan_drops_insider_heavy_runner(monkeypatch):
    _rejects_on(monkeypatch)
    sent = []
    monkeypatch.setattr(momentum.alerter, "send_pump",
                        lambda *a, **k: sent.append(1) or True)
    risk = _clean_risk()
    risk.insider_pct = 45.0
    with Store(":memory:") as s:
        s.append_snapshots([_snap()])
        assert momentum.scan(s, _Risk(risk), T0) == 0
    assert sent == []


def test_scan_passes_risk_to_send_pump(monkeypatch):
    _rejects_on(monkeypatch)
    seen = []
    monkeypatch.setattr(momentum.alerter, "send_pump",
                        lambda snap, top10, age_min, now, risk=None: seen.append(risk) or True)
    risk = _clean_risk()
    with Store(":memory:") as s:
        s.append_snapshots([_snap()])
        momentum.scan(s, _Risk(risk), T0)
    assert seen == [risk]
```

Also update the existing lambda in `test_scan_alerts_and_records_dedup` (≈ line 112) from
`lambda snap, top10, age_min, now: sent.append(snap.address) or True`
to
`lambda snap, top10, age_min, now, risk=None: sent.append(snap.address) or True`.

- [ ] **Step 3: Run to verify they fail**

Run: `py -m pytest tests/scanner/test_alerter.py tests/scanner/test_momentum.py -v -k "insider or light_check or passes_risk"`
Expected: FAIL (`_insider_line` missing; `send_pump` got unexpected keyword `risk`; insider-heavy runner still sent).

- [ ] **Step 4: Implement `_insider_line` and wire it into the three messages**

In `alerter.py`, add after `_safety`:

```python
def _insider_line(risk: RiskData | None) -> str | None:
    """One-line insider / bundle / dev summary (spec 2026-10-21 §5.4). Parts
    RugCheck didn't supply are omitted; None when there is nothing to say."""
    if risk is None:
        return None
    parts = []
    if risk.insider_pct is not None:
        net = f" (net {risk.insider_network_size})" if risk.insider_network_size else ""
        parts.append(f"insiders {risk.insider_pct:.0f}%{net}")
    elif risk.insider_network_size:
        parts.append(f"insider net {risk.insider_network_size}")
    if risk.creator_pct is not None:
        parts.append(f"dev {risk.creator_pct:.0f}%")
    if risk.total_holders is not None:
        parts.append(f"{risk.total_holders:,} holders")
    return "\U0001f9e9 " + " · ".join(parts) if parts else None
```

In `format_message`, compute `insider = _insider_line(ctx.risk)` before `lines = [`, and directly after the `_safety(ctx.risk),` entry add:

```python
        *([insider] if insider else []),
```

In `send_reject`, replace the `line = (...)` assignment with:

```python
    insider = _insider_line(ctx.risk)
    line = (
        f"❌ {sym}  spike {hit.spike_ratio:.1f}x / {hit.buys_m5} buys  "
        f"·  dropped: {reason}\n"
        + (f"{insider}\n" if insider else "")
        + f"{hit.address}\n"
        f"https://dexscreener.com/solana/{hit.address}"
    )
```

(Keep whatever follows the assignment unchanged. Compare with the current
body before editing, so the first line's text stays identical.)

Change `send_pump`'s signature and body:

```python
def send_pump(
    snap: MarketSnapshot, top10_pct: float, age_min: float, now: datetime,
    risk: RiskData | None = None,
) -> bool:
```

Update its docstring sentence "No safety gate ran, so the line says so." to
"Only the light insider/dev check ran (momentum.scan), so the line says so."
Then replace the `unvetted — no safety gate ran, DYOR\n` line in the f-string with:

```python
        f"light check only (insiders/dev) — DYOR\n"
        + (f"{insider}\n" if (insider := _insider_line(risk)) else "")
        + f"{snap.address}\n"
```

and make the remaining two lines plain continuations:

```python
        f"https://dexscreener.com/solana/{snap.address}\n"
        f"Run: screen {snap.address}"
```

The full `line = (...)` for `send_pump` must read:

```python
    line = (
        f"\U0001f4c8 NEW PUMP — ${sym}   +{pct:.0f}% 1h  ·  age {_age(age_min)}  ·  "
        f"vol/mcap {vol_mcap:.1f}x  ·  top10 {top10_pct:.0f}%  ·  liq {_money(snap.liquidity_usd)}\n"
        f"light check only (insiders/dev) — DYOR\n"
        + (f"{insider}\n" if (insider := _insider_line(risk)) else "")
        + f"{snap.address}\n"
        f"https://dexscreener.com/solana/{snap.address}\n"
        f"Run: screen {snap.address}"
    )
```

- [ ] **Step 5: Gate NEW PUMP in `momentum.scan`**

Add `filters` to the scanner import: `from memescreen.scanner import alerter, candidates, filters`.
Replace the body of the `for snap in store.latest_snapshots():` loop from `top10 = ...` through the `send_pump` block with:

```python
        risk = risk_cache.get(snap.address)
        top10 = _top10_pct(risk)
        if top10 is None or top10 > config.PUMP_MAX_TOP10_PCT:
            continue
        drop = filters.insider_drop_reason(risk)
        if drop is not None:
            _log.info("momentum: %s dropped (%s)", snap.address[:8], drop)
            continue
        age_min = (now - snap.pair_created_at).total_seconds() / 60.0
        if alerter.send_pump(snap, top10, age_min, now, risk=risk):
            store.record_pump_alert(snap.address, now, snap.price_change_h1)
            sent += 1
            candidates.record_hit(snap.address, "pump", now)
```

Update the module docstring paragraph "There is deliberately NO safety gate here..." to:

```
The only safety check here is the light insider/dev gate
(`filters.insider_drop_reason`, fail-open). These alerts go to their own
`DISCORD_PUMP_WEBHOOK_URL` channel labelled "light check only". Dedup is one
alert per token per `PUMP_COOLDOWN_H`, tracked in `pump_alerts_sent`.
```

Also update the `config.py` comment above `PUMP_PRICE_PCT` ("NO safety gate -- the alert is labelled unvetted on purpose.") to "only the light insider/dev gate -- labelled 'light check only'."

- [ ] **Step 6: Run tests**

Run: `py -m pytest tests/scanner/test_alerter.py tests/scanner/test_momentum.py -v` → all PASS.
Run: `py -m pytest -q` → all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/memescreen/scanner/alerter.py src/memescreen/scanner/momentum.py src/memescreen/config.py tests/scanner/test_alerter.py tests/scanner/test_momentum.py
git -c user.name="memescreen" -c user.email="noreply@localhost" commit -m "feat(alerts): insider/dev line on alerts; gate NEW PUMP on it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `alert_outcomes` storage

**Files:**
- Modify: `src/memescreen/scanner/store.py`
- Test: `tests/scanner/test_store.py`

**Interfaces:**
- Produces (all on `Store`):
  - `has_open_outcome(address: str, kind: str) -> bool`
  - `insert_outcome(fields: dict) -> int` (returns row id; unknown column → `ValueError`)
  - `open_outcomes() -> list[dict]` (raw row dicts; datetimes are ISO strings)
  - `update_outcome(row_id: int, fields: dict) -> None` (unknown column → `ValueError`)
  - `outcomes_since(since: datetime) -> list[dict]`
  - `newest_history_ts() -> datetime | None`
  - module constant `OUTCOME_COLUMNS: frozenset[str]`

- [ ] **Step 1: Write the failing tests** (append to `tests/scanner/test_store.py`; reuse its existing imports and add any missing: `from datetime import UTC, datetime, timedelta`, `import pytest`, `from memescreen.models import MarketSnapshot`, `from memescreen.scanner.store import Store`)

```python
# --- alert_outcomes -----------------------------------------------------------

_OT0 = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _outcome(**over) -> dict:
    base = dict(address="tok", kind="spike", alerted_at=_OT0.isoformat(), price0=1.0)
    base.update(over)
    return base


def test_outcome_insert_open_update_close():
    with Store(":memory:") as s:
        rid = s.insert_outcome(_outcome())
        assert s.has_open_outcome("tok", "spike") is True
        assert s.has_open_outcome("tok", "pump") is False
        [row] = s.open_outcomes()
        assert row["id"] == rid and row["price0"] == 1.0 and row["rugged"] == 0
        s.update_outcome(rid, {"price_15m": 1.2, "closed": 1})
        assert s.open_outcomes() == []
        assert s.has_open_outcome("tok", "spike") is False


def test_outcome_rejects_unknown_columns():
    with Store(":memory:") as s:
        with pytest.raises(ValueError):
            s.insert_outcome(_outcome(bogus=1))
        rid = s.insert_outcome(_outcome())
        with pytest.raises(ValueError):
            s.update_outcome(rid, {"id = 0; --": 1})


def test_outcomes_since_filters_by_alerted_at():
    with Store(":memory:") as s:
        s.insert_outcome(_outcome(alerted_at=(_OT0 - timedelta(days=10)).isoformat()))
        s.insert_outcome(_outcome(address="new", alerted_at=_OT0.isoformat()))
        rows = s.outcomes_since(_OT0 - timedelta(days=7))
        assert [r["address"] for r in rows] == ["new"]


def test_newest_history_ts():
    with Store(":memory:") as s:
        assert s.newest_history_ts() is None
        s.append_snapshots([
            MarketSnapshot(address="a", fetched_at=_OT0),
            MarketSnapshot(address="b", fetched_at=_OT0 + timedelta(minutes=1)),
        ])
        assert s.newest_history_ts() == _OT0 + timedelta(minutes=1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -m pytest tests/scanner/test_store.py -v -k "outcome or newest"`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'insert_outcome'`.

- [ ] **Step 3: Add schema** — append inside the `_SCHEMA` string, before its closing `"""`:

```sql
CREATE TABLE IF NOT EXISTS alert_outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL,
  kind TEXT NOT NULL,
  reason TEXT,
  alerted_at TEXT NOT NULL,
  symbol TEXT,
  price0 REAL, mcap0 REAL, liq0 REAL,
  age_min0 REAL, change_h1_0 REAL,
  insider_pct REAL, insider_net INTEGER, creator_pct REAL,
  price_15m REAL, price_1h REAL, price_6h REAL,
  peak_price REAL, trough_price REAL,
  last_price REAL, last_seen_at TEXT,
  rugged INTEGER NOT NULL DEFAULT 0,
  closed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_outcomes_open ON alert_outcomes(closed, address);
CREATE INDEX IF NOT EXISTS ix_outcomes_time ON alert_outcomes(alerted_at);
```

- [ ] **Step 4: Add the column whitelist** below `_HISTORY_ADDED_COLUMNS`:

```python
# Writable alert_outcomes columns. Column names are interpolated into SQL by
# insert_outcome / update_outcome, so anything not listed here is refused.
OUTCOME_COLUMNS: frozenset[str] = frozenset({
    "address", "kind", "reason", "alerted_at", "symbol",
    "price0", "mcap0", "liq0", "age_min0", "change_h1_0",
    "insider_pct", "insider_net", "creator_pct",
    "price_15m", "price_1h", "price_6h", "peak_price", "trough_price",
    "last_price", "last_seen_at", "rugged", "closed",
})
```

- [ ] **Step 5: Add the methods** at the end of `class Store`:

```python
    # --- alert_outcomes (spec 2026-10-21 §4) ---

    @staticmethod
    def _check_outcome_columns(fields: dict) -> None:
        bad = set(fields) - OUTCOME_COLUMNS
        if bad:
            raise ValueError(f"unknown alert_outcomes column(s): {sorted(bad)}")

    def has_open_outcome(self, address: str, kind: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM alert_outcomes WHERE address = ? AND kind = ? AND closed = 0 LIMIT 1",
            (address, kind),
        )
        return cur.fetchone() is not None

    def insert_outcome(self, fields: dict) -> int:
        self._check_outcome_columns(fields)
        cols = list(fields)
        cur = self._conn.execute(
            f"INSERT INTO alert_outcomes ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            [fields[c] for c in cols],
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def open_outcomes(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM alert_outcomes WHERE closed = 0 ORDER BY id"
        )
        return [dict(r) for r in cur.fetchall()]

    def update_outcome(self, row_id: int, fields: dict) -> None:
        if not fields:
            return
        self._check_outcome_columns(fields)
        sets = ", ".join(f"{c} = ?" for c in fields)
        self._conn.execute(
            f"UPDATE alert_outcomes SET {sets} WHERE id = ?",
            [*fields.values(), row_id],
        )
        self._conn.commit()

    def outcomes_since(self, since: datetime) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM alert_outcomes WHERE alerted_at >= ? ORDER BY alerted_at",
            (since.isoformat(),),
        )
        return [dict(r) for r in cur.fetchall()]

    def newest_history_ts(self) -> datetime | None:
        """Newest poll timestamp across all tokens. Outcome staleness is
        measured against this, not wall-clock, so a poller outage never
        looks like every token rugging at once."""
        row = self._conn.execute("SELECT MAX(ts) AS ts FROM token_history").fetchone()
        if row is None or row["ts"] is None:
            return None
        return datetime.fromisoformat(row["ts"])
```

Also update the module docstring's first sentence to include "alert outcomes".

- [ ] **Step 6: Run tests**

Run: `py -m pytest tests/scanner/test_store.py -v` → all PASS. `py -m pytest -q` → all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/memescreen/scanner/store.py tests/scanner/test_store.py
git -c user.name="memescreen" -c user.email="noreply@localhost" commit -m "feat(store): alert_outcomes table

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Outcome recording + per-cycle update, wired in

**Files:**
- Create: `src/memescreen/scanner/outcomes.py`
- Modify: `src/memescreen/scanner/run.py`, `src/memescreen/scanner/momentum.py`, `src/memescreen/scanner/walletwatch.py`
- Create: `tests/scanner/test_outcomes.py`
- Modify: `tests/scanner/test_run.py` (one lambda), `tests/scanner/test_momentum.py`, `tests/scanner/test_walletwatch.py`

**Interfaces:**
- Consumes: Task 4 store methods; `RiskData` insider fields (Task 1); the momentum branches from Task 3.
- Produces:
  - `outcomes.record(store: Store, *, address: str, kind: str, now: datetime, snap: MarketSnapshot | None = None, reason: str | None = None, risk: RiskData | None = None, age_min: float | None = None) -> bool` (True when a row was inserted)
  - `outcomes.advance(row: dict, snap: MarketSnapshot | None, now: datetime, newest_ts: datetime | None) -> dict` (pure; changed fields)
  - `outcomes.update(store: Store, now: datetime) -> int` (rows closed)
  - constants `WINDOW`, `CHECKPOINTS`, `STALE`, `KINDS`
  - `walletwatch.scan(now: datetime, store: Store | None = None) -> int`

- [ ] **Step 1: Write the failing tests** — create `tests/scanner/test_outcomes.py`:

```python
"""Outcome tracking (spec 2026-10-21 §4): record / advance / update."""
from datetime import UTC, datetime, timedelta

from memescreen.models import MarketSnapshot, RiskData
from memescreen.scanner import outcomes
from memescreen.scanner.store import Store

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _snap(price=1.0, at=T0, liq=50_000.0, addr="tok", **kw) -> MarketSnapshot:
    return MarketSnapshot(
        address=addr, fetched_at=at, symbol="TOK", price_usd=price,
        liquidity_usd=liq, market_cap_usd=price * 1_000_000,
        pair_created_at=T0 - timedelta(minutes=95), price_change_h1=40.0, **kw,
    )


def _risk() -> RiskData:
    return RiskData(rugcheck_score=1, mint_authority_enabled=False,
                    freeze_authority_enabled=False, lp_locked_pct=100.0,
                    insider_pct=12.0, insider_network_size=48, creator_pct=3.0)


# --- record -------------------------------------------------------------------

def test_record_captures_alert_time_state():
    with Store(":memory:") as s:
        assert outcomes.record(s, address="tok", kind="spike", now=T0,
                               snap=_snap(), risk=_risk()) is True
        [row] = s.open_outcomes()
        assert row["price0"] == 1.0 and row["liq0"] == 50_000.0
        assert row["age_min0"] == 95.0 and row["change_h1_0"] == 40.0
        assert row["insider_pct"] == 12.0 and row["insider_net"] == 48
        assert row["peak_price"] == row["trough_price"] == row["last_price"] == 1.0
        assert "tok" in s.universe_addresses()      # tracked for the window


def test_record_dedups_open_row():
    with Store(":memory:") as s:
        for i in range(5):
            outcomes.record(s, address="tok", kind="spike_reject", now=T0 + timedelta(minutes=i),
                            snap=_snap(), reason="holders flat")
        outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap())
        kinds = sorted(r["kind"] for r in s.open_outcomes())
        assert kinds == ["spike", "spike_reject"]


def test_record_without_snapshot_is_allowed():
    with Store(":memory:") as s:
        assert outcomes.record(s, address="mint", kind="wallet", now=T0, reason="whale") is True
        [row] = s.open_outcomes()
        assert row["price0"] is None and row["reason"] == "whale"


def test_record_never_raises(monkeypatch):
    with Store(":memory:") as s:
        monkeypatch.setattr(s, "insert_outcome", lambda f: 1 / 0)
        assert outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap()) is False


# --- advance (pure) -------------------------------------------------------------

def _row(**over) -> dict:
    base = dict(
        id=1, address="tok", kind="spike", alerted_at=T0.isoformat(), symbol="TOK",
        price0=1.0, mcap0=1e6, liq0=50_000.0,
        price_15m=None, price_1h=None, price_6h=None,
        peak_price=1.0, trough_price=1.0, last_price=1.0,
        last_seen_at=T0.isoformat(), rugged=0, closed=0,
    )
    base.update(over)
    return base


def test_advance_tracks_peak_trough_and_checkpoints():
    row = _row()
    f = outcomes.advance(row, _snap(price=2.0, at=T0 + timedelta(minutes=16)),
                         T0 + timedelta(minutes=16), T0 + timedelta(minutes=16))
    assert f["peak_price"] == 2.0 and f["trough_price"] == 1.0
    assert f["price_15m"] == 2.0
    assert "price_1h" not in f and "closed" not in f


def test_advance_ignores_already_seen_snapshot():
    f = outcomes.advance(_row(), _snap(price=1.0, at=T0), T0 + timedelta(minutes=1), T0)
    assert "last_price" not in f and "peak_price" not in f


def test_advance_closes_at_6h_and_fills_6h_checkpoint():
    now = T0 + timedelta(hours=6, minutes=1)
    f = outcomes.advance(_row(price_15m=1.1, price_1h=1.2), _snap(price=0.8, at=now), now, now)
    assert f["price_6h"] == 0.8 and f["closed"] == 1 and "rugged" not in f


def test_advance_rug_on_liquidity_pull_fills_checkpoints():
    now = T0 + timedelta(minutes=20)
    f = outcomes.advance(_row(), _snap(price=0.5, liq=5_000.0, at=now), now, now)
    assert f["rugged"] == 1 and f["closed"] == 1
    assert f["price_15m"] == 0.5 and f["price_1h"] == 0.5 and f["price_6h"] == 0.5


def test_advance_rug_on_price_collapse():
    now = T0 + timedelta(minutes=5)
    f = outcomes.advance(_row(), _snap(price=0.09, at=now), now, now)
    assert f["rugged"] == 1


def test_advance_rug_when_token_vanishes():
    # newest poll is 31 min after this token's last snapshot
    now = T0 + timedelta(minutes=31)
    f = outcomes.advance(_row(), None, now, now)
    assert f["rugged"] == 1


def test_poller_outage_does_not_rug():
    # wall clock is 2h later but the poller produced nothing new for anyone
    now = T0 + timedelta(hours=2)
    f = outcomes.advance(_row(), None, now, T0)
    assert "rugged" not in f


def test_backfills_price0_for_unpriced_row():
    row = _row(price0=None, mcap0=None, liq0=None, peak_price=None,
               trough_price=None, last_price=None, last_seen_at=None, symbol=None)
    now = T0 + timedelta(minutes=2)
    f = outcomes.advance(row, _snap(price=3.0, at=now), now, now)
    assert f["price0"] == 3.0 and f["liq0"] == 50_000.0 and f["symbol"] == "TOK"


def test_never_priced_row_closes_at_6h_without_data():
    row = _row(price0=None, mcap0=None, liq0=None, peak_price=None,
               trough_price=None, last_price=None, last_seen_at=None)
    now = T0 + timedelta(hours=6)
    f = outcomes.advance(row, None, now, now)
    assert f == {"closed": 1}


# --- update (store loop) -------------------------------------------------------

def test_update_advances_rows_and_keeps_tokens_tracked():
    with Store(":memory:") as s:
        s.append_snapshots([_snap()])
        outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap())
        later = T0 + timedelta(minutes=16)
        s.append_snapshots([_snap(price=1.5, at=later)])
        assert outcomes.update(s, later) == 0
        [row] = s.open_outcomes()
        assert row["price_15m"] == 1.5 and row["peak_price"] == 1.5


def test_update_protects_open_rows_from_eviction():
    # 4h on, the token left every feed (last_seen_in_feed = T0 is past the 3h
    # retention cut). Only update()'s touch_activity keeps it in the universe.
    with Store(":memory:") as s:
        outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap())
        much_later = T0 + timedelta(hours=4)
        s.append_snapshots([_snap(price=1.2, at=much_later)])
        outcomes.update(s, much_later)
        s.evict(much_later, cap=10_000)
        assert "tok" in s.universe_addresses()


def test_update_survives_a_bad_row(monkeypatch):
    with Store(":memory:") as s:
        outcomes.record(s, address="a", kind="spike", now=T0, snap=_snap(addr="a"))
        outcomes.record(s, address="b", kind="spike", now=T0, snap=_snap(addr="b"))
        real = outcomes.advance

        def flaky(row, snap, now, newest):
            if row["address"] == "a":
                raise RuntimeError("boom")
            return real(row, snap, now, newest)

        monkeypatch.setattr(outcomes, "advance", flaky)
        assert outcomes.update(s, T0 + timedelta(hours=7)) == 1   # b closed, a skipped
```

Append to `tests/scanner/test_momentum.py`:

```python
def test_scan_records_pump_and_pump_reject_outcomes(monkeypatch):
    _rejects_on(monkeypatch)
    monkeypatch.setattr(momentum.alerter, "send_pump", lambda *a, **k: True)
    heavy = _clean_risk()
    heavy.insider_pct = 45.0
    with Store(":memory:") as s:
        s.append_snapshots([_snap("ok"), _snap("bad")])
        risks = {"ok": _clean_risk(), "bad": heavy}

        class _ByAddr:
            def get(self, address):
                return risks[address]

        momentum.scan(s, _ByAddr(), T0)
        got = sorted((r["address"], r["kind"], r["reason"]) for r in s.open_outcomes())
    assert got == [("bad", "pump_reject", "insiders ≥30%"), ("ok", "pump", None)]
```

Append to `tests/scanner/test_walletwatch.py` (mirror the existing `test_subsequent_poll_alerts_oldest_first_and_advances_cursor` setup for how a wallet is seeded with a non-null cursor and `helius.fetch_wallet_buys` / `alerter.send_wallet_buy` are stubbed. Copy those lines exactly, then pass a store):

```python
def test_confirmed_wallet_buy_records_outcome(monkeypatch, tmp_path):
    from memescreen.scanner.store import Store
    _on(monkeypatch)
    db = str(tmp_path / "j.db")
    monkeypatch.setattr(config, "JOURNAL_DB_PATH", db)
    with JournalStore(db) as js:
        js.watch_wallet(address=WALLET, label="whale", user="u", now=T0)
        js.update_wallet_cursor(WALLET, "old-sig")
    monkeypatch.setattr(walletwatch.helius, "fetch_wallet_buys",
                        lambda addr, until_sig=None: [_buy("s1")])
    monkeypatch.setattr(walletwatch.alerter, "send_wallet_buy", lambda w, b: True)
    with Store(":memory:") as s:
        assert walletwatch.scan(T0, s) == 1
        [row] = s.open_outcomes()
    assert (row["address"], row["kind"], row["reason"]) == (MINT, "wallet", "whale")
```

(If `JournalStore.watch_wallet` / `update_wallet_cursor` signatures differ, use the exact calls the existing walletwatch tests use.)

In `tests/scanner/test_run.py` (≈ line 71) change
`lambda now: calls.append(now) or 0` → `lambda now, store=None: calls.append(now) or 0`.

- [ ] **Step 2: Run to verify they fail**

Run: `py -m pytest tests/scanner/test_outcomes.py tests/scanner/test_momentum.py tests/scanner/test_walletwatch.py -v`
Expected: FAIL with `ImportError: cannot import name 'outcomes'` (and the momentum/walletwatch cases fail).

- [ ] **Step 3: Create `src/memescreen/scanner/outcomes.py`**

```python
"""Forward price path of every alert and gate reject (spec 2026-10-21 §4).

`record()` writes one `alert_outcomes` row when an alert (or reject) happens.
`update()` runs once per cycle after the poll and advances every open row
from the token's latest snapshot: running peak/trough, +15m/+1h/+6h
checkpoints, rug detection, and closing at 6h. `advance()` is the pure core.

Nothing here may break the scan: every public entry point logs and swallows.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from memescreen.models import MarketSnapshot, RiskData
from memescreen.scanner.store import Store

_log = logging.getLogger("memescreen.scanner")

KINDS = ("spike", "spike_reject", "pump", "pump_reject", "wallet")
WINDOW = timedelta(hours=6)
CHECKPOINTS = (
    ("price_15m", timedelta(minutes=15)),
    ("price_1h", timedelta(hours=1)),
    ("price_6h", timedelta(hours=6)),
)
STALE = timedelta(minutes=30)
RUG_LIQ_FRAC = 0.2
RUG_PRICE_FRAC = 0.1


def record(
    store: Store, *, address: str, kind: str, now: datetime,
    snap: MarketSnapshot | None = None, reason: str | None = None,
    risk: RiskData | None = None, age_min: float | None = None,
) -> bool:
    """Open an outcome row. No-op (False) while one is already open for this
    (address, kind) -- a token re-rejected every cycle stays one row."""
    try:
        if store.has_open_outcome(address, kind):
            return False
        if age_min is None and snap is not None and snap.pair_created_at is not None:
            age_min = (now - snap.pair_created_at).total_seconds() / 60.0
        price = snap.price_usd if snap is not None else None
        store.insert_outcome({
            "address": address, "kind": kind, "reason": reason,
            "alerted_at": now.isoformat(),
            "symbol": snap.symbol if snap is not None else None,
            "price0": price,
            "mcap0": snap.market_cap_usd if snap is not None else None,
            "liq0": snap.liquidity_usd if snap is not None else None,
            "age_min0": age_min,
            "change_h1_0": snap.price_change_h1 if snap is not None else None,
            "insider_pct": risk.insider_pct if risk is not None else None,
            "insider_net": risk.insider_network_size if risk is not None else None,
            "creator_pct": risk.creator_pct if risk is not None else None,
            "peak_price": price, "trough_price": price, "last_price": price,
            "last_seen_at": (
                snap.fetched_at.isoformat() if snap is not None and price is not None else None
            ),
        })
        # Make sure the poller tracks it (wallet buys can be of untracked mints).
        store.upsert_seen({address}, now)
        return True
    except Exception:
        _log.exception("outcomes: record failed for %s", address[:8])
        return False


def advance(
    row: dict, snap: MarketSnapshot | None, now: datetime, newest_ts: datetime | None,
) -> dict:
    """Pure: the fields of `row` that change given its latest snapshot.
    `newest_ts` is the newest poll timestamp across ALL tokens -- staleness is
    measured against it so a poller outage never looks like a rug."""
    out: dict = {}
    cur = dict(row)
    alerted = datetime.fromisoformat(row["alerted_at"])
    last_seen = datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None
    liq_now = None

    if (
        snap is not None and snap.price_usd is not None
        and (last_seen is None or snap.fetched_at > last_seen)
    ):
        p = snap.price_usd
        out["last_price"] = p
        out["last_seen_at"] = snap.fetched_at.isoformat()
        out["peak_price"] = p if cur["peak_price"] is None else max(cur["peak_price"], p)
        out["trough_price"] = p if cur["trough_price"] is None else min(cur["trough_price"], p)
        if cur["price0"] is None:
            out["price0"] = p
            out["mcap0"] = snap.market_cap_usd
            out["liq0"] = snap.liquidity_usd
            if cur["symbol"] is None:
                out["symbol"] = snap.symbol
        cur.update(out)
        last_seen = snap.fetched_at
        liq_now = snap.liquidity_usd

    if cur["last_price"] is not None:
        for col, delta in CHECKPOINTS:
            if cur[col] is None and now >= alerted + delta:
                out[col] = cur["last_price"]
                cur[col] = cur["last_price"]

    rugged = False
    if cur["price0"]:
        if liq_now is not None and cur["liq0"] and liq_now <= cur["liq0"] * RUG_LIQ_FRAC:
            rugged = True
        if cur["last_price"] is not None and cur["last_price"] <= cur["price0"] * RUG_PRICE_FRAC:
            rugged = True
        if last_seen is not None and newest_ts is not None and newest_ts - last_seen >= STALE:
            rugged = True

    if rugged:
        out["rugged"] = 1
        out["closed"] = 1
        for col, _ in CHECKPOINTS:   # a rug counts as a loss at every later checkpoint
            if cur[col] is None:
                out[col] = cur["last_price"]
    elif now >= alerted + WINDOW:
        out["closed"] = 1
    return out


def update(store: Store, now: datetime) -> int:
    """Advance every open outcome row. Returns how many closed this call."""
    try:
        rows = store.open_outcomes()
        newest = store.newest_history_ts()
    except Exception:
        _log.exception("outcomes: could not load open rows")
        return 0

    closed = 0
    still_open = []
    for row in rows:
        try:
            fields = advance(row, store.latest_snapshot(row["address"]), now, newest)
            store.update_outcome(row["id"], fields)
            if fields.get("closed"):
                closed += 1
            else:
                still_open.append(row["address"])
        except Exception:
            _log.exception("outcomes: update failed for row %s", row.get("id"))

    try:
        # Keeps alerted tokens out of store.evict for the whole window.
        store.touch_activity(set(still_open), now)
    except Exception:
        _log.exception("outcomes: touch_activity failed")
    if closed:
        _log.info("outcomes: %d closed, %d still open", closed, len(still_open))
    return closed
```

- [ ] **Step 4: Wire into `run.cycle`**

Add `outcomes` to the `from memescreen.scanner import (...)` list (alphabetical, after `momentum`).
Directly after `poller.poll_once(store, now)`:

```python
        outcomes.update(store, now)
```

In the `if passed:` branch, inside `if sent:` after `candidates.record_hit(...)`:

```python
                    outcomes.record(store, address=hit.address, kind="spike", now=now,
                                    snap=snap, risk=ctx.risk, age_min=ctx.age_min)
```

In the `else:` (reject) branch, after `alerter.send_reject(hit, ctx, reason, now)`:

```python
                outcomes.record(store, address=hit.address, kind="spike_reject", now=now,
                                snap=snap, reason=reason, risk=ctx.risk, age_min=ctx.age_min)
```

Change `walletwatch.scan(now)` to `walletwatch.scan(now, store)`.

- [ ] **Step 5: Wire into `momentum.scan`**

Add `outcomes` to its scanner import: `from memescreen.scanner import alerter, candidates, filters, outcomes`.
In the insider-drop branch from Task 3, before `continue`:

```python
            outcomes.record(store, address=snap.address, kind="pump_reject", now=now,
                            snap=snap, reason=drop, risk=risk)
```

In the confirmed-send block, after `candidates.record_hit(snap.address, "pump", now)`:

```python
            outcomes.record(store, address=snap.address, kind="pump", now=now,
                            snap=snap, risk=risk, age_min=age_min)
```

- [ ] **Step 6: Wire into `walletwatch.scan`**

Change the imports and signature (no import cycle: `outcomes` imports only `models` and `store`):

```python
from memescreen.scanner import alerter, outcomes
from memescreen.scanner.store import Store
...
def scan(now: datetime, store: Store | None = None) -> int:
```

Update the docstring to add: "With a `store`, each confirmed buy also opens a `wallet` outcome row."
Replace the send loop body:

```python
                for buy in reversed(buys):
                    if alerter.send_wallet_buy(wallet, buy):
                        sent += 1
                        if store is not None:
                            outcomes.record(store, address=buy.mint, kind="wallet", now=now,
                                            snap=store.latest_snapshot(buy.mint),
                                            reason=wallet["label"] or wallet["address"][:8])
```

(`wallet` rows from `list_watched()` have `label` and `address` keys, as `handler.py` uses them.)

- [ ] **Step 7: Run tests**

Run: `py -m pytest tests/scanner -v` → all PASS. `py -m pytest -q` → all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/memescreen/scanner/outcomes.py src/memescreen/scanner/run.py src/memescreen/scanner/momentum.py src/memescreen/scanner/walletwatch.py tests/scanner/test_outcomes.py tests/scanner/test_run.py tests/scanner/test_momentum.py tests/scanner/test_walletwatch.py
git -c user.name="memescreen" -c user.email="noreply@localhost" commit -m "feat(scanner): record and track alert outcomes for 6h

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Summaries + `!results` text

**Files:**
- Modify: `src/memescreen/scanner/outcomes.py`
- Test: `tests/scanner/test_outcomes.py`

**Interfaces:**
- Consumes: row dicts from `Store.outcomes_since` (Task 4).
- Produces:
  - `@dataclass KindStats(n: int, no_price: int, ret_15m: float | None, ret_1h: float | None, ret_6h: float | None, pct_peak_50: float | None, pct_down_50: float | None, age_min0: float | None, change_h1_0: float | None)`
  - `summarize(rows: list[dict]) -> KindStats`
  - `summarize_by_kind(rows) -> dict[str, KindStats]` (only kinds present, in `KINDS` order)
  - `insider_bucket(pct: float | None) -> str` → `"<10%" | "10–30%" | "≥30%" | "unknown"`
  - `summarize_by_insider(rows) -> dict[str, KindStats]` (bucket order above, only present)
  - `format_results(by_kind, by_insider, days: int) -> str`
  - `results_text(db_path: str, days: int, now: datetime) -> str`

- [ ] **Step 1: Write the failing tests** (append to `tests/scanner/test_outcomes.py`)

```python
# --- summarize / format ----------------------------------------------------------

def _closed(kind="spike", p0=1.0, p15=None, p1h=None, p6h=None, peak=None,
            trough=None, rugged=0, insider=None, age=60.0, ch=30.0) -> dict:
    return dict(kind=kind, price0=p0, price_15m=p15, price_1h=p1h, price_6h=p6h,
                peak_price=peak, trough_price=trough, rugged=rugged,
                insider_pct=insider, age_min0=age, change_h1_0=ch)


def test_summarize_medians_and_shares():
    rows = [
        _closed(p15=1.1, p1h=1.2, p6h=2.0, peak=2.5, trough=0.9),   # winner
        _closed(p15=0.9, p1h=0.5, p6h=0.2, peak=1.0, trough=0.2),   # loser
        _closed(p15=1.0, p1h=None, p6h=None, peak=1.0, trough=1.0, age=30.0),  # still open
    ]
    st = outcomes.summarize(rows)
    assert st.n == 3 and st.no_price == 0
    assert round(st.ret_15m) == 0          # median of +10, -10, 0
    assert round(st.ret_1h) == -15         # median of +20, -50
    assert round(st.ret_6h) == 10          # median of +100, -80
    assert round(st.pct_peak_50) == 33
    assert round(st.pct_down_50) == 33
    assert st.age_min0 == 60.0


def test_summarize_counts_no_price_rows():
    st = outcomes.summarize([_closed(p0=None), _closed(p15=1.5, peak=1.5, trough=1.0)])
    assert st.n == 1 and st.no_price == 1
    assert round(st.ret_15m) == 50


def test_summarize_rugged_counts_as_down():
    st = outcomes.summarize([_closed(p15=0.95, peak=1.0, trough=0.95, rugged=1)])
    assert st.pct_down_50 == 100.0


def test_insider_buckets():
    assert outcomes.insider_bucket(None) == "unknown"
    assert outcomes.insider_bucket(9.99) == "<10%"
    assert outcomes.insider_bucket(10.0) == "10–30%"
    assert outcomes.insider_bucket(30.0) == "≥30%"


def test_format_results_empty():
    text = outcomes.format_results({}, {}, 7)
    assert "no alerts recorded in the last 7d" in text


def test_format_results_all_kinds_fit_discord():
    rows = []
    for kind in outcomes.KINDS:
        for i in range(30):
            rows.append(_closed(kind=kind, p15=1.1, p1h=0.9, p6h=0.5, peak=1.8,
                                trough=0.4, insider=(i * 3.0) if i % 4 else None))
    text = outcomes.format_results(outcomes.summarize_by_kind(rows),
                                   outcomes.summarize_by_insider(rows), 30)
    assert len(text) < 1900
    for label in ("VOLUME SPIKE", "spike rejects", "NEW PUMP", "pump rejects",
                  "SMART WALLET", "by insider share"):
        assert label in text


def test_results_text_reads_the_db(tmp_path):
    db = str(tmp_path / "m.db")
    with Store(db) as s:
        outcomes.record(s, address="tok", kind="spike", now=T0, snap=_snap())
    text = outcomes.results_text(db, 7, T0 + timedelta(hours=1))
    assert "VOLUME SPIKE" in text and "n=1" in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -m pytest tests/scanner/test_outcomes.py -v -k "summarize or bucket or format or results_text"`
Expected: FAIL with `AttributeError: module ... has no attribute 'summarize'`.

- [ ] **Step 3: Implement** — add to `outcomes.py` (add `import statistics` and `from dataclasses import dataclass` to the imports):

```python
# --- reporting (spec §4.4) ----------------------------------------------------

_LABELS = {
    "spike": "VOLUME SPIKE",
    "spike_reject": "spike rejects (gate said no)",
    "pump": "NEW PUMP",
    "pump_reject": "pump rejects (insider/dev)",
    "wallet": "SMART WALLET BUY",
}
_BUCKETS = ("<10%", "10–30%", "≥30%", "unknown")


@dataclass
class KindStats:
    n: int
    no_price: int
    ret_15m: float | None
    ret_1h: float | None
    ret_6h: float | None
    pct_peak_50: float | None
    pct_down_50: float | None
    age_min0: float | None
    change_h1_0: float | None


def _median(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _ret(price: float | None, p0: float) -> float | None:
    return None if price is None else (price / p0 - 1.0) * 100.0


def summarize(rows: list[dict]) -> KindStats:
    priced = [r for r in rows if r["price0"]]
    n = len(priced)

    def share(pred) -> float | None:
        return 100.0 * sum(1 for r in priced if pred(r)) / n if n else None

    return KindStats(
        n=n,
        no_price=len(rows) - n,
        ret_15m=_median(_ret(r["price_15m"], r["price0"]) for r in priced),
        ret_1h=_median(_ret(r["price_1h"], r["price0"]) for r in priced),
        ret_6h=_median(_ret(r["price_6h"], r["price0"]) for r in priced),
        pct_peak_50=share(lambda r: r["peak_price"] is not None
                          and r["peak_price"] >= 1.5 * r["price0"]),
        pct_down_50=share(lambda r: bool(r["rugged"]) or (
            r["trough_price"] is not None and r["trough_price"] <= 0.5 * r["price0"])),
        age_min0=_median(r["age_min0"] for r in priced),
        change_h1_0=_median(r["change_h1_0"] for r in priced),
    )


def summarize_by_kind(rows: list[dict]) -> dict[str, KindStats]:
    return {k: summarize([r for r in rows if r["kind"] == k])
            for k in KINDS if any(r["kind"] == k for r in rows)}


def insider_bucket(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    if pct < 10.0:
        return "<10%"
    if pct < 30.0:
        return "10–30%"
    return "≥30%"


def summarize_by_insider(rows: list[dict]) -> dict[str, KindStats]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(insider_bucket(r["insider_pct"]), []).append(r)
    return {b: summarize(groups[b]) for b in _BUCKETS if b in groups}


def _p(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.0f}%"


def _s(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0f}%"


def _hm(minutes: float | None) -> str:
    if minutes is None:
        return "n/a"
    m = int(minutes)
    return f"{m // 60}h {m % 60:02d}m"


def format_results(
    by_kind: dict[str, KindStats], by_insider: dict[str, KindStats], days: int,
) -> str:
    if not by_kind:
        return (f"no alerts recorded in the last {days}d yet — outcome tracking "
                "fills in as alerts fire.")
    lines = [f"results — last {days}d  (median price change after the alert; "
             "open alerts count with the checkpoints they have so far)", ""]
    for kind, st in by_kind.items():
        extra = f"  ({st.no_price} never priced)" if st.no_price else ""
        lines += [
            f"{_LABELS[kind]}   n={st.n}{extra}",
            f"  15m {_p(st.ret_15m)} · 1h {_p(st.ret_1h)} · 6h {_p(st.ret_6h)}",
            f"  peaked ≥+50%: {_s(st.pct_peak_50)} · fell ≥50%/rugged: {_s(st.pct_down_50)}",
            f"  at alert: age {_hm(st.age_min0)} · already {_p(st.change_h1_0)} 1h",
        ]
    if by_insider:
        lines += ["", "by insider share (all kinds):"]
        for bucket, st in by_insider.items():
            lines.append(
                f"  {bucket:<8} n={st.n:<4} 6h {_p(st.ret_6h):>5} · "
                f"≥+50% {_s(st.pct_peak_50):>4} · −50%/rug {_s(st.pct_down_50):>4}"
            )
    return "\n".join(lines)


def results_text(db_path: str, days: int, now: datetime) -> str:
    """What `!results` posts. Opens its own Store (the bot runs on another
    thread than the scanner, and sqlite connections are per-thread)."""
    with Store(db_path) as s:
        rows = s.outcomes_since(now - timedelta(days=days))
    return format_results(summarize_by_kind(rows), summarize_by_insider(rows), days)
```

- [ ] **Step 4: Run tests**

Run: `py -m pytest tests/scanner/test_outcomes.py -v` → all PASS. `py -m pytest -q` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/memescreen/scanner/outcomes.py tests/scanner/test_outcomes.py
git -c user.name="memescreen" -c user.email="noreply@localhost" commit -m "feat(outcomes): per-kind and per-insider-bucket result summaries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `!results [days]` in the journal bot

**Files:**
- Modify: `src/memescreen/journal/messages.py`, `src/memescreen/journal/handler.py`, `src/memescreen/journal/bot.py`
- Test: `tests/journal/test_messages.py`, `tests/journal/test_handler.py`

**Interfaces:**
- Consumes: `outcomes.results_text(db_path, days, now)` (Task 6).
- Produces: `Command.days: int | None`; `handle(..., results: Callable[[int], str] | None = None)`; `bot._amain(token, with_results: bool = False)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/journal/test_messages.py`:

```python
def test_parse_results_command():
    from memescreen.journal.messages import parse_command
    assert parse_command("!results").kind == "results"
    assert parse_command("!results").days == 7
    assert parse_command("!results 3").days == 3
    assert parse_command("!RESULTS 99").days == 30     # clamped
    assert parse_command("!results 0").days == 1       # clamped
    assert parse_command("!results soon").days == 7    # junk -> default
    assert parse_command("!resultsx") is None
```

Append to `tests/journal/test_handler.py`:

```python
def test_results_command_uses_injected_fn():
    asked = []
    with JournalStore() as s:
        r = handle(content="!results 3", replied_to=None, author_id=AUTHOR,
                   store=s, now=T0, results=lambda d: asked.append(d) or f"R{d}")
    assert r.text == "R3" and asked == [3]


def test_results_command_without_scanner():
    with JournalStore() as s:
        r = _h(s, "!results")
    assert "scanner" in r.text


def test_results_command_errors_are_friendly():
    def boom(days):
        raise RuntimeError("db locked")
    with JournalStore() as s:
        r = handle(content="!results", replied_to=None, author_id=AUTHOR,
                   store=s, now=T0, results=boom)
    assert "couldn't load results" in r.text


def test_help_mentions_results():
    with JournalStore() as s:
        assert "!results" in _h(s, "!help").text
```

- [ ] **Step 2: Run to verify they fail**

Run: `py -m pytest tests/journal -v -k results`
Expected: FAIL (`AttributeError: ... has no attribute 'kind'` on None / `handle() got an unexpected keyword argument 'results'`).

- [ ] **Step 3: Parse it** — in `messages.py`:

Add a field to `Command` (after `address`) and extend the kind comment with `'results'`:

```python
    days: int | None = None
```

In `parse_command`, after the `!candidates` block:

```python
    if low == "!results" or low.startswith("!results "):
        parts2 = low.split()
        days = 7
        if len(parts2) >= 2 and parts2[1].isdigit():
            days = max(1, min(30, int(parts2[1])))
        return Command("results", days=days)
```

- [ ] **Step 4: Handle it** — in `handler.py`:

Add `import logging` and `from typing import Callable` to the imports, and `_log = logging.getLogger("memescreen.journal")` below them. Append to `_HELP`, before its closing parenthesis:

```python
    "  !results [days]         how past alerts played out (default 7d)\n"
```

Add the parameter to `handle`:

```python
    now: datetime,
    results: Callable[[int], str] | None = None,
) -> Reply | None:
```

After the `candidates` block:

```python
    if cmd.kind == "results":
        if results is None:
            return Reply("results need the scanner running in the same process.")
        try:
            return Reply(results(cmd.days or 7))
        except Exception:
            _log.exception("!results failed")
            return Reply("couldn't load results (see logs).")
```

- [ ] **Step 5: Inject it in `bot.py`**

Change `async def _amain(token: str) -> None:` to `async def _amain(token: str, with_results: bool = False) -> None:` and, right after `allow = config.JOURNAL_CHANNEL_IDS`, add:

```python
    results_fn = None
    if with_results:
        from memescreen.scanner import outcomes

        def results_fn(days: int) -> str:
            return outcomes.results_text(config.DB_PATH, days, datetime.now(UTC))
```

Pass it into the `handle(...)` call: add `results=results_fn,` after `now=datetime.now(UTC),`.

In `_run_forever` (the co-hosted thread target), change `asyncio.run(_amain(token))` to `asyncio.run(_amain(token, with_results=True))`. Leave `run()` (standalone `memescreen journal`) as is.

- [ ] **Step 6: Run tests**

Run: `py -m pytest tests/journal -v` → all PASS. `py -m pytest -q` → all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/memescreen/journal/messages.py src/memescreen/journal/handler.py src/memescreen/journal/bot.py tests/journal/test_messages.py tests/journal/test_handler.py
git -c user.name="memescreen" -c user.email="noreply@localhost" commit -m "feat(journal): !results command for alert outcomes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Verify, merge, deploy, confirm live

**Files:** none new (README touch only if it lists bot commands).

- [ ] **Step 1: Full suite + live RugCheck sanity check**

Run: `py -m pytest -q` → expect `0 failed`, ~380 passed.

Then confirm the insider fields exist for a **brand-new** launch (the spec only verified BONK). Fetch one fresh pump.fun mint and map it:

```bash
py -c "from memescreen.providers import pumpfun, rugcheck; m=sorted(pumpfun.fetch_new_launches())[0]; r=rugcheck.fetch_risk_data(m); print(m, r.insider_pct, r.insider_network_size, r.creator_pct, r.total_holders)"
```

Expected: prints a mint and four values. `None` for some fields is acceptable (that's why the gate is fail-open), but note in the report how many came back None. If `fetch_new_launches` has a different name or return type, check `src/memescreen/providers/pumpfun.py` and adapt the one-liner. This is a read-only check.

- [ ] **Step 2: README** — if `README.md` documents bot commands, add the `!results [days]` line and the `INSIDER_MAX_PCT` / `CREATOR_MAX_PCT` env vars next to the existing ones. Commit if changed:

```bash
git add README.md
git -c user.name="memescreen" -c user.email="noreply@localhost" commit -m "docs: !results and insider gate settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 3: Whole-branch review** before merging (superpowers:requesting-code-review), and fix anything it confirms.

- [ ] **Step 4: Merge and deploy**

```bash
git checkout main
git merge --no-ff feature/outcomes-insider-gate -m "Merge: outcome tracking + insider/bundle gate"
git push origin main
```

Railway auto-deploys on push. Never use `railway redeploy`.

- [ ] **Step 5: Confirm live** (needs `railway login`, which is done as of 2026-10-21)

```bash
MSYS_NO_PATHCONV=1 railway status
MSYS_NO_PATHCONV=1 railway logs | tail -40
```

Expected: service Online on the new deployment; cycles logging `poll:` / `momentum:` lines with no `cycle failed` tracebacks. Within an hour or so, `outcomes:` lines once rows close, and any alert in Discord shows the `🧩` line when RugCheck has data. Then ask the user to type `!results 1` in Discord. It should answer (possibly "no alerts recorded… yet").
