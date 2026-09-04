"""Pure parsing helpers: pull a token out of an alert message, and turn the
user's reply text into a `Command`. No Discord types here so every branch is
unit-testable."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Base58, Solana mint length. `\b` keeps it from matching inside a longer blob.
_ADDR = r"[1-9A-HJ-NP-Za-km-z]{32,44}"
_CONTRACT_RE = re.compile(rf"Contract:\s*({_ADDR})")
_DEXLINK_RE = re.compile(rf"dexscreener\.com/solana/({_ADDR})")
_BARE_ADDR_RE = re.compile(rf"\b{_ADDR}\b")
_ADDR_FULL_RE = re.compile(rf"^{_ADDR}$")
_SYMBOL_RE = re.compile(r"\$([A-Za-z0-9_]{2,15})")


def extract_address(text: str | None) -> str | None:
    """The token mint from an alert message. Prefers the explicit `Contract:`
    line, then the dexscreener link, then any base58 blob of the right shape."""
    if not text:
        return None
    for rx in (_CONTRACT_RE, _DEXLINK_RE, _BARE_ADDR_RE):
        m = rx.search(text)
        if m:
            return m.group(1) if m.groups() else m.group(0)
    return None


def alert_kind(text: str | None) -> str | None:
    """'pump' for a NEW PUMP alert, 'spike' for a VOLUME SPIKE alert, else None
    (e.g. the user replied to a reject-feed line or something unrelated)."""
    if not text:
        return None
    if "NEW PUMP" in text or "\U0001f4c8" in text:
        return "pump"
    if "VOLUME SPIKE" in text or "\U0001f6a8" in text:
        return "spike"
    return None


def extract_symbol(text: str | None) -> str | None:
    if not text:
        return None
    m = _SYMBOL_RE.search(text)
    return m.group(1).upper() if m else None


def _num(token: str) -> float | None:
    """Lenient price/size parse: strips a leading $ and thousands commas,
    accepts scientific notation (`1.2e-7`)."""
    cleaned = token.strip().lstrip("$").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


@dataclass
class Command:
    kind: str  # 'in' | 'out' | 'note' | 'stats' | 'open' | 'help' |
               # 'watch' | 'unwatch' | 'wallets' | 'watch_help'
    price: float | None = None
    size: float | None = None
    note: str | None = None
    address: str | None = None


def parse_command(text: str) -> Command | None:
    """The user's reply text -> a Command, or None when it is not for us."""
    t = (text or "").strip()
    low = t.lower()
    if low in ("!stats", "!stat"):
        return Command("stats")
    if low in ("!open", "!positions"):
        return Command("open")
    if low in ("!help", "!journal"):
        return Command("help")
    if low in ("!wallets", "!watched"):
        return Command("wallets")

    if low.startswith("!watch"):
        parts2 = t.split(maxsplit=2)
        if len(parts2) < 2 or not _ADDR_FULL_RE.match(parts2[1]):
            return Command("watch_help")
        label = parts2[2] if len(parts2) >= 3 else None
        return Command("watch", address=parts2[1], note=label)

    if low.startswith("!unwatch"):
        parts2 = t.split(maxsplit=1)
        if len(parts2) < 2 or not _ADDR_FULL_RE.match(parts2[1]):
            return Command("watch_help")
        return Command("unwatch", address=parts2[1])

    parts = t.split()
    if not parts:
        return None
    verb = parts[0].lower()

    if verb in ("in", "buy") and len(parts) >= 2:
        price = _num(parts[1])
        if price is None or price <= 0:
            return None
        size = _num(parts[2]) if len(parts) >= 3 else None
        if size is not None and size <= 0:
            size = None
        return Command("in", price=price, size=size)

    if verb in ("out", "sell") and len(parts) >= 2:
        price = _num(parts[1])
        if price is None or price <= 0:
            return None
        return Command("out", price=price)

    if verb == "note" and len(parts) >= 2:
        return Command("note", note=t[len(parts[0]):].strip())

    return None
