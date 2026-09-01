"""The always-on scan loop: seed -> poll -> detect -> filter -> alert.

`main()` runs until SIGINT. `cycle()` is one iteration, wrapped so a single
failing step is logged and swallowed rather than killing the loop. Cadence is
driven off `time.monotonic()` marks so a slow cycle never compounds drift.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import UTC, datetime

from memescreen import config
from memescreen.scanner import alerter, detector, filters, poller, universe
from memescreen.scanner.riskcache import RiskCache
from memescreen.scanner.store import Store

_LOGGER_NAME = "memescreen.scanner"
_LOG_PATH = os.path.join("data", "memescreen.log")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# Set by the SIGINT handler; checked every <=1s slice by the main loop.
_STOP = False


def build_logger() -> logging.Logger:
    """Configure and return the ``memescreen.scanner`` logger at INFO with a
    stdout stream handler and a rotating file handler under ``data/``.

    Idempotent: if the logger already has handlers it is returned untouched,
    so repeated calls (tests included) never stack duplicate handlers. The
    same logger object is returned every call.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(_LOG_FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    os.makedirs(os.path.dirname(_LOG_PATH) or ".", exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_PATH, maxBytes=2_000_000, backupCount=3
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def _latest_or_none(store: Store, address: str):
    """Thin wrapper over ``store.latest_snapshot`` so tests can monkeypatch
    the snapshot lookup independently of the store (Ruling A)."""
    return store.latest_snapshot(address)


def cycle(store: Store, risk_cache: RiskCache, now: datetime, *, do_seed: bool) -> None:
    """One scan iteration. The whole body is guarded so one bad cycle never
    kills the loop -- failures are logged via ``logger.exception`` and swallowed.
    """
    try:
        if do_seed:
            universe.seed(store, now)
        poller.poll_once(store, now)
        hits = detector.detect_all(store, now)
        for hit in hits:
            snap = _latest_or_none(store, hit.address)
            if snap is None:
                continue
            passed, _reason, ctx = filters.evaluate(hit, store, risk_cache, snap)
            if passed:
                ctx.symbol = snap.symbol  # Ruling B
                alerter.send(hit, ctx, store, now)
    except Exception:
        logging.getLogger(_LOGGER_NAME).exception("cycle failed")


def _handler(signum, frame) -> None:  # pragma: no cover - invoked by the OS
    global _STOP
    _STOP = True


def main(argv: list[str] | None = None) -> int:
    global _STOP
    _STOP = False

    build_logger()
    log = logging.getLogger(_LOGGER_NAME)

    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)

    # A SIGINT handler can only be installed from the main thread; tolerate a
    # non-main-thread caller rather than crashing.
    try:
        signal.signal(signal.SIGINT, _handler)
    except ValueError:
        pass

    store = Store(config.DB_PATH)
    risk_cache = RiskCache()
    if not alerter.telegram_configured():
        log.warning("Telegram not configured -- running in terminal-only mode")

    log.info("scan started")
    cycle(store, risk_cache, datetime.now(UTC), do_seed=True)
    mark = time.monotonic()
    next_poll = mark + config.POLL_INTERVAL_SEC
    next_seed = mark + config.SEED_INTERVAL_SEC

    while not _STOP:
        if time.monotonic() < next_poll:
            time.sleep(min(1.0, max(0.0, next_poll - time.monotonic())))
            continue
        do_seed = time.monotonic() >= next_seed
        cycle(store, risk_cache, datetime.now(UTC), do_seed=do_seed)
        next_poll += config.POLL_INTERVAL_SEC
        if do_seed:
            next_seed += config.SEED_INTERVAL_SEC

    store.prune_history(datetime.now(UTC))
    store.close()
    log.info("scan stopped")
    return 0
