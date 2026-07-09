"""Toshiba AC telemetry service.

A read-only HTTP microservice + headless history collector for Toshiba
air-conditioning units, fed by Toshiba's cloud (the `toshiba-ac` PyPI package;
indoor temperatures arrive as push telemetry over Azure IoT / AMQP).

    [ daemon thread ]                        [ main thread ]
    AcSource (asyncio loop)   --writes-->  SharedState + HistoryRecorder
    Toshiba cloud / Azure IoT  (lock)          |
                                               `-- aiohttp API (read-only):
                                                   /healthz /realtime /history

Entry points:

    python -m metric_collector.server     # HTTP API + history collector
    python -m metric_collector.collector  # history capture only, no API
    python -m metric_collector.history    # query/export an existing history.db

The on-disk data dir (~/.toshiba_menubar) and the systemd unit name keep
their historical names from the toshiba-ac-menubar days, so an existing
deployment's history.db, device.json, and server.env keep working unchanged.
"""

__version__ = "0.1.0"
