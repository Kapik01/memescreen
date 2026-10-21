"""Discord glue for the trade journal. Deliberately thin: every decision
lives in `handler.handle`, which is pure and unit-tested. This module only
wires the gateway to it. `discord` is imported inside the functions so the
rest of the package (and the test suite) does not need the dependency.

Two entry points:
  * `run()`            -- block on the bot; used by `memescreen journal`.
  * `start_in_thread()` -- daemon thread; `scan` calls this when a token is
                           set, so one Railway service runs both.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime

from memescreen import config
from memescreen.journal.handler import handle
from memescreen.journal.store import JournalStore

_log = logging.getLogger("memescreen.journal")


def _replied_text(message) -> str | None:
    ref = getattr(message, "reference", None)
    if ref is None:
        return None
    resolved = getattr(ref, "resolved", None)
    if resolved is not None:
        return getattr(resolved, "content", None)
    return None  # caller falls back to an explicit fetch


async def _amain(token: str, with_results: bool = False) -> None:
    import discord

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    store = JournalStore(config.JOURNAL_DB_PATH)
    allow = config.JOURNAL_CHANNEL_IDS

    # `!results` reads the scanner's DB, so it is only offered when the bot is
    # co-hosted inside `scan` (start_in_thread), not run standalone.
    results_fn = None
    if with_results:
        from memescreen.scanner import outcomes

        def results_fn(days: int) -> str:
            return outcomes.results_text(config.DB_PATH, days, datetime.now(UTC))

    @client.event
    async def on_ready() -> None:  # pragma: no cover - needs a live gateway
        _log.info("journal bot online as %s", client.user)
        for g in client.guilds:
            chans = ", ".join(
                f"#{c.name}({c.id})" for c in getattr(g, "text_channels", [])
            )
            _log.info("journal in guild %s (%s) -- text channels: %s", g.name, g.id, chans or "none visible")
        if allow:
            _log.info("journal channel allowlist active: %s", sorted(allow))

    @client.event
    async def on_message(message) -> None:  # pragma: no cover - needs a gateway
        if message.author.bot:
            return  # ignore the webhook alerts themselves and other bots
        _log.info(
            "journal saw msg in #%s(%s) from %s: content_len=%d reply=%s",
            getattr(message.channel, "name", "?"), message.channel.id,
            message.author, len(message.content or ""),
            message.reference is not None,
        )
        if allow and message.channel.id not in allow:
            return

        replied_to = _replied_text(message)
        ref = getattr(message, "reference", None)
        if replied_to is None and ref is not None and ref.message_id is not None:
            try:
                fetched = await message.channel.fetch_message(ref.message_id)
                replied_to = fetched.content
            except Exception:
                replied_to = None

        try:
            reply = handle(
                content=message.content or "",
                replied_to=replied_to,
                author_id=str(message.author.id),
                store=store,
                now=datetime.now(UTC),
                results=results_fn,
            )
        except Exception:
            _log.exception("journal handle() failed")
            return
        if reply is not None:
            try:
                await message.reply(reply.text[:1900], mention_author=False)
            except Exception:
                _log.exception("journal could not post reply in #%s(%s)",
                               getattr(message.channel, "name", "?"), message.channel.id)

    await client.start(token)


def run() -> int:
    token = config.DISCORD_BOT_TOKEN
    if not token:
        _log.error("DISCORD_BOT_TOKEN not set -- nothing to run")
        return 2
    try:
        asyncio.run(_amain(token))
    except Exception:
        _log.exception("journal bot stopped")
        return 1
    return 0


def _run_forever(token: str) -> None:  # pragma: no cover - thread target
    try:
        asyncio.run(_amain(token, with_results=True))
    except Exception:
        _log.exception("journal bot thread crashed")


def start_in_thread() -> threading.Thread | None:
    """Start the bot on a daemon thread. No-op (returns None) without a token."""
    token = config.DISCORD_BOT_TOKEN
    if not token:
        return None
    t = threading.Thread(target=_run_forever, args=(token,), name="journal-bot", daemon=True)
    t.start()
    return t
