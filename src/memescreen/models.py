"""Normalized data structures shared by providers, orchestrator, rules, and
(in Plan 2) the scanner. Every provider maps its raw API payload onto one of
these so downstream code never touches vendor JSON shapes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

FlagLevel = str  # "red" | "yellow" | "green" | "unknown"


@dataclass
class HolderShare:
    address: str
    pct: float


@dataclass
class MarketData:
    name: str | None
    symbol: str | None
    price_usd: float | None
    liquidity_usd: float | None
    volume_24h_usd: float | None
    market_cap_usd: float | None
    pair_created_at: datetime | None
    dex: str | None
    socials: list[str] = field(default_factory=list)
    source: str = "dexscreener"


@dataclass
class RiskData:
    rugcheck_score: int | None
    mint_authority_enabled: bool | None
    freeze_authority_enabled: bool | None
    lp_locked_pct: float | None
    top_holders: list[HolderShare] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    # Subset of risk_flags whose RugCheck entry carried level == "danger".
    danger_flags: list[str] = field(default_factory=list)
    source: str = "rugcheck"


@dataclass
class HolderData:
    holder_count: int | None
    top10_pct: float | None
    top20_pct: float | None
    creator_pct: float | None
    source: str = "birdeye"


@dataclass
class Flag:
    level: FlagLevel
    check: str
    message: str


@dataclass
class TokenReport:
    address: str
    fetched_at: datetime
    market: MarketData | None
    risk: RiskData | None
    holders: HolderData | None
    provider_errors: dict[str, str]
    flags: list[Flag] = field(default_factory=list)
    score: int | None = None
    verdict: str | None = None


@dataclass
class MarketSnapshot:
    """One poll-cycle reading for one token. Feeds `token_history`."""
    address: str
    fetched_at: datetime
    symbol: str | None = None
    price_usd: float | None = None
    liquidity_usd: float | None = None
    market_cap_usd: float | None = None
    vol_m5: float | None = None
    vol_h1: float | None = None
    vol_h6: float | None = None
    vol_h24: float | None = None
    buys_m5: int | None = None
    sells_m5: int | None = None
    pair_created_at: datetime | None = None
    dex: str | None = None


@dataclass
class AnomalyHit:
    address: str
    detected_at: datetime
    spike_ratio: float
    accel: float
    buy_ratio: float
    buys_m5: int
    vol_m5: float
    vol_h1: float
    liq_now: float
    liq_change: float
    first_seen_at: datetime
    anomaly_score: int


@dataclass
class CreatorStats:
    """pump.fun deployer wallet + how many coins it has launched."""
    wallet: str
    coins_created: int
    capped: bool = False  # True if coins_created hit the API page limit (a floor)
