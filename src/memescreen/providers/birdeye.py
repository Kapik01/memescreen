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
    parsed_any = False
    for item in items[:n]:
        pct = item.get("percentage")
        if isinstance(pct, (int, float)):
            total += float(pct)
            parsed_any = True
    # Nothing parsed means we know nothing; 0.0 would read downstream as a
    # green "0% held by top 10", a safety signal we never actually saw.
    return total if parsed_any else None


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
