"""The one pure function the Discord layer calls: given the reply text, the
text of the message it replied to, and who sent it, do the journal action and
return the text to post back (or None to stay silent)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from memescreen.journal import stats
from memescreen.journal.messages import (
    Command,
    alert_kind,
    extract_address,
    extract_symbol,
    parse_command,
)
from memescreen.journal.store import JournalStore

_HELP = (
    "trade journal — reply to an alert with:\n"
    "  in <price> [usd_size]   open a position\n"
    "  out <price>             close it, records P&L\n"
    "  note <text>             attach a note\n"
    "anywhere:\n"
    "  !stats   win rate + P&L by alert type\n"
    "  !open    positions you haven't closed\n"
)


@dataclass
class Reply:
    text: str


def _short(addr: str, sym: str | None) -> str:
    return sym or addr[:8]


def handle(
    *,
    content: str,
    replied_to: str | None,
    author_id: str,
    store: JournalStore,
    now: datetime,
) -> Reply | None:
    cmd = parse_command(content)
    if cmd is None:
        return None

    if cmd.kind == "help":
        return Reply(_HELP)
    if cmd.kind == "stats":
        return Reply(stats.format_summary(
            stats.summarize(store.closed_trades(author_id)),
            open_count=len(store.open_positions(author_id)),
        ))
    if cmd.kind == "open":
        return Reply(stats.format_open(store.open_positions(author_id)))

    # in / out / note all need the alert the user replied to
    if replied_to is None:
        return Reply("reply to an alert message with `in <price>` or `out <price>`.")
    addr = extract_address(replied_to)
    if addr is None:
        return Reply("couldn't find a token address in the message you replied to.")
    sym = extract_symbol(replied_to)
    kind = alert_kind(replied_to)

    if cmd.kind == "in":
        ok, msg = store.open_trade(
            address=addr, alert_kind=kind, symbol=sym,
            entry_price=cmd.price, size_usd=cmd.size, now=now, user=author_id,
        )
        if not ok:
            return Reply(f"{_short(addr, sym)}: {msg}.")
        size = f"  (${cmd.size:g})" if cmd.size else ""
        return Reply(f"\U0001f4dd in **{_short(addr, sym)}** @ {cmd.price:g}{size}")

    if cmd.kind == "out":
        ok, msg, pnl = store.close_trade(
            address=addr, exit_price=cmd.price, now=now, user=author_id,
        )
        if not ok:
            return Reply(f"{_short(addr, sym)}: {msg}.")
        return Reply(f"\U0001f4dd out **{_short(addr, sym)}** @ {cmd.price:g}  →  {pnl:+.1f}%")

    if cmd.kind == "note":
        ok = store.add_note(address=addr, note=cmd.note or "", user=author_id)
        return Reply("noted." if ok else f"{_short(addr, sym)}: no trade on that token yet.")

    return None  # unreachable
