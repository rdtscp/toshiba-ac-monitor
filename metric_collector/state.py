"""Thread-safe shared state between the network thread and the UI.

This is the single synchronisation point in the app. The network `AcSource`
runs on a background daemon thread and is the only writer; the rumps UI on the
main thread is the only reader. A single lock guards all access, and readers
always receive immutable snapshots so they can render without holding the lock.

Keeping this module dependency-free (no rumps, no toshiba-ac) makes it trivial
to unit-test and keeps the concurrency contract in one obvious place.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional


class ConnectionStatus(str, Enum):
    """Lifecycle of the upstream (Toshiba cloud / Azure IoT) connection."""

    STARTING = "starting"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True)
class RoomState:
    """An immutable snapshot of one indoor unit.

    `indoor_temperature` / `outdoor_temperature` are deliberately Optional:
    some units only report indoor temperature while powered on and return
    None otherwise. The UI must handle None explicitly (show "--").
    """

    device_id: str
    # Name as Toshiba reports it (the app's label for the unit).
    toshiba_name: str
    # Name after applying the user's room-name remap; falls back to toshiba_name.
    display_name: str
    indoor_temperature: Optional[float] = None
    outdoor_temperature: Optional[float] = None
    target_temperature: Optional[float] = None
    power_on: bool = False
    mode: Optional[str] = None  # e.g. "cooling", "heating", "auto", "fan"
    # Monotonic-derived wall-clock epoch (time.time()) of the last update for
    # THIS room. Used for per-room staleness if ever needed; the app primarily
    # uses the global last_update on StateSnapshot.
    updated_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class StateSnapshot:
    """An immutable, point-in-time view of the whole app state.

    Returned to the UI thread so it can render without holding the lock.
    """

    status: ConnectionStatus
    rooms: tuple[RoomState, ...]
    # Epoch of the most recent successful data update from upstream, or None
    # if we have never received data. Drives the staleness indicator.
    last_update: Optional[float]
    # Human-readable detail for the last error, if status == ERROR.
    last_error: Optional[str]

    def seconds_since_update(self, now: Optional[float] = None) -> Optional[float]:
        if self.last_update is None:
            return None
        return (now if now is not None else time.time()) - self.last_update


class SharedState:
    """Lock-guarded mutable state. The network thread writes; the UI reads.

    All public methods are safe to call from any thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rooms: dict[str, RoomState] = {}
        self._status = ConnectionStatus.STARTING
        self._last_update: Optional[float] = None
        self._last_error: Optional[str] = None

    # --- writer side (network thread) -----------------------------------

    def upsert_room(self, room: RoomState, *, mark_update: bool = True) -> None:
        """Insert or replace a room snapshot.

        `mark_update=True` advances the global last_update clock, which the
        staleness indicator keys off. Pass False for housekeeping writes that
        shouldn't count as "fresh data" (e.g. a name remap with no telemetry).
        """
        with self._lock:
            self._rooms[room.device_id] = room
            if mark_update:
                self._last_update = room.updated_at

    def set_status(self, status: ConnectionStatus, error: Optional[str] = None) -> None:
        with self._lock:
            self._status = status
            # Keep the detail for the degraded states; clear it on healthy ones.
            if status in (ConnectionStatus.ERROR, ConnectionStatus.RECONNECTING):
                self._last_error = error
            else:
                self._last_error = None

    def set_display_name(self, device_id: str, display_name: str) -> None:
        """Apply a remapped display name without counting as fresh telemetry."""
        with self._lock:
            existing = self._rooms.get(device_id)
            if existing is not None:
                self._rooms[device_id] = replace(existing, display_name=display_name)

    # --- reader side (UI thread) ----------------------------------------

    def snapshot(self) -> StateSnapshot:
        """Return an immutable copy of the entire state."""
        with self._lock:
            rooms = tuple(
                sorted(self._rooms.values(), key=lambda r: r.display_name.lower())
            )
            return StateSnapshot(
                status=self._status,
                rooms=rooms,
                last_update=self._last_update,
                last_error=self._last_error,
            )
