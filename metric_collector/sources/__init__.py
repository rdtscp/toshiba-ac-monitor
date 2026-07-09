"""Data sources that populate SharedState on a background thread.

A source is whatever feeds telemetry into the service. There are two:

    DummySource    - synthetic data, no network. The default; lets the whole
                     service run and be reviewed without any credentials.
    ToshibaSource  - the real thing: Toshiba cloud + Azure IoT push telemetry.

Both implement AcSource (see base.py) and run their own background daemon
thread. The consumers never know or care which one is active.
"""

from .base import AcSource

__all__ = ["AcSource", "build_source"]


def build_source(name: str, state, config, recorders=()) -> AcSource:
    """Construct the configured source by name.

    "dummy"   -> synthetic data, no network (default).
    "toshiba" -> real Toshiba cloud + Azure IoT.

    `recorders` are StateSinks (e.g. HistoryRecorder) the source fans every
    reading out to. The server and collector pass a HistoryRecorder.

    The toshiba source is imported lazily so the default path doesn't pull in
    azure-iot-device.
    """
    if name == "dummy":
        from .dummy import DummySource

        return DummySource(state, config, recorders=recorders)
    if name == "toshiba":
        from .toshiba import ToshibaSource

        return ToshibaSource(state, config, recorders=recorders)
    raise ValueError(f"Unknown source {name!r} (expected 'dummy' or 'toshiba')")
