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
