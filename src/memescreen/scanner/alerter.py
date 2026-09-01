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
from datetime import datetime

from memescreen import config
from memescreen.models import AnomalyHit, RiskData
from memescreen.providers.base import ProviderError, request_json
from memescreen.scanner.filters import FilterContext
from memescreen.scanner.store import Store

MISSED_LOG = config.MISSED_LOG_PATH

# Discord truncates `content` at 2000 chars; leave headroom for the code fence.
_CONTENT_MAX = 1900


def discord_configured() -> bool:
    """True only when the Discord webhook URL is set (read at call time)."""
    return bool(config.DISCORD_WEBHOOK_URL)


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
        "",
        f"Contract: {hit.address}",
        f"Chart: https://dexscreener.com/solana/{hit.address}",
        f"Run: screen {hit.address}",
    ]
    return "\n".join(lines)


# --- delivery ---------------------------------------------------------


def _record_missed(msg: str, now: datetime) -> None:
    os.makedirs(os.path.dirname(MISSED_LOG) or ".", exist_ok=True)
    with open(MISSED_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"--- missed {now.isoformat()} ---\n")
        fh.write(msg + "\n")


def _webhook_url() -> str:
    """Webhook with `?wait=true` so Discord answers 200 + a JSON body on
    success instead of a bodyless 204 (which `request_json` would reject)."""
    url = config.DISCORD_WEBHOOK_URL or ""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}wait=true"


def send(hit: AnomalyHit, ctx: FilterContext, store: Store, now: datetime) -> bool:
    """Format and deliver the alert. Returns True only on a confirmed send."""
    if store.recent_alert(hit.address, now, config.ALERT_COOLDOWN_H):
        return False

    msg = format_message(hit, ctx)

    if not discord_configured():
        print(msg)
        _record_missed(msg, now)
        return False

    content = "```\n" + msg[:_CONTENT_MAX] + "\n```"
    try:
        resp = request_json(
            "discord",
            "POST",
            _webhook_url(),
            json_body={"content": content},
        )
    except ProviderError:
        # Bad/deleted webhook -> 4xx, exhausted retries -> ProviderError.
        print(msg)
        _record_missed(msg, now)
        return False

    # With `?wait=true` a success is 200 + the created-message object (a dict
    # with an "id"). Anything else -- a proxy returning a list, an empty body
    # slipping through -- is not a confirmed delivery.
    if not (isinstance(resp, dict) and resp.get("id")):
        print(msg)
        _record_missed(msg, now)
        return False

    store.record_alert(hit.address, now, hit.anomaly_score)
    return True
