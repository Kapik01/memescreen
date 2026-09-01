"""Assemble a TokenReport from every provider, tolerating partial failure,
then score it."""
from __future__ import annotations

from datetime import UTC, datetime

from memescreen import rules
from memescreen.models import TokenReport
from memescreen.providers import birdeye, dexscreener, helius, rugcheck
from memescreen.providers.base import ProviderError


def screen(address: str) -> TokenReport:
    errors: dict[str, str] = {}

    try:
        market = dexscreener.fetch_market_data(address)
    except ProviderError as exc:
        market, errors[exc.source] = None, exc.reason

    try:
        risk = rugcheck.fetch_risk_data(address)
    except ProviderError as exc:
        risk, errors[exc.source] = None, exc.reason

    try:
        holders = birdeye.fetch_holder_data(address)
    except ProviderError as exc:
        holders, errors[exc.source] = None, exc.reason

    if market is not None and (market.name is None or market.symbol is None):
        fallback = helius.fetch_metadata_fallback(address)
        if fallback is not None:
            name, symbol = fallback
            market.name = market.name or name
            market.symbol = market.symbol or symbol

    report = TokenReport(
        address=address,
        fetched_at=datetime.now(UTC),
        market=market,
        risk=risk,
        holders=holders,
        provider_errors=errors,
    )
    rules.evaluate(report)
    return report
