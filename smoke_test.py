#!/usr/bin/env python3
"""Standalone connectivity smoke test — run this BEFORE trusting the menu bar.

It connects to the Toshiba cloud, lists your indoor units, and prints each
one's name, indoor temp, outdoor temp, target setpoint, mode and on/off status,
then shuts down cleanly.

Purpose:
  1. Verify your credentials work.
  2. Confirm whether YOUR specific units actually report indoor temperature —
     some only report it while powered on and return None otherwise. If a unit
     prints `indoor=None` while it's switched on, that's a real finding about
     your hardware, not a bug here.

This does NOT touch the menu-bar app or its persisted device state directory
in a destructive way — it reuses the same persisted device_id + SAS token so
it doesn't register a brand-new IoT client each run.

Usage:
    # Credentials come from Keychain or env (see credentials.py):
    export TOSHIBA_USER='you@example.com'
    export TOSHIBA_PASS='...'            # or store in Keychain
    .venv/bin/python smoke_test.py

    # Optionally wait for live push telemetry (units must be reporting):
    .venv/bin/python smoke_test.py --watch 30
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from metric_collector.config import load_device_state, save_device_state
from metric_collector.credentials import CredentialsError, resolve_credentials


def _fmt_temp(value) -> str:
    return "None" if value is None else f"{value}°C"


def _print_device(device) -> None:
    from toshiba_ac.device.properties import ToshibaAcStatus
    from toshiba_ac.utils import pretty_enum_name

    on = device.ac_status == ToshibaAcStatus.ON
    print(
        f"  • {device.name!r}\n"
        f"      power   : {'ON' if on else 'OFF'}\n"
        f"      mode    : {pretty_enum_name(device.ac_mode)}\n"
        f"      indoor  : {_fmt_temp(device.ac_indoor_temperature)}\n"
        f"      outdoor : {_fmt_temp(device.ac_outdoor_temperature)}\n"
        f"      setpoint: {_fmt_temp(device.ac_temperature)}\n"
        f"      unique  : {device.ac_unique_id}"
    )


async def run(watch_seconds: int) -> int:
    from toshiba_ac.device_manager import ToshibaAcDeviceManager

    try:
        creds = resolve_credentials()
    except CredentialsError as exc:
        print(f"[smoke] {exc}", file=sys.stderr)
        return 2

    dev_state = load_device_state()
    print(f"[smoke] using device_id suffix: {dev_state.device_id} "
          f"(have cached SAS token: {dev_state.sas_token is not None})")

    # Must be constructed inside a running loop — get_running_loop() is called
    # in the manager's __init__.
    manager = ToshibaAcDeviceManager(
        creds.username, creds.password, dev_state.device_id, dev_state.sas_token
    )

    def _on_token(token: str) -> None:
        dev_state.sas_token = token
        save_device_state(dev_state)
        print("[smoke] SAS token renewed and persisted")

    manager.on_sas_token_updated_callback.add(_on_token)

    print("[smoke] connecting…")
    try:
        token = await manager.connect()
        dev_state.sas_token = token
        save_device_state(dev_state)
        print("[smoke] connected; SAS token persisted")

        devices = await manager.get_devices()
        print(f"[smoke] found {len(devices)} device(s):")
        for device in devices:
            _print_device(device)

        if watch_seconds > 0:
            print(f"\n[smoke] watching for live push updates for {watch_seconds}s…")

            def _on_change(device) -> None:
                print(f"[smoke] update: {device.name!r} indoor="
                      f"{_fmt_temp(device.ac_indoor_temperature)}")

            for device in devices:
                device.on_state_changed_callback.add(_on_change)
            await asyncio.sleep(watch_seconds)

        return 0
    finally:
        print("[smoke] shutting down…")
        await manager.shutdown()
        print("[smoke] clean shutdown complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--watch", type=int, default=0, metavar="SECONDS",
        help="after listing, keep the connection open and print live push "
             "updates for this many seconds (0 = don't wait).",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.watch))


if __name__ == "__main__":
    raise SystemExit(main())
