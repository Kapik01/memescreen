"""Turn a gate-surviving `AnomalyHit` into a Discord alert (spec §7).

`send()` is the single entry point. It dedupes against `alerts_sent`
(`store.recent_alert`), formats a plain-text message, and delivers it by POSTing
to a Discord channel webhook. When the webhook is unconfigured or the request
fails, it falls back to a terminal print plus an appended line in `MISSED_LOG`
and does *not* write `alerts_sent` -- so an outage never both loses the signal
and suppresses a later retry.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from memescreen import config
from memescreen.models import AnomalyHit, MarketSnapshot, RiskData
from memescreen.providers import dexscreener
from memescreen.providers.base import ProviderError, request_json
from memescreen.scanner.filters import FilterContext
from memescreen.scanner.store import Store

MISSED_LOG = config.MISSED_LOG_PATH

# Discord truncates `content` at 2000 chars; leave headroom for the code fence.
_CONTENT_MAX = 1900

# Per-process dedup for the (informational) rejects feed: {address: last posted}.
# Cleared on restart, which is fine -- a missed reject is nothing.
_reject_seen: dict[str, datetime] = {}


def discord_configured() -> bool:
    """True only when the main Discord webhook URL is set (read at call time)."""
    return bool(config.DISCORD_WEBHOOK_URL)


def rejects_configured() -> bool:
    """True when the separate rejects-channel webhook is set."""
    return bool(config.DISCORD_REJECTS_WEBHOOK_URL)


def pump_configured() -> bool:
    """True when the separate NEW PUMP channel webhook is set."""
    return bool(config.DISCORD_PUMP_WEBHOOK_URL)


def _digest_url() -> str | None:
    return config.DISCORD_DIGEST_WEBHOOK_URL or config.DISCORD_PUMP_WEBHOOK_URL


def digest_configured() -> bool:
    """True when the BOARD digest has somewhere to post (its own webhook, or
    the pump webhook it falls back to)."""
    return bool(_digest_url())


def send_digest(content: str) -> bool:
    """Post the periodic board digest. True only on a confirmed send."""
    url = _digest_url()
    if not url:
        return False
    return _post_discord(url, content)


# --- formatting helpers -------------------------------------------------


def _money(v: float | None) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"${v / 1_000:.1f}k"
    return f"${v:.0f}"


def _age(age_min: float | None) -> str:
    if age_min is None:
        return "n/a"
    total = int(age_min)
    return f"{total // 60}h {total % 60}m"


def _safety(risk: RiskData | None) -> str:
    if risk is None:
        return "safety: n/a"
    if risk.lp_locked_pct is None:
        lp = "LP locked n/a"
    else:
        lp = f"LP locked {risk.lp_locked_pct:.0f}%"
    mint = "mint renounced" if risk.mint_authority_enabled is False else "mint live"
    freeze = "freeze renounced" if risk.freeze_authority_enabled is False else "freeze live"
    return f"safety: {lp} · {mint} · {freeze}"


def format_message(hit: AnomalyHit, ctx: FilterContext) -> str:
    """Plain-text alert body (spec §7 layout). No markdown."""
    symbol = ctx.symbol or hit.address[:8]
    buy_pct = hit.buy_ratio * 100
    liq_pct = (hit.liq_change - 1.0) * 100

    if ctx.holder_growth is None:
        holders_line = "holders: n/a"
    else:
        holders_line = f"Holders   +{ctx.holder_growth} since first seen"

    rug = ctx.risk.rugcheck_score if ctx.risk is not None else None

    lines = [
        f"\U0001f6a8 VOLUME SPIKE — ${symbol}  (score {hit.anomaly_score}/100)",
        "",
        f"5m vol   {_money(hit.vol_m5)}   ·   {hit.spike_ratio:.1f}x baseline",
        f"1h vol   {_money(hit.vol_h1)}   ·   {hit.accel:.1f}x a typical hour",
        f"Buys     {hit.buys_m5}   ({buy_pct:.0f}% buys)",
        f"Liquidity {_money(hit.liq_now)}   ({liq_pct:+.0f}%)",
        holders_line,
        f"Age       {_age(ctx.age_min)}   ·   {ctx.dex or '?'}",
        "",
        _safety(ctx.risk),
        # `rug or 'n/a'` would render 0 -- the SAFEST possible score -- as
        # "unavailable"; only None means unavailable.
        f"RugCheck score: {rug if rug is not None else 'n/a'}",
        *(
            [f"Deployer: {ctx.pumpfun_coins} pump.fun coins launched"]
            if ctx.pumpfun_coins is not None
            else []
        ),
        "",
        f"Contract: {hit.address}",
        f"Chart: https://dexscreener.com/solana/{hit.address}",
        f"Run: screen {hit.address}",
    ]
    return "\n".join(lines)


# --- delivery ---------------------------------------------------------


def _narrative_lines(address: str) -> list[str]:
    """Best-effort: the token's linked socials (Twitter / Telegram / site) so
    the alert answers 'why is this moving'. Any failure returns no lines --
    the alert still goes out, just without the narrative block."""
    try:
        md = dexscreener.fetch_market_data(address)
    except Exception:
        return []
    urls = [u for u in (md.socials or []) if u][:6]
    if not urls:
        return []
    return ["", "Narrative — check before entering:"] + [f"  {u}" for u in urls]


def _with_narrative(msg: str, address: str) -> str:
    extra = _narrative_lines(address)
    return msg + "\n" + "\n".join(extra) if extra else msg


def _record_missed(msg: str, now: datetime) -> None:
    os.makedirs(os.path.dirname(MISSED_LOG) or ".", exist_ok=True)
    with open(MISSED_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"--- missed {now.isoformat()} ---\n")
        fh.write(msg + "\n")


def _wait_url(url: str) -> str:
    """Append `?wait=true` so Discord answers 200 + a JSON body on success
    instead of a bodyless 204 (which `request_json` would reject)."""
    url = url or ""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}wait=true"


def _post_discord(url: str, content: str) -> bool:
    """POST one code-fenced message to a Discord webhook. True only on a
    confirmed send (200 + a body carrying an `id`)."""
    try:
        resp = request_json(
            "discord", "POST", _wait_url(url),
            json_body={"content": "```\n" + content[:_CONTENT_MAX] + "\n```"},
        )
    except ProviderError:
        return False
    return isinstance(resp, dict) and bool(resp.get("id"))


def send_reject(
    hit: AnomalyHit, ctx: FilterContext, reason: str, now: datetime
) -> bool:
    """Post a detected-but-filtered hit to the rejects channel. Deduped per
    token by `REJECT_COOLDOWN_H`. No-op (returns False) when the rejects
    webhook is unset -- this feed is purely informational."""
    if not rejects_configured():
        return False
    window = timedelta(hours=config.REJECT_COOLDOWN_H)
    # Drop entries that can no longer suppress anything (keeps the dict bounded
    # to "tokens rejected in the last cooldown window").
    for addr in [a for a, t in _reject_seen.items() if now - t >= window]:
        del _reject_seen[addr]
    last = _reject_seen.get(hit.address)
    if last is not None and now - last < window:
        return False
    # Set before the POST: a transiently-down webhook must not be re-hit every
    # 60s. A missed reject is nothing (this feed is informational).
    _reject_seen[hit.address] = now

    sym = ctx.symbol or hit.address[:8]
    line = (
        f"❌ {sym}  spike {hit.spike_ratio:.1f}x / {hit.buys_m5} buys  "
        f"·  dropped: {reason}\n"
        f"{hit.address}\n"
        f"https://dexscreener.com/solana/{hit.address}"
    )
    return _post_discord(config.DISCORD_REJECTS_WEBHOOK_URL, line)


def send_pump(
    snap: MarketSnapshot, top10_pct: float, age_min: float, now: datetime
) -> bool:
    """Post a NEW PUMP momentum alert to the rejects channel. This is a
    freshly-polled young token already running -- 1h price up, volume past
    market cap, holders not over-concentrated. No safety gate ran, so the
    line says so. Returns True only on a confirmed send; a no-op (False)
    when the NEW PUMP webhook is unset. Dedup is the caller's job
    (`store.recent_pump_alert` / `record_pump_alert`)."""
    if not pump_configured():
        return False
    sym = snap.symbol or snap.address[:8]
    pct = snap.price_change_h1 or 0.0
    vol_mcap = (
        snap.vol_h24 / snap.market_cap_usd
        if snap.vol_h24 is not None and snap.market_cap_usd
        else 0.0
    )
    line = (
        f"\U0001f4c8 NEW PUMP — ${sym}   +{pct:.0f}% 1h  ·  age {_age(age_min)}  ·  "
        f"vol/mcap {vol_mcap:.1f}x  ·  top10 {top10_pct:.0f}%  ·  liq {_money(snap.liquidity_usd)}\n"
        f"unvetted — no safety gate ran, DYOR\n"
        f"{snap.address}\n"
        f"https://dexscreener.com/solana/{snap.address}\n"
        f"Run: screen {snap.address}"
    )
    return _post_discord(config.DISCORD_PUMP_WEBHOOK_URL, _with_narrative(line, snap.address))


def send(hit: AnomalyHit, ctx: FilterContext, store: Store, now: datetime) -> bool:
    """Format and deliver the alert. Returns True only on a confirmed send."""
    if store.recent_alert(hit.address, now, config.ALERT_COOLDOWN_H):
        return False

    msg = _with_narrative(format_message(hit, ctx), hit.address)

    if not discord_configured():
        print(msg)
        _record_missed(msg, now)
        return False

    # A bad/deleted webhook (4xx), exhausted retries, or a body without an `id`
    # are all "not delivered" -> fall back, and do NOT record, so a later cycle
    # retries instead of the 6h cooldown suppressing it.
    if not _post_discord(config.DISCORD_WEBHOOK_URL, msg):
        print(msg)
        _record_missed(msg, now)
        return False

    store.record_alert(hit.address, now, hit.anomaly_score)
    return True
