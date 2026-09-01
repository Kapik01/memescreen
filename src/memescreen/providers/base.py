"""Shared HTTP plumbing for all providers: one client, a retry policy, and a
single exception type the orchestrator catches."""
from __future__ import annotations

import json
from typing import Any

import httpx

from memescreen import config

_client: httpx.Client | None = None


class ProviderError(Exception):
    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=config.HTTP_TIMEOUT_SEC,
            headers={"User-Agent": "memescreen/0.1"},
        )
    return _client


def request_json(
    source: str,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    json_body: dict | None = None,
) -> Any:
    client = get_client()
    attempts = config.HTTP_RETRIES + 1
    last_reason = "unknown error"
    for _ in range(attempts):
        try:
            resp = client.request(
                method, url, params=params, headers=headers, json=json_body
            )
        except httpx.TimeoutException:
            last_reason = "timeout"
            continue
        except httpx.TransportError as exc:
            last_reason = f"transport error: {exc}"
            continue

        if resp.status_code >= 500:
            last_reason = f"{resp.status_code} {resp.reason_phrase}".strip()
            continue
        if resp.status_code >= 400:
            raise ProviderError(
                source, f"{resp.status_code} {resp.reason_phrase}".strip()
            )
        try:
            return resp.json()
        except json.JSONDecodeError:
            raise ProviderError(source, "response body was not valid JSON") from None

    raise ProviderError(source, last_reason)
