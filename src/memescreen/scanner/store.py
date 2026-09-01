"""SQLite persistence layer for the scanner: token history, the tracked
universe, detection hits, sent alerts, and per-token hit context. One
`sqlite3.Connection` per `Store` instance; every write commits immediately."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Iterable

from memescreen import config
from memescreen.models import AnomalyHit, MarketSnapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_history (
  address TEXT NOT NULL, ts TEXT NOT NULL,
  price_usd REAL, liquidity_usd REAL, market_cap_usd REAL,
  vol_m5 REAL, vol_h1 REAL, vol_h6 REAL, vol_h24 REAL,
  buys_m5 INTEGER, sells_m5 INTEGER,
  PRIMARY KEY (address, ts)
);
CREATE INDEX IF NOT EXISTS ix_history_addr_ts ON token_history(address, ts);
CREATE TABLE IF NOT EXISTS universe (
  address TEXT PRIMARY KEY, first_seen TEXT NOT NULL,
  last_seen_in_feed TEXT, last_activity_ts TEXT
);
CREATE TABLE IF NOT EXISTS hits (
  address TEXT NOT NULL, detected_at TEXT NOT NULL, anomaly_score INTEGER,
  spike_ratio REAL, accel REAL, buy_ratio REAL,
  passed_gate INTEGER NOT NULL, fail_reason TEXT
);
CREATE TABLE IF NOT EXISTS alerts_sent (
  address TEXT NOT NULL, sent_at TEXT NOT NULL, anomaly_score INTEGER
);
CREATE TABLE IF NOT EXISTS hit_context (
  address TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL,
  first_holder_count INTEGER, first_holder_count_at TEXT
);
"""

_HISTORY_COLUMNS = (
    "price_usd", "liquidity_usd", "market_cap_usd",
    "vol_m5", "vol_h1", "vol_h6", "vol_h24",
    "buys_m5", "sells_m5",
)


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- token_history ---

    def append_snapshots(self, snaps: Iterable[MarketSnapshot]) -> None:
        rows = [
            (
                snap.address, snap.fetched_at.isoformat(),
                snap.price_usd, snap.liquidity_usd, snap.market_cap_usd,
                snap.vol_m5, snap.vol_h1, snap.vol_h6, snap.vol_h24,
                snap.buys_m5, snap.sells_m5,
            )
            for snap in snaps
        ]
        if not rows:
            return
        self._conn.executemany(
            "INSERT OR REPLACE INTO token_history "
            "(address, ts, price_usd, liquidity_usd, market_cap_usd, "
            "vol_m5, vol_h1, vol_h6, vol_h24, buys_m5, sells_m5) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def history(self, address: str, since: datetime) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM token_history WHERE address = ? AND ts >= ? "
            "ORDER BY ts ASC",
            (address, since.isoformat()),
        )
        out = []
        for row in cur.fetchall():
            d = dict(row)
            d["ts"] = datetime.fromisoformat(d["ts"])
            out.append(d)
        return out

    def earliest_ts(self, address: str) -> datetime | None:
        cur = self._conn.execute(
            "SELECT MIN(ts) AS ts FROM token_history WHERE address = ?",
            (address,),
        )
        row = cur.fetchone()
        if row is None or row["ts"] is None:
            return None
        return datetime.fromisoformat(row["ts"])

    def latest_snapshot(self, address: str) -> MarketSnapshot | None:
        cur = self._conn.execute(
            "SELECT * FROM token_history WHERE address = ? "
            "ORDER BY ts DESC LIMIT 1",
            (address,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        return MarketSnapshot(
            address=d["address"],
            fetched_at=datetime.fromisoformat(d["ts"]),
            symbol=None,
            price_usd=d["price_usd"],
            liquidity_usd=d["liquidity_usd"],
            market_cap_usd=d["market_cap_usd"],
            vol_m5=d["vol_m5"],
            vol_h1=d["vol_h1"],
            vol_h6=d["vol_h6"],
            vol_h24=d["vol_h24"],
            buys_m5=d["buys_m5"],
            sells_m5=d["sells_m5"],
            pair_created_at=None,
            dex=None,
        )

    def prune_history(self, now: datetime) -> int:
        cutoff = (now - timedelta(hours=config.HISTORY_RETENTION_H)).isoformat()
        cur = self._conn.execute(
            "DELETE FROM token_history WHERE ts < ?", (cutoff,)
        )
        self._conn.commit()
        return cur.rowcount

    # --- universe ---

    def upsert_seen(self, addresses: set[str], now: datetime) -> None:
        now_iso = now.isoformat()
        self._conn.executemany(
            "INSERT INTO universe (address, first_seen, last_seen_in_feed) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(address) DO UPDATE SET last_seen_in_feed = excluded.last_seen_in_feed",
            [(addr, now_iso, now_iso) for addr in addresses],
        )
        self._conn.commit()

    def touch_activity(self, addresses: Iterable[str], now: datetime) -> None:
        now_iso = now.isoformat()
        self._conn.executemany(
            "UPDATE universe SET last_activity_ts = ? WHERE address = ?",
            [(now_iso, addr) for addr in addresses],
        )
        self._conn.commit()

    def universe_addresses(self) -> list[str]:
        cur = self._conn.execute("SELECT address FROM universe")
        return [row["address"] for row in cur.fetchall()]

    def first_seen(self, address: str) -> datetime | None:
        cur = self._conn.execute(
            "SELECT first_seen FROM universe WHERE address = ?", (address,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["first_seen"])

    def evict(self, now: datetime, cap: int) -> int:
        cutoff = (now - timedelta(hours=config.HISTORY_RETENTION_H)).isoformat()
        cur = self._conn.execute(
            "DELETE FROM universe WHERE "
            "(last_seen_in_feed IS NULL OR last_seen_in_feed < ?) AND "
            "(last_activity_ts IS NULL OR last_activity_ts < ?)",
            (cutoff, cutoff),
        )
        deleted = cur.rowcount
        self._conn.commit()

        cur = self._conn.execute("SELECT COUNT(*) AS n FROM universe")
        remaining = cur.fetchone()["n"]
        if remaining > cap:
            excess = remaining - cap
            cur = self._conn.execute(
                "DELETE FROM universe WHERE address IN ("
                "SELECT address FROM universe "
                "ORDER BY COALESCE(last_activity_ts, first_seen) ASC LIMIT ?"
                ")",
                (excess,),
            )
            deleted += cur.rowcount
            self._conn.commit()

        return deleted

    # --- hits ---

    def record_hit(self, hit: AnomalyHit, passed_gate: bool, fail_reason: str | None) -> None:
        self._conn.execute(
            "INSERT INTO hits (address, detected_at, anomaly_score, "
            "spike_ratio, accel, buy_ratio, passed_gate, fail_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                hit.address, hit.detected_at.isoformat(), hit.anomaly_score,
                hit.spike_ratio, hit.accel, hit.buy_ratio,
                1 if passed_gate else 0, fail_reason,
            ),
        )
        self._conn.commit()

    # --- alerts_sent ---

    def recent_alert(self, address: str, now: datetime, cooldown_h: float) -> bool:
        cutoff = (now - timedelta(hours=cooldown_h)).isoformat()
        cur = self._conn.execute(
            "SELECT 1 FROM alerts_sent WHERE address = ? AND sent_at >= ? LIMIT 1",
            (address, cutoff),
        )
        return cur.fetchone() is not None

    def record_alert(self, address: str, now: datetime, anomaly_score: int) -> None:
        self._conn.execute(
            "INSERT INTO alerts_sent (address, sent_at, anomaly_score) VALUES (?, ?, ?)",
            (address, now.isoformat(), anomaly_score),
        )
        self._conn.commit()

    # --- hit_context ---

    def hit_context(self, address: str) -> dict | None:
        cur = self._conn.execute(
            "SELECT first_seen_at, first_holder_count, first_holder_count_at "
            "FROM hit_context WHERE address = ?",
            (address,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "first_seen_at": datetime.fromisoformat(row["first_seen_at"]),
            "first_holder_count": row["first_holder_count"],
            "first_holder_count_at": (
                datetime.fromisoformat(row["first_holder_count_at"])
                if row["first_holder_count_at"] is not None
                else None
            ),
        }

    def ensure_hit_context(self, address: str, now: datetime, holder_count: int | None) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO hit_context "
            "(address, first_seen_at, first_holder_count, first_holder_count_at) "
            "VALUES (?, ?, ?, ?)",
            (address, now.isoformat(), holder_count, now.isoformat()),
        )
        self._conn.commit()
