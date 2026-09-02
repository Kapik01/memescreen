"""DexScreener -> MarketData. No API key required. 60 req/min."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from memescreen import config
from memescreen.models import MarketData, MarketSnapshot
from memescreen.providers.base import ProviderError, request_json

_SOURCE = "dexscreener"
_log = logging.getLogger(__name__)


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


def _snapshot_from_pair(address: str, pair: dict, fetched_at: datetime) -> MarketSnapshot:
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    price_change = pair.get("priceChange") or {}
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
        symbol=(pair.get("baseToken") or {}).get("symbol"),
        price_usd=_to_float(pair.get("priceUsd")),
        liquidity_usd=_to_float(liquidity.get("usd")),
        market_cap_usd=_to_float(pair.get("marketCap")),
        vol_m5=_to_float(volume.get("m5")),
        vol_h1=_to_float(volume.get("h1")),
        vol_h6=_to_float(volume.get("h6")),
        vol_h24=_to_float(volume.get("h24")),
        buys_m5=int(txns_m5["buys"]) if isinstance(txns_m5.get("buys"), (int, float)) and not isinstance(txns_m5.get("buys"), bool) else None,
        sells_m5=int(txns_m5["sells"]) if isinstance(txns_m5.get("sells"), (int, float)) and not isinstance(txns_m5.get("sells"), bool) else None,
        pair_created_at=created,
        dex=pair.get("dexId"),
        price_change_h1=_to_float(price_change.get("h1")),
    )


def _chunks(seq: list[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_snapshots(addresses: list[str]) -> dict[str, MarketSnapshot]:
    now = datetime.now(UTC)
    out: dict[str, MarketSnapshot] = {}
    for chunk in _chunks(list(addresses), max(1, config.BATCH_SIZE)):
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
