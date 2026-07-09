"""Headless history collector — the cloud-deployable piece.

This is deliberately SEPARATE from the menu-bar app. It runs no UI: it connects
a source (the real Toshiba cloud source by default), attaches a HistoryRecorder
sink, and persists every reading. It's meant to run somewhere always-on and
free (a small VM / container), NOT on the laptop. The laptop menu bar stays
realtime-only and records nothing.

Because the source layer is decoupled and history is just a StateSink, the
collector reuses the exact same ToshibaSource + HistoryRecorder as everything
else — there's no separate data path to keep in sync.

Run locally against synthetic data (no creds, no cloud) to prove the pipeline:
    python -m metric_collector.collector --source dummy --db /tmp/history.db \
        --interval 2 --once-seconds 8
    python -m metric_collector.history --db /tmp/history.db --since 1h

Run for real (in the cloud):
    TOSHIBA_USER=... TOSHIBA_PASS=... \
        python -m metric_collector.collector --source toshiba --db /data/history.db

Note: the collector registers its OWN Toshiba device_id (a generated suffix in
its own app-home), so it coexists with the laptop menu bar as an independent
client under the same account — both receive push telemetry.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Optional

from .config import Config
from .history import HistoryRecorder
from .sources import build_source
from .state import ConnectionStatus, SharedState


def _quiet_upstream_noise(verbose: bool) -> None:
    """Silence the library's 403 WARNINGs for the secondary HTTP poll endpoints.

    Those endpoints (GetCurrentACState / energy) are a backup we don't rely on —
    temperatures arrive via AMQP push — so a 403 each cycle is harmless noise.
    Raise that logger to ERROR; --verbose-upstream opts back in.
    """
    if not verbose:
        logging.getLogger("toshiba_ac.utils.http_api").setLevel(logging.ERROR)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def run(
    *,
    source_name: str,
    db_path: Path,
    interval_seconds: float,
    retention_days: Optional[int],
    once_seconds: Optional[float] = None,
) -> int:
    """Start the collector and block until a signal (or --once-seconds elapses)."""
    config = Config()
    config.source = source_name

    recorder = HistoryRecorder(
        db_path, interval_seconds=interval_seconds, retention_days=retention_days
    )
    state = SharedState()
    source = build_source(source_name, state, config, recorders=[recorder])

    stop = threading.Event()

    def _handle_signal(signum, _frame) -> None:
        print(f"[collector] received signal {signum}, shutting down…")
        stop.set()

    # SIGTERM is what container orchestrators send on stop/redeploy.
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            pass  # not on the main thread, or unsupported

    print(f"[collector] starting source={source_name} db={db_path} "
          f"interval={interval_seconds}s retention={retention_days}d")
    source.start()

    deadline = (time.monotonic() + once_seconds) if once_seconds else None
    last_log = 0.0
    try:
        while not stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                print("[collector] --once-seconds elapsed, stopping")
                break
            # Lightweight heartbeat so cloud logs show liveness + status.
            now = time.monotonic()
            if now - last_log >= 30.0:
                snap = state.snapshot()
                print(f"[collector] status={snap.status.value} rooms={len(snap.rooms)} "
                      f"last_update={'never' if snap.last_update is None else 'ok'}")
                last_log = now
                if snap.status == ConnectionStatus.ERROR:
                    print(f"[collector] error: {snap.last_error}")
            stop.wait(timeout=1.0)
    finally:
        source.stop()  # also closes the recorder
        print("[collector] stopped cleanly")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Headless Toshiba AC history collector.")
    parser.add_argument(
        "--source", default=os.environ.get("COLLECTOR_SOURCE", "toshiba"),
        choices=["dummy", "toshiba"],
        help="data source (default: toshiba; use dummy to test the pipeline)",
    )
    parser.add_argument(
        "--db", default=os.environ.get("HISTORY_DB", ""),
        help="path to history.db (default: ~/.toshiba_menubar/history.db)",
    )
    parser.add_argument(
        "--interval", type=float, default=float(os.environ.get("HISTORY_INTERVAL", "60")),
        help="min seconds between stored samples per device (default 60)",
    )
    parser.add_argument(
        "--retention-days", type=int, default=_env_int("HISTORY_RETENTION_DAYS", 365),
        help="prune rows older than this; 0 = keep forever (default 365)",
    )
    parser.add_argument(
        "--once-seconds", type=float, default=None,
        help="run for N seconds then exit (for local pipeline testing)",
    )
    parser.add_argument(
        "--verbose-upstream", action="store_true",
        help="don't suppress the library's 403 warnings for the secondary HTTP "
             "poll endpoints (debugging)",
    )
    args = parser.parse_args(argv)

    _quiet_upstream_noise(args.verbose_upstream)

    if args.db:
        db_path = Path(args.db)
    else:
        from .history import HISTORY_DB_PATH

        db_path = HISTORY_DB_PATH

    retention = None if args.retention_days == 0 else args.retention_days
    return run(
        source_name=args.source,
        db_path=db_path,
        interval_seconds=args.interval,
        retention_days=retention,
        once_seconds=args.once_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
