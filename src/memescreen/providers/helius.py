"""Helius getAsset -> (name, symbol). Best-effort metadata fallback only used
when DexScreener returned no name/symbol. Never raises.

Also: the Enhanced Transactions API for wallet-buy watching (`fetch_wallet_buys`).
"""
from __future__ import annotations

from datetime import UTC, datetime

from memescreen import config
from memescreen.models import TokenBuy
from memescreen.providers.base import ProviderError, request_json

_SOURCE = "helius"

# Mints a "buy" alert should never fire on -- the wallet receiving these is
# just wrapping SOL or holding stables, not acquiring a new token.
_IGNORE_MINTS = {
    "So11111111111111111111111111111111111111112",  # wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


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


def fetch_wallet_buys(
    address: str, *, until_sig: str | None = None, limit: int | None = None
) -> list[TokenBuy]:
    """Recent SPL-token acquisitions by `address`, newest first. Stops paging
    (returns only the newer ones) once `until_sig` is reached -- pass the last
    signature already processed for this wallet. No key, no history, or any
    request failure all return an empty list (fail quiet: this is a watchlist
    feature, never a safety check)."""
    if not config.HELIUS_API_KEY:
        return []
    lim = limit if limit is not None else config.WALLET_WATCH_LOOKBACK
    try:
        txns = request_json(
            _SOURCE, "GET",
            f"{config.HELIUS_API_BASE}/v0/addresses/{address}/transactions",
            params={"api-key": config.HELIUS_API_KEY, "limit": lim},
        )
    except ProviderError:
        return []
    if not isinstance(txns, list):
        return []

    out: list[TokenBuy] = []
    for tx in txns:
        if not isinstance(tx, dict):
            continue
        sig = tx.get("signature")
        if not isinstance(sig, str) or not sig:
            continue
        if until_sig is not None and sig == until_sig:
            break
        ts = tx.get("timestamp")
        when = (
            datetime.fromtimestamp(ts, tz=UTC)
            if isinstance(ts, (int, float)) and not isinstance(ts, bool)
            else None
        )
        for xfer in tx.get("tokenTransfers") or []:
            if not isinstance(xfer, dict) or xfer.get("toUserAccount") != address:
                continue
            mint = xfer.get("mint")
            if not isinstance(mint, str) or not mint or mint in _IGNORE_MINTS:
                continue
            amount = xfer.get("tokenAmount")
            out.append(TokenBuy(
                signature=sig, mint=mint,
                amount=float(amount) if isinstance(amount, (int, float)) and not isinstance(amount, bool) else None,
                timestamp=when,
            ))
    return out
