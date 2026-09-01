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
