"""All tunable configuration. Values come from module defaults, overridable
via environment variables (and a .env file loaded at import time)."""
from __future__ import annotations

import os
from typing import Callable, TypeVar

from dotenv import load_dotenv

load_dotenv()

T = TypeVar("T")


def env(key: str, default: T, cast: Callable[[str], T]) -> T:
    """Return os.environ[key] passed through `cast`, or `default` if unset/blank."""
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return cast(raw.strip())


def _opt_str(key: str) -> str | None:
    raw = os.environ.get(key)
    return raw.strip() if raw and raw.strip() else None


# --- secrets (never logged) ---
BIRDEYE_API_KEY: str | None = _opt_str("BIRDEYE_API_KEY")
HELIUS_API_KEY: str | None = _opt_str("HELIUS_API_KEY")
TELEGRAM_BOT_TOKEN: str | None = _opt_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str | None = _opt_str("TELEGRAM_CHAT_ID")

# --- API bases ---
DEXSCREENER_BASE: str = env("DEXSCREENER_BASE", "https://api.dexscreener.com", str)
RUGCHECK_BASE: str = env("RUGCHECK_BASE", "https://api.rugcheck.xyz", str)
BIRDEYE_BASE: str = env("BIRDEYE_BASE", "https://public-api.birdeye.so", str)
HELIUS_RPC: str | None = (
    f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    if HELIUS_API_KEY
    else None
)

# --- HTTP ---
HTTP_TIMEOUT_SEC: float = env("HTTP_TIMEOUT_SEC", 10.0, float)
HTTP_RETRIES: int = env("HTTP_RETRIES", 1, int)

# --- deep-dive flag thresholds ---
FLAG_LIQ_RED: float = env("FLAG_LIQ_RED", 10_000.0, float)
FLAG_LIQ_GREEN: float = env("FLAG_LIQ_GREEN", 30_000.0, float)
FLAG_LP_LOCKED_RED: float = env("FLAG_LP_LOCKED_RED", 50.0, float)
FLAG_LP_LOCKED_GREEN: float = env("FLAG_LP_LOCKED_GREEN", 90.0, float)
FLAG_TOP10_RED: float = env("FLAG_TOP10_RED", 60.0, float)
FLAG_TOP10_YELLOW: float = env("FLAG_TOP10_YELLOW", 35.0, float)
FLAG_AGE_RED_MIN: float = env("FLAG_AGE_RED_MIN", 30.0, float)
FLAG_AGE_GREEN_HR: float = env("FLAG_AGE_GREEN_HR", 6.0, float)
FLAG_VOL_LIQ_HIGH: float = env("FLAG_VOL_LIQ_HIGH", 20.0, float)
FLAG_VOL_LIQ_LOW: float = env("FLAG_VOL_LIQ_LOW", 0.05, float)

# --- deep-dive scoring ---
SCORE_RED_WEIGHT: int = env("SCORE_RED_WEIGHT", 25, int)
SCORE_YELLOW_WEIGHT: int = env("SCORE_YELLOW_WEIGHT", 8, int)
SCORE_UNKNOWN_WEIGHT: int = env("SCORE_UNKNOWN_WEIGHT", 5, int)
VERDICT_AVOID_MAX: int = env("VERDICT_AVOID_MAX", 40, int)
VERDICT_TRADE_MIN: int = env("VERDICT_TRADE_MIN", 70, int)

RUGCHECK_DANGER_MAX: float = env("RUGCHECK_DANGER_MAX", 60.0, float)
