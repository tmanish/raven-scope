"""SQLite persistence — makes RavenScope a real monitor, not a live-only toy.

Stores a downsampled motion track, every state transition / anomaly event, and
supports an occupancy heatmap query (activity per hour-of-day). stdlib sqlite3.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import List, Optional, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL PRIMARY KEY,
    energy REAL NOT NULL,
    state TEXT NOT NULL,
    n_links INTEGER NOT NULL,
    z REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


class Store:
    def __init__(self, path: str = "ravenscope.db"):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._last_sample_write = 0.0

    def log_sample(self, ts: float, energy: float, state: str, n_links: int,
                   z: float = 0.0, min_interval: float = 1.0) -> None:
        # downsample writes to ~1 Hz to keep the db small over long runs
        if ts - self._last_sample_write < min_interval:
            return
        self._last_sample_write = ts
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?)",
                    (ts, energy, state, n_links, z))
                self._conn.commit()
            except sqlite3.Error:
                pass

    def log_event(self, ts: float, kind: str, detail: str = "") -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO events (ts, kind, detail) VALUES (?,?,?)",
                    (ts, kind, detail))
                self._conn.commit()
            except sqlite3.Error:
                pass

    def recent_samples(self, seconds: float = 600) -> List[Tuple]:
        cutoff = time.time() - seconds
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts, energy, state, z FROM samples WHERE ts >= ? "
                "ORDER BY ts ASC", (cutoff,))
            return cur.fetchall()

    def recent_events(self, limit: int = 50) -> List[Tuple]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT ?",
                (limit,))
            return cur.fetchall()

    def occupancy_by_hour(self, days: float = 7) -> List[float]:
        """Average motion energy per hour-of-day over the last `days` days."""
        cutoff = time.time() - days * 86400
        buckets = [0.0] * 24
        counts = [0] * 24
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts, energy FROM samples WHERE ts >= ?", (cutoff,))
            for ts, energy in cur.fetchall():
                h = time.localtime(ts).tm_hour
                buckets[h] += energy
                counts[h] += 1
        return [buckets[i] / counts[i] if counts[i] else 0.0 for i in range(24)]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
