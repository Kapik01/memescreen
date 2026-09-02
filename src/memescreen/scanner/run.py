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
from memescreen.scanner import alerter, detector, filters, momentum, poller, universe
from memescreen.scanner.riskcache import RiskCache
from memescreen.scanner.store import Store

_LOGGER_NAME = "memescreen.scanner"
_LOG_PATH = config.LOG_PATH
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


def _advance(mark: float, interval: float, now: float) -> float:
    """Next cadence mark, skipping any slot a slow cycle has already missed.

    Plain `mark += interval` accumulates: a cycle that overruns by minutes
    (50 sequential DexScreener calls x a 10s timeout with a retry, when the
    API is degraded) leaves the mark far in the past, and the loop then fires
    several back-to-back zero-sleep cycles at the already-struggling API --
    with `do_seed` latching true for each. Clamping to `now` drops the missed
    slots instead of replaying them.
    """
    return max(mark + interval, now)


def _latest_or_none(store: Store, address: str):
    """Thin wrapper over ``store.latest_snapshot`` so tests can monkeypatch
    the snapshot lookup independently of the store (Ruling A)."""
    return store.latest_snapshot(address)


def cycle(store: Store, risk_cache: RiskCache, now: datetime, *, do_seed: bool) -> None:
    """One scan iteration. The whole body is guarded so one bad cycle never
    kills the loop -- failures are logged via ``logger.exception`` and swallowed.
    """
    log = logging.getLogger(_LOGGER_NAME)
    try:
        if do_seed:
            universe.seed(store, now)
        poller.poll_once(store, now)
        hits = detector.detect_all(store, now)
        if hits:
            log.info(
                "detected %d anomaly hit(s): %s",
                len(hits), ", ".join(f"{h.address[:8]}(x{h.spike_ratio:.1f})" for h in hits),
            )
        drops: dict[str, int] = {}
        passed_n = 0
        for hit in hits:
            snap = _latest_or_none(store, hit.address)
            if snap is None:
                # Unreachable while the detector only fires on stored history,
                # but spec section 6 requires every hit to reach the table.
                store.record_hit(hit, passed_gate=False, fail_reason="no snapshot")
                log.info("gate: %s dropped (no snapshot)", hit.address[:8])
                drops["no snapshot"] = drops.get("no snapshot", 0) + 1
                continue
            passed, reason, ctx = filters.evaluate(hit, store, risk_cache, snap)
            ctx.symbol = snap.symbol  # Ruling B -- both the alert and the reject feed use it
            if passed:
                passed_n += 1
                sent = alerter.send(hit, ctx, store, now)
                log.info(
                    "gate: %s PASSED -> alert %s",
                    hit.address[:8], "sent" if sent else "not sent (cooldown / delivery)",
                )
            else:
                log.info("gate: %s dropped (%s)", hit.address[:8], reason)
                drops[reason] = drops.get(reason, 0) + 1
                alerter.send_reject(hit, ctx, reason, now)
        if hits:
            # One rollup line per cycle so tuning is data-driven: which filter
            # is actually eating the hits, and how many.
            tally = ", ".join(
                f"{r}:{n}" for r, n in sorted(drops.items(), key=lambda kv: -kv[1])
            )
            log.info(
                "cycle summary: detected=%d passed=%d dropped=%d%s",
                len(hits), passed_n, sum(drops.values()),
                f" [{tally}]" if tally else "",
            )
        # NEW PUMP momentum scan: independent of the spike funnel above, walks
        # every tracked token's latest snapshot for a young price run-up.
        momentum.scan(store, risk_cache, now)
    except Exception:
        log.exception("cycle failed")


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

    # Paths in config are cwd-relative; log the resolved one so a run started
    # from an unexpected directory is obvious in the log rather than silently
    # writing a second database.
    log.info("db: %s", os.path.abspath(config.DB_PATH))
    store = Store(config.DB_PATH)
    risk_cache = RiskCache()
    if not alerter.discord_configured():
        log.warning(
            "Discord webhook not configured -- alerts will print and append to %s",
            config.MISSED_LOG_PATH,
        )

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
        now_mono = time.monotonic()
        next_poll = _advance(next_poll, config.POLL_INTERVAL_SEC, now_mono)
        if do_seed:
            next_seed = _advance(next_seed, config.SEED_INTERVAL_SEC, now_mono)

    store.prune_history(datetime.now(UTC))
    store.close()
    log.info("scan stopped")
    return 0
