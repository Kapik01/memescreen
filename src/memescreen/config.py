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
DISCORD_WEBHOOK_URL: str | None = _opt_str("DISCORD_WEBHOOK_URL")

# --- data directory (Railway: set DATA_DIR=/data and mount a volume there) ---
DATA_DIR: str = env("DATA_DIR", "data", str)

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

# --- scanner: detection (spec §5) ---
SPIKE_RATIO_MIN: float = env("SPIKE_RATIO_MIN", 8.0, float)
ACCEL_MIN: float = env("ACCEL_MIN", 3.0, float)
BUY_RATIO_MIN: float = env("BUY_RATIO_MIN", 0.6, float)
MIN_BUYS: int = env("MIN_BUYS", 15, int)
VOL_M5_FLOOR: float = env("VOL_M5_FLOOR", 3_000.0, float)
FRESH_WINDOW_MIN: float = env("FRESH_WINDOW_MIN", 90.0, float)
BASELINE_WINDOW_MIN: float = env("BASELINE_WINDOW_MIN", 45.0, float)
BASELINE_EXCLUDE_MIN: float = env("BASELINE_EXCLUDE_MIN", 10.0, float)
LIQ_REF_MIN: float = env("LIQ_REF_MIN", 30.0, float)
LIQ_REF_TOL_MIN: float = env("LIQ_REF_TOL_MIN", 10.0, float)
LIQ_HOLD_MIN: float = env("LIQ_HOLD_MIN", 0.9, float)
DETECT_MIN_HISTORY_MIN: float = env("DETECT_MIN_HISTORY_MIN", 45.0, float)

# --- scanner: strict filter gate (spec §6) ---
LP_LOCKED_MIN: float = env("LP_LOCKED_MIN", 90.0, float)
MIN_LIQUIDITY: float = env("MIN_LIQUIDITY", 20_000.0, float)
MIN_AGE_MIN: float = env("MIN_AGE_MIN", 60.0, float)
HOLDER_GROWTH_MIN: float = env("HOLDER_GROWTH_MIN", 0.05, float)
HOLDER_GROWTH_ABS: int = env("HOLDER_GROWTH_ABS", 25, int)

# --- scanner: ops (spec §10, §12) ---
POLL_INTERVAL_SEC: int = env("POLL_INTERVAL_SEC", 60, int)
SEED_INTERVAL_SEC: int = env("SEED_INTERVAL_SEC", 180, int)
UNIVERSE_CAP: int = env("UNIVERSE_CAP", 1500, int)
ALERT_COOLDOWN_H: float = env("ALERT_COOLDOWN_H", 6.0, float)
HISTORY_RETENTION_H: float = env("HISTORY_RETENTION_H", 3.0, float)
RISK_CACHE_TTL_H: float = env("RISK_CACHE_TTL_H", 6.0, float)
BATCH_SIZE: int = env("BATCH_SIZE", 30, int)

# Paths default to <DATA_DIR>/... but each stays individually overridable.
# Forward slashes on purpose -- SQLite and open() accept them on every platform
# and Railway (the deploy target) is Linux.
_DATA_DIR = DATA_DIR.rstrip("/\\")
DB_PATH: str = env("DB_PATH", f"{_DATA_DIR}/memescreen.db", str)
LOG_PATH: str = env("LOG_PATH", f"{_DATA_DIR}/memescreen.log", str)
MISSED_LOG_PATH: str = env("MISSED_LOG_PATH", f"{_DATA_DIR}/missed_alerts.log", str)
