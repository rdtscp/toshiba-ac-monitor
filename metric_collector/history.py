"""Durable time-series capture of room readings (SQLite).

A HistoryRecorder is a StateSink: the source publishes every RoomState through
AcSource.publish_room(), which hands it here in addition to the realtime
SharedState cache. We persist to ~/.toshiba_menubar/history.db so temperatures
can be reviewed historically, well outside the menu bar's last-value view.

Design choices:
  * SQLite, stdlib, single file, WAL mode -> an external viewer/query tool can
    read concurrently while the app writes.
  * Writes happen on the source's daemon thread (never the UI thread). A lock
    guards the connection so close() from the UI thread on quit is safe.
  * Throttled: at most one stored sample per device per `interval_seconds`,
    PLUS an immediate sample whenever power/mode changes (so on/off and
    cooling<->heating transitions are never missed between intervals).
  * Retention: rows older than `retention_days` are pruned periodically
    (None = keep forever).

This module is also runnable for ad-hoc viewing:
    python -m metric_collector.history --since 24h
    python -m metric_collector.history --since 7d --csv > temps.csv
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .config import DEVICE_STATE_PATH, ensure_app_home
from .state import RoomState


HISTORY_DB_PATH = DEVICE_STATE_PATH.parent / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    ts           REAL    NOT NULL,   -- epoch seconds (UTC)
    device_id    TEXT    NOT NULL,
    display_name TEXT,
    indoor_c     REAL,
    outdoor_c    REAL,
    target_c     REAL,
    power_on     INTEGER NOT NULL,
    mode         TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings (device_id, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts);
"""

_PRUNE_EVERY_SECONDS = 3600.0  # at most once an hour


@dataclass
class _LastWrite:
    ts: float
    power_on: bool
    mode: Optional[str]


class HistoryRecorder:
    """SQLite-backed StateSink. Thread-safe; cheap on the hot path."""

    def __init__(
        self,
        path: Path = HISTORY_DB_PATH,
        *,
        interval_seconds: float = 60.0,
        retention_days: Optional[int] = 365,
    ) -> None:
        self.path = path
        self.interval_seconds = max(1.0, interval_seconds)
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._last: dict[str, _LastWrite] = {}
        self._last_prune = 0.0

        ensure_app_home()
        # check_same_thread=False: record() runs on the daemon thread, close()
        # on the UI thread; we serialise both with self._lock.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        self._prune(time.time())

    # --- StateSink API ---------------------------------------------------

    def record(self, room: RoomState) -> None:
        now = room.updated_at or time.time()
        if not self._should_write(room, now):
            return

        with self._lock:
            self._conn.execute(
                "INSERT INTO readings "
                "(ts, device_id, display_name, indoor_c, outdoor_c, target_c, power_on, mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    room.device_id,
                    room.display_name,
                    room.indoor_temperature,
                    room.outdoor_temperature,
                    room.target_temperature,
                    int(room.power_on),
                    room.mode,
                ),
            )
            self._conn.commit()

        self._last[room.device_id] = _LastWrite(now, room.power_on, room.mode)
        self._maybe_prune(now)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            finally:
                self._conn.close()

    # --- write policy ----------------------------------------------------

    def _should_write(self, room: RoomState, now: float) -> bool:
        prev = self._last.get(room.device_id)
        if prev is None:
            return True
        # Always capture state transitions immediately.
        if room.power_on != prev.power_on or room.mode != prev.mode:
            return True
        # Otherwise throttle to the sampling cadence.
        return (now - prev.ts) >= self.interval_seconds

    # --- retention -------------------------------------------------------

    def _maybe_prune(self, now: float) -> None:
        # Throttle in the SAME time domain as incoming readings (room.updated_at),
        # so prune cadence isn't skewed by wall-clock vs. data-clock differences.
        if now - self._last_prune >= _PRUNE_EVERY_SECONDS:
            self._last_prune = now
            self._prune(now)

    def _prune(self, now: float) -> None:
        if self.retention_days is None:
            return
        cutoff = now - self.retention_days * 86400
        with self._lock:
            self._conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
            self._conn.commit()


# --- read-side helpers (for "view it elsewhere") -------------------------


@dataclass(frozen=True)
class Reading:
    ts: float
    device_id: str
    display_name: Optional[str]
    indoor_c: Optional[float]
    outdoor_c: Optional[float]
    target_c: Optional[float]
    power_on: bool
    mode: Optional[str]


def query_readings(
    path: Path = HISTORY_DB_PATH,
    *,
    since_seconds: Optional[float] = None,
    device_id: Optional[str] = None,
    now: Optional[float] = None,
) -> list[Reading]:
    """Read history back. Opens its own read-only-ish connection.

    Safe to call from a separate process/tool while the app is writing (WAL).
    """
    now = now if now is not None else time.time()
    sql = "SELECT ts, device_id, display_name, indoor_c, outdoor_c, target_c, power_on, mode FROM readings"
    clauses: list[str] = []
    params: list[object] = []
    if since_seconds is not None:
        clauses.append("ts >= ?")
        params.append(now - since_seconds)
    if device_id is not None:
        clauses.append("device_id = ?")
        params.append(device_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY ts ASC"

    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        Reading(ts, dev, name, indoor, outdoor, target, bool(power), mode)
        for (ts, dev, name, indoor, outdoor, target, power, mode) in rows
    ]


# --- CLI: ad-hoc viewing -------------------------------------------------


def _parse_duration(text: str) -> float:
    """Parse '24h', '7d', '90m', '3600s' (or a bare number = seconds)."""
    text = text.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text and text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text)


def _rows_to_csv(readings: Iterable[Reading]) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["iso_time", "device_id", "display_name", "indoor_c", "outdoor_c", "target_c", "power_on", "mode"]
    )
    for r in readings:
        iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(r.ts))
        writer.writerow(
            [iso, r.device_id, r.display_name, r.indoor_c, r.outdoor_c, r.target_c, int(r.power_on), r.mode]
        )
    return buf.getvalue()


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Query captured Toshiba AC temperature history.",
    )
    parser.add_argument("--since", default="24h", help="window, e.g. 24h, 7d, 90m (default 24h)")
    parser.add_argument("--device", default=None, help="filter to one device_id")
    parser.add_argument("--csv", action="store_true", help="emit CSV instead of a summary")
    parser.add_argument("--db", default=str(HISTORY_DB_PATH), help="path to history.db")
    args = parser.parse_args(argv)

    readings = query_readings(
        Path(args.db), since_seconds=_parse_duration(args.since), device_id=args.device
    )

    if args.csv:
        print(_rows_to_csv(readings), end="")
        return 0

    if not readings:
        print(f"No readings in the last {args.since} ({args.db}).")
        return 0

    # Compact per-device summary.
    by_device: dict[str, list[Reading]] = {}
    for r in readings:
        by_device.setdefault(r.device_id, []).append(r)

    print(f"{len(readings)} reading(s) over the last {args.since}:")
    for dev, rs in by_device.items():
        indoors = [r.indoor_c for r in rs if r.indoor_c is not None]
        name = rs[-1].display_name or dev
        if indoors:
            print(
                f"  {name}: {len(rs)} samples, indoor min/avg/max = "
                f"{min(indoors):.1f}/{sum(indoors) / len(indoors):.1f}/{max(indoors):.1f}°C, "
                f"latest {rs[-1].indoor_c}°C"
            )
        else:
            print(f"  {name}: {len(rs)} samples, no indoor readings (unit off?)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
