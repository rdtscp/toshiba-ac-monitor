"""HTTP service — the microservice that holds the credentials and serves data.

This is the collector (ToshibaSource subscribe + HistoryRecorder) with a small
read-only aiohttp API bolted on. Intended to run on an always-on host (a
Raspberry Pi on your LAN); for development it runs fine on the laptop with the
dummy source.

    daemon thread:  AcSource (AMQP push) ─publish_room─► SharedState + HistoryRecorder
    main thread:    aiohttp server (read-only) reads SharedState + history.db

Endpoints:
    GET /healthz                       liveness; no auth; no sensitive data
    GET /realtime                      current per-room state (in-memory cache)
    GET /history?since=24h&device=ID   historical readings (SQLite)

Auth: a bearer token (Authorization: Bearer <token>) gates /realtime and
/history. The token comes from --token or $TOSHIBA_API_TOKEN.

Safety rails:
  * Read-only — reports control state, never actuates the AC.
  * Refuses to bind a non-loopback host without a token (so you can't expose
    your home's occupancy data to the LAN/internet unauthenticated by accident).

Run (laptop dev, dummy data, localhost):
    python -m metric_collector.server --source dummy
    curl -s localhost:8787/realtime | python -m json.tool

Run (real, on the Pi, LAN + token):
    TOSHIBA_USER=your-toshiba-username TOSHIBA_API_TOKEN=$(openssl rand -hex 32) \
        python -m metric_collector.server --source toshiba --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
from pathlib import Path
from typing import Optional

from aiohttp import web

from .config import Config
from .history import HISTORY_DB_PATH, HistoryRecorder, _parse_duration, query_readings
from .sources import build_source
from .state import SharedState, StateSnapshot

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Typed app keys (avoids aiohttp's NotAppKeyWarning and is type-safe).
STATE_KEY: "web.AppKey[SharedState]" = web.AppKey("state", SharedState)
DB_KEY: "web.AppKey[Path]" = web.AppKey("db_path", Path)
TOKEN_KEY: "web.AppKey[object]" = web.AppKey("token", object)  # Optional[str]
CORS_ORIGIN_KEY: "web.AppKey[str]" = web.AppKey("cors_origin", str)


# --- serialization -------------------------------------------------------

def _snapshot_to_dict(snap: StateSnapshot) -> dict:
    return {
        "status": snap.status.value,
        "last_update": snap.last_update,
        "rooms": [
            {
                "device_id": r.device_id,
                "display_name": r.display_name,
                "indoor_c": r.indoor_temperature,
                "outdoor_c": r.outdoor_temperature,
                "target_c": r.target_temperature,
                "power_on": r.power_on,
                "mode": r.mode,
                "updated_at": r.updated_at,
            }
            for r in snap.rooms
        ],
    }


def _reading_to_dict(r) -> dict:
    return {
        "ts": r.ts,
        "device_id": r.device_id,
        "display_name": r.display_name,
        "indoor_c": r.indoor_c,
        "outdoor_c": r.outdoor_c,
        "target_c": r.target_c,
        "power_on": r.power_on,
        "mode": r.mode,
    }


# --- middleware + handlers ----------------------------------------------

@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Allow a browser dashboard (different origin) to call the API.

    Must be the OUTERMOST middleware: a CORS preflight is an OPTIONS request
    that carries no Authorization header, so we answer it here and short-circuit
    BEFORE auth runs (otherwise auth would 401 the preflight). CORS headers are
    added to every response — including auth failures — so the browser can read
    the status.
    """
    origin = request.app[CORS_ORIGIN_KEY]
    if request.method == "OPTIONS":
        resp: web.StreamResponse = web.Response(status=204)
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return resp


@web.middleware
async def auth_middleware(request: web.Request, handler):
    # /healthz is intentionally open (liveness probes); it leaks nothing useful.
    if request.path == "/healthz":
        return await handler(request)

    token = request.app[TOKEN_KEY]
    if token:
        provided = request.headers.get("Authorization", "")
        # Constant-time compare to avoid leaking the token via timing.
        if not hmac.compare_digest(provided, f"Bearer {token}"):
            return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def healthz_handler(request: web.Request) -> web.Response:
    snap: StateSnapshot = request.app[STATE_KEY].snapshot()
    age = snap.seconds_since_update()
    return web.json_response(
        {
            "status": snap.status.value,
            "rooms": len(snap.rooms),
            "last_update_age_s": round(age, 1) if age is not None else None,
        }
    )


async def realtime_handler(request: web.Request) -> web.Response:
    snap: StateSnapshot = request.app[STATE_KEY].snapshot()
    return web.json_response(_snapshot_to_dict(snap))


async def history_handler(request: web.Request) -> web.Response:
    since = request.query.get("since", "24h")
    device = request.query.get("device")
    try:
        since_seconds = _parse_duration(since)
    except ValueError:
        return web.json_response({"error": f"bad since: {since!r}"}, status=400)

    db_path: Path = request.app[DB_KEY]
    # query_readings is blocking SQLite I/O — run it off the event loop.
    loop = asyncio.get_running_loop()
    readings = await loop.run_in_executor(
        None,
        lambda: query_readings(db_path, since_seconds=since_seconds, device_id=device),
    )
    return web.json_response([_reading_to_dict(r) for r in readings])


# --- app wiring ----------------------------------------------------------

def build_app(state: SharedState, source, db_path: Path, token: Optional[str],
              cors_origin: str = "*") -> web.Application:
    # cors_middleware MUST be outermost (first) so it answers preflight OPTIONS
    # before auth_middleware runs.
    app = web.Application(middlewares=[cors_middleware, auth_middleware])
    app[STATE_KEY] = state
    app[DB_KEY] = db_path
    app[TOKEN_KEY] = token
    app[CORS_ORIGIN_KEY] = cors_origin
    app.add_routes(
        [
            web.get("/healthz", healthz_handler),
            web.get("/realtime", realtime_handler),
            web.get("/history", history_handler),
        ]
    )

    async def _on_startup(_app: web.Application) -> None:
        source.start()

    async def _on_cleanup(_app: web.Application) -> None:
        source.stop()  # joins the daemon thread and closes the recorder

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def _quiet_upstream_noise(verbose: bool) -> None:
    """Silence the library's per-request 403 WARNINGs for endpoints we don't use.

    `GetCurrentACState` and `GetGroupACEnergyConsumption` are a poll-based BACKUP
    (and energy, which we ignore). Temperatures arrive via AMQP push, so a 403 on
    these is harmless noise — but the library logs one every cycle. We raise that
    one logger to ERROR so genuine errors still surface. --verbose-upstream opts
    back in.
    """
    if not verbose:
        logging.getLogger("toshiba_ac.utils.http_api").setLevel(logging.ERROR)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Toshiba AC HTTP microservice (read-only).")
    parser.add_argument("--source", default=os.environ.get("COLLECTOR_SOURCE", "dummy"),
                        choices=["dummy", "toshiba"],
                        help="data source (default: dummy for laptop dev)")
    parser.add_argument("--host", default=os.environ.get("API_HOST", "127.0.0.1"),
                        help="bind host (default 127.0.0.1; use 0.0.0.0 on the Pi)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("API_PORT", "8787")))
    parser.add_argument("--db", default=os.environ.get("HISTORY_DB", ""),
                        help="history.db path (default ~/.toshiba_menubar/history.db)")
    parser.add_argument("--interval", type=float,
                        default=float(os.environ.get("HISTORY_INTERVAL", "60")),
                        help="min seconds between stored samples per device")
    parser.add_argument("--retention-days", type=int,
                        default=int(os.environ.get("HISTORY_RETENTION_DAYS", "365")),
                        help="prune rows older than this; 0 = keep forever")
    parser.add_argument("--token", default=os.environ.get("TOSHIBA_API_TOKEN"),
                        help="bearer token for /realtime and /history "
                             "(env TOSHIBA_API_TOKEN)")
    parser.add_argument("--verbose-upstream", action="store_true",
                        help="don't suppress the library's 403 warnings for the "
                             "secondary HTTP poll endpoints (debugging)")
    parser.add_argument("--cors-origin", default=os.environ.get("CORS_ORIGIN", "*"),
                        help="Access-Control-Allow-Origin for the browser dashboard "
                             "(default '*'; set to the dashboard's origin to restrict)")
    args = parser.parse_args(argv)

    _quiet_upstream_noise(args.verbose_upstream)

    # Safety: never expose a non-loopback bind without a token.
    if args.host not in _LOOPBACK_HOSTS and not args.token:
        parser.error(
            f"refusing to bind {args.host} without a token. Set --token / "
            "$TOSHIBA_API_TOKEN, or bind 127.0.0.1 for local dev."
        )

    db_path = Path(args.db) if args.db else HISTORY_DB_PATH
    retention = None if args.retention_days == 0 else args.retention_days

    config = Config()
    config.source = args.source
    state = SharedState()
    recorder = HistoryRecorder(db_path, interval_seconds=args.interval,
                               retention_days=retention)
    source = build_source(args.source, state, config, recorders=[recorder])
    app = build_app(state, source, db_path, args.token, cors_origin=args.cors_origin)

    auth_state = "token required" if args.token else "NO AUTH (loopback only)"
    print(f"[server] source={args.source} bind={args.host}:{args.port} "
          f"db={db_path} auth={auth_state} cors={args.cors_origin}")
    web.run_app(app, host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
