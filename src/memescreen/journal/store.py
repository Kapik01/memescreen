"""SQLite persistence for the trade journal. Its own database file
(`JOURNAL_DB_PATH`), separate from the scanner -- the bot can run as a
separate process without any shared-volume concerns. One connection per
instance; every write commits immediately."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

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
