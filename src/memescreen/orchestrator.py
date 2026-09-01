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

    def call(fetch, source: str):
        """Run one provider. Any failure is recorded, never raised: a partial
        report is the product (spec §3.2), and a malformed-but-2xx payload can
        raise anything the mapping code happens to hit."""
        try:
            return fetch(address)
        except ProviderError as exc:
            errors[exc.source] = exc.reason
        except Exception as exc:  # malformed 2xx payload / unexpected shape
            errors[source] = f"unexpected {type(exc).__name__} mapping response"
        return None

    market = call(dexscreener.fetch_market_data, "dexscreener")
    risk = call(rugcheck.fetch_risk_data, "rugcheck")
    holders = call(birdeye.fetch_holder_data, "birdeye")

    if market is not None and (market.name is None or market.symbol is None):
        fallback = call(helius.fetch_metadata_fallback, "helius")
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
