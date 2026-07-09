"""Synthetic data source — drives the app with no network or credentials.

This is the default source. It mimics the shape of real telemetry closely
enough to exercise the UI paths:

  * three indoor units — Living Room, Office, Bedroom — matching a typical
    3-head home setup,
  * indoor temperatures that drift slowly over time, each around a different
    baseline, with varied modes (cooling / auto),
  * a shared outdoor temperature.

To exercise the "unit off → no indoor temp → '--'" rendering path, flip any
unit's "on" to False below (a powered-off unit publishes indoor=None, like the
real hardware). And to eyeball the staleness indicator, set
`freeze_after_seconds` to stop publishing updates after a while.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Optional

from ..state import ConnectionStatus, RoomState, SharedState
from .base import AcSource


# Three indoor heads mirroring a typical home. All powered on by default; set
# "on": False on any unit to exercise the off / indoor=None / "--" path.
_DUMMY_UNITS = [
    {"device_id": "dummy-living",  "toshiba_name": "Living Room", "base_temp": 22.0, "mode": "cooling", "on": True},
    {"device_id": "dummy-office",  "toshiba_name": "Office",      "base_temp": 24.0, "mode": "cooling", "on": True},
    {"device_id": "dummy-bedroom", "toshiba_name": "Bedroom",     "base_temp": 21.0, "mode": "auto",    "on": True},
]


class DummySource(AcSource):
    def __init__(self, *args, tick_seconds: float = 3.0,
                 freeze_after_seconds: Optional[float] = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tick = tick_seconds
        self._freeze_after = freeze_after_seconds
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="dummy-source", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._refresh.set()  # wake the loop so it can exit promptly
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.close_recorders()

    def request_refresh(self) -> None:
        # Just wake the loop to publish immediately.
        self._refresh.set()

    # --- background thread ----------------------------------------------

    def _run(self) -> None:
        started = time.time()
        self.state.set_status(ConnectionStatus.CONNECTING)
        # Simulate a brief connect.
        if self._stop.wait(0.5):
            return
        self.state.set_status(ConnectionStatus.CONNECTED)

        while not self._stop.is_set():
            now = time.time()
            frozen = (
                self._freeze_after is not None
                and (now - started) > self._freeze_after
            )
            if not frozen:
                self._publish(now)
            # Wait for the next tick, or an external refresh request, or stop.
            self._refresh.wait(timeout=self._tick)
            self._refresh.clear()

        self.state.set_status(ConnectionStatus.STOPPED)

    def _publish(self, now: float) -> None:
        outdoor = round(28.0 + 4.0 * math.sin(now / 120.0), 1)
        for unit in _DUMMY_UNITS:
            if unit["on"]:
                # Slow drift around the base temperature.
                drift = 1.5 * math.sin(now / 90.0 + hash(unit["device_id"]) % 7)
                indoor: Optional[float] = round(unit["base_temp"] + drift, 1)
                target: Optional[float] = unit["base_temp"]
            else:
                # Powered-off units often report no indoor temperature.
                indoor = None
                target = None

            display = self.config.display_name_for(
                unit["toshiba_name"], unit["device_id"]
            )
            self.publish_room(
                RoomState(
                    device_id=unit["device_id"],
                    toshiba_name=unit["toshiba_name"],
                    display_name=display,
                    indoor_temperature=indoor,
                    outdoor_temperature=outdoor,
                    target_temperature=target,
                    power_on=bool(unit["on"]),
                    mode=unit["mode"],
                    updated_at=now,
                )
            )
