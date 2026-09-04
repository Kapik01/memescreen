"""SQLite persistence for the trade journal. Its own database file
(`JOURNAL_DB_PATH`), separate from the scanner -- the bot can run as a
separate process without any shared-volume concerns. One connection per
instance; every write commits immediately."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from memescreen import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL,
  alert_kind TEXT,
  symbol TEXT,
  entry_price REAL NOT NULL,
  entry_at TEXT NOT NULL,
  size_usd REAL,
  exit_price REAL,
  exit_at TEXT,
  pnl_pct REAL,
  note TEXT,
  opened_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_trades_open
  ON journal_trades(opened_by, address, exit_price);
CREATE TABLE IF NOT EXISTS watched_wallets (
  address TEXT PRIMARY KEY,
  label TEXT,
  added_by TEXT NOT NULL,
  added_at TEXT NOT NULL,
  last_sig TEXT
);
"""


class JournalStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "JournalStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- writes ---

    def open_trade(
        self, *, address: str, alert_kind: str | None, symbol: str | None,
        entry_price: float, size_usd: float | None, now: datetime, user: str,
    ) -> tuple[bool, str]:
        """Open a position. Refuses a second open position on the same token
        for the same user -- close the first with `out` before re-entering."""
        dup = self._conn.execute(
            "SELECT 1 FROM journal_trades "
            "WHERE address = ? AND opened_by = ? AND exit_price IS NULL LIMIT 1",
            (address, user),
        ).fetchone()
        if dup is not None:
            return False, "already have an open position on that token"
        self._conn.execute(
            "INSERT INTO journal_trades "
            "(address, alert_kind, symbol, entry_price, entry_at, size_usd, opened_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (address, alert_kind, symbol, entry_price, now.isoformat(), size_usd, user),
        )
        self._conn.commit()
        return True, "opened"

    def close_trade(
        self, *, address: str, exit_price: float, now: datetime, user: str,
    ) -> tuple[bool, str, float | None]:
        """Close the oldest open position on the token. Returns (ok, msg, pnl%)."""
        row = self._conn.execute(
            "SELECT id, entry_price FROM journal_trades "
            "WHERE address = ? AND opened_by = ? AND exit_price IS NULL "
            "ORDER BY entry_at ASC LIMIT 1",
            (address, user),
        ).fetchone()
        if row is None:
            return False, "no open position on that token", None
        pnl = (exit_price / row["entry_price"] - 1.0) * 100.0
        self._conn.execute(
            "UPDATE journal_trades SET exit_price = ?, exit_at = ?, pnl_pct = ? WHERE id = ?",
            (exit_price, now.isoformat(), pnl, row["id"]),
        )
        self._conn.commit()
        return True, "closed", pnl

    def add_note(self, *, address: str, note: str, user: str) -> bool:
        """Attach a note to the most recent trade (open or closed) on the token."""
        row = self._conn.execute(
            "SELECT id FROM journal_trades "
            "WHERE address = ? AND opened_by = ? ORDER BY entry_at DESC LIMIT 1",
            (address, user),
        ).fetchone()
        if row is None:
            return False
        self._conn.execute(
            "UPDATE journal_trades SET note = ? WHERE id = ?", (note, row["id"])
        )
        self._conn.commit()
        return True

    # --- reads ---

    def open_positions(self, user: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM journal_trades "
            "WHERE opened_by = ? AND exit_price IS NULL ORDER BY entry_at ASC",
            (user,),
        )
        return [dict(r) for r in cur.fetchall()]

    def closed_trades(self, user: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM journal_trades "
            "WHERE opened_by = ? AND exit_price IS NOT NULL ORDER BY exit_at ASC",
            (user,),
        )
        return [dict(r) for r in cur.fetchall()]

    # --- watched_wallets (smart-wallet buy tracking) ---

    def watch_wallet(
        self, *, address: str, label: str | None, user: str, now: datetime,
    ) -> tuple[bool, str]:
        """Start watching a wallet. Re-watching an already-watched wallet just
        updates its label (its `last_sig` cursor is untouched). Refuses a new
        wallet once `WALLET_WATCH_MAX` is reached."""
        existing = self._conn.execute(
            "SELECT 1 FROM watched_wallets WHERE address = ?", (address,)
        ).fetchone()
        if existing is None:
            n = self._conn.execute(
                "SELECT COUNT(*) AS n FROM watched_wallets"
            ).fetchone()["n"]
            if n >= config.WALLET_WATCH_MAX:
                return False, f"already watching {config.WALLET_WATCH_MAX} wallets (the max) -- !unwatch one first"
        self._conn.execute(
            "INSERT INTO watched_wallets (address, label, added_by, added_at, last_sig) "
            "VALUES (?, ?, ?, ?, NULL) "
            "ON CONFLICT(address) DO UPDATE SET label = excluded.label",
            (address, label, user, now.isoformat()),
        )
        self._conn.commit()
        return True, "watching"

    def unwatch_wallet(self, address: str) -> bool:
        cur = self._conn.execute("DELETE FROM watched_wallets WHERE address = ?", (address,))
        self._conn.commit()
        return cur.rowcount > 0

    def list_watched(self) -> list[dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM watched_wallets ORDER BY added_at ASC")
        return [dict(r) for r in cur.fetchall()]

    def update_wallet_cursor(self, address: str, last_sig: str | None) -> None:
        self._conn.execute(
            "UPDATE watched_wallets SET last_sig = ? WHERE address = ?", (last_sig, address)
        )
        self._conn.commit()
