"""The real data source: Toshiba cloud + Azure IoT push telemetry.

Runs entirely on a background daemon thread with its own asyncio event loop.
Verified against the installed `toshiba-ac` 0.3.13 source:

  ToshibaAcDeviceManager(username, password, device_id=None, sas_token=None)
      * NOTE: __init__ calls asyncio.get_running_loop(), so the manager MUST be
        constructed inside a running loop (we are — inside _main()).
      * `device_id` is a SHORT suffix; internal id becomes f"{username}_{id}".
      * await connect() -> sas_token: str   (registers a client if no token)
      * await get_devices() -> list[ToshibaAcDevice]
      * await shutdown()
      * manager.on_sas_token_updated_callback.add(cb)  # cb(token: str)

  ToshibaAcDevice
      * .name, .ac_unique_id
      * .ac_indoor_temperature  -> Optional[int]   (push telemetry)
      * .ac_outdoor_temperature -> Optional[int]
      * .ac_temperature         -> Optional[int]   (setpoint)
      * .ac_status -> ToshibaAcStatus (ON/OFF/NONE)
      * .ac_mode   -> ToshibaAcMode (AUTO/COOL/HEAT/DRY/FAN/NONE)
      * .on_state_changed_callback.add(cb)  # cb(device); sync or async

Threading model:
  - The UI thread calls start()/stop()/request_refresh() — all non-blocking.
  - stop()/request_refresh() poke asyncio.Events via loop.call_soon_threadsafe.
  - The async loop is the only thing that ever touches the network.

Reconnection: connect() failures are retried with capped exponential backoff.
The library handles its own AMQP-level token renewal once connected; we can't
cleanly observe a silently-dropped connection, so we ALSO re-publish cached
attributes every `config.poll_seconds`. A genuinely dead-but-not-erroring
connection then surfaces via the staleness indicator rather than a freeze.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional

from ..backoff import ExponentialBackoff
from ..config import Config, DeviceState, load_device_state, save_device_state
from ..credentials import CredentialsError, resolve_credentials
from ..state import ConnectionStatus, RoomState, SharedState
from .base import AcSource


# Map the library's ac_mode enum onto the friendly vocabulary the UI uses
# (and which the dummy source also emits), keyed by enum .name.
_MODE_WORDS = {
    "COOL": "cooling",
    "HEAT": "heating",
    "AUTO": "auto",
    "DRY": "dry",
    "FAN": "fan",
}


class ToshibaSource(AcSource):
    def __init__(self, state: SharedState, config: Config, recorders=()) -> None:
        super().__init__(state, config, recorders)
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # asyncio.Events, created inside the loop in _main().
        self._stop_evt: Optional[asyncio.Event] = None
        self._refresh_evt: Optional[asyncio.Event] = None
        # Cap of 30 min, not 5: every connect() attempt costs ~3 Toshiba
        # /api/Consumer/Login calls (the lib retries internally), and the
        # cloud 429-rate-limits logins. Retrying every 5 min keeps the
        # limiter hot indefinitely; transient blips still reconnect within
        # seconds via the early backoff steps.
        self._backoff = ExponentialBackoff(base=2.0, cap=1800.0)
        self._device_state: Optional[DeviceState] = None

    # --- AcSource API (called from the UI thread) -----------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._thread_main, name="toshiba-source", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._poke(lambda: self._stop_evt and self._stop_evt.set())
        if self._thread is not None:
            self._thread.join(timeout=10.0)
        self.close_recorders()

    def request_refresh(self) -> None:
        self._poke(lambda: self._refresh_evt and self._refresh_evt.set())

    def _poke(self, fn) -> None:
        """Run a 0-arg callable on the loop thread, if the loop is alive."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(fn)

    # --- background thread entry ----------------------------------------

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:  # last-resort guard; thread must not crash silently
            self.state.set_status(ConnectionStatus.ERROR, f"source thread died: {exc}")

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_evt = asyncio.Event()
        self._refresh_evt = asyncio.Event()

        try:
            creds = resolve_credentials()
        except CredentialsError as exc:
            self.state.set_status(ConnectionStatus.ERROR, str(exc))
            return

        self._device_state = load_device_state()

        first = True
        while not self._stop_evt.is_set():
            self.state.set_status(
                ConnectionStatus.CONNECTING if first else ConnectionStatus.RECONNECTING
            )
            manager = None
            try:
                manager = await self._connect_and_run(creds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.set_status(ConnectionStatus.ERROR, _short(exc))
            finally:
                if manager is not None:
                    await _safe_shutdown(manager)

            if self._stop_evt.is_set():
                break

            delay = self._backoff.next_delay()
            await self._sleep_or_stop(delay)
            first = False

        self.state.set_status(ConnectionStatus.STOPPED)

    # --- connect + run one session --------------------------------------

    async def _connect_and_run(self, creds):
        # Imported lazily so the dummy-source path never imports the heavy
        # azure-iot stack.
        from toshiba_ac.device_manager import ToshibaAcDeviceManager

        assert self._device_state is not None
        manager = ToshibaAcDeviceManager(
            creds.username,
            creds.password,
            self._device_state.device_id,
            self._device_state.sas_token,
        )
        manager.on_sas_token_updated_callback.add(self._on_sas_token_updated)

        token = await manager.connect()
        self._persist_token(token)

        devices = await manager.get_devices()
        for device in devices:
            device.on_state_changed_callback.add(self._on_device_state_changed)

        self.state.set_status(ConnectionStatus.CONNECTED)
        self._backoff.reset()

        # Initial publish from whatever state the library already has.
        for device in devices:
            self._publish_device(device)

        # Stay alive: re-publish on push (the callback does that) and also on a
        # periodic poll, until asked to stop.
        assert self._stop_evt is not None and self._refresh_evt is not None
        while not self._stop_evt.is_set():
            await self._wait_refresh_or_timeout(self.config.poll_seconds)
            self._refresh_evt.clear()
            for device in devices:
                self._publish_device(device)

        return manager

    # --- callbacks from the library (run on the loop thread) ------------

    def _on_device_state_changed(self, device) -> None:
        # Push telemetry arrived (or setpoint changed). Mirror into shared state.
        self._publish_device(device)

    def _on_sas_token_updated(self, token: str) -> None:
        self._persist_token(token)

    # --- helpers ---------------------------------------------------------

    def _persist_token(self, token: str) -> None:
        if self._device_state is None:
            return
        if token and token != self._device_state.sas_token:
            self._device_state.sas_token = token
            save_device_state(self._device_state)

    def _publish_device(self, device) -> None:
        from toshiba_ac.device.properties import ToshibaAcStatus

        indoor = device.ac_indoor_temperature
        outdoor = device.ac_outdoor_temperature
        target = device.ac_temperature

        mode_word = _MODE_WORDS.get(getattr(device.ac_mode, "name", ""), None)
        display = self.config.display_name_for(device.name, device.ac_unique_id)

        self.publish_room(
            RoomState(
                device_id=device.ac_unique_id,
                toshiba_name=device.name,
                display_name=display,
                indoor_temperature=float(indoor) if indoor is not None else None,
                outdoor_temperature=float(outdoor) if outdoor is not None else None,
                target_temperature=float(target) if target is not None else None,
                power_on=device.ac_status == ToshibaAcStatus.ON,
                mode=mode_word,
                updated_at=time.time(),
            )
        )

    async def _sleep_or_stop(self, delay: float) -> None:
        assert self._stop_evt is not None
        try:
            await asyncio.wait_for(self._stop_evt.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def _wait_refresh_or_timeout(self, timeout: float) -> None:
        assert self._refresh_evt is not None and self._stop_evt is not None
        stop_task = asyncio.ensure_future(self._stop_evt.wait())
        refresh_task = asyncio.ensure_future(self._refresh_evt.wait())
        try:
            await asyncio.wait(
                {stop_task, refresh_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_task.cancel()
            refresh_task.cancel()


def _short(exc: Exception, limit: int = 200) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    return msg if len(msg) <= limit else msg[: limit - 3] + "..."


async def _safe_shutdown(manager) -> None:
    try:
        await manager.shutdown()
    except Exception:
        # Shutdown errors during reconnect are noise; the next connect() will
        # rebuild everything from scratch.
        pass
