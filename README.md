# Toshiba AC monitor

A **read-only HTTP microservice + headless history collector** for Toshiba
air-conditioning units. It holds the Toshiba account credentials, keeps a
single push-telemetry connection to Toshiba's cloud (via
[`toshiba-ac`](https://github.com/KaSroka/Toshiba-AC-control); Azure IoT / AMQP
under the hood), records a SQLite time-series, and serves current + historical
readings over a small authenticated API. Designed to run on an always-on host —
a Raspberry Pi on your LAN.

Two clients consume the API: the **React web dashboard** in
[`dashboard/`](dashboard/README.md) (this repo), and the macOS menu-bar app in
the companion repo
[`toshiba-ac-menubar`](https://github.com/rdtscp/toshiba-ac-menubar) (a thin
`http` client holding no credentials).

```
daemon thread:  AcSource (AMQP push) ─publish_room─► SharedState + HistoryRecorder
main thread:    aiohttp server (read-only) reads SharedState + history.db
```

## API

| endpoint | auth | returns |
|----------|------|---------|
| `GET /healthz` | none | liveness: status, room count, data age (no sensitive data) |
| `GET /realtime` | bearer | current per-room state, from the in-memory cache |
| `GET /history?since=24h&device=ID` | bearer | historical readings from SQLite |

Auth is a bearer token (`Authorization: Bearer <token>`) from `--token` /
`$TOSHIBA_API_TOKEN`, compared in constant time.

Safety rails:

- **Read-only** — reports control state, never actuates the AC.
- **Refuses to bind a non-loopback host without a token**, so you can't
  accidentally expose your home's occupancy data unauthenticated.
- **LAN only by design** — don't port-forward it; use Tailscale for remote
  access (see [`deploy/README.md`](deploy/README.md)).

## Quickstart (synthetic data, no credentials)

Python 3.11–3.13 (**not 3.14** — the Azure IoT SDK hangs in `connect()` there):

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m metric_collector.server --source dummy
curl -s localhost:8787/realtime | python3 -m json.tool
```

## Running against real units

Credentials come from env vars (never written to disk by this app):

```sh
export TOSHIBA_USER="your-toshiba-username"
export TOSHIBA_PASS="..."
```

Smoke-test the cloud connection first — it connects, lists your units with
temperatures, and shuts down cleanly:

```sh
.venv/bin/python smoke_test.py
.venv/bin/python smoke_test.py --watch 30    # also print live push updates
```

Then run the server for real (a non-loopback bind requires a token):

```sh
TOSHIBA_API_TOKEN=$(openssl rand -hex 32) \
    .venv/bin/python -m metric_collector.server --source toshiba --host 0.0.0.0
```

Production deployment (systemd on a Raspberry Pi, hardened unit, env file) is
step-by-step in [`deploy/README.md`](deploy/README.md).

### What gets persisted

`~/.toshiba_menubar/` (created `0700`):

- `device.json` (`0600`) — a generated client `device_id` suffix + the SAS
  token returned by `connect()`, so the service doesn't re-register a new IoT
  client on every launch (the cloud dislikes that).
- `history.db` — the SQLite time-series (WAL mode).

## History without the API

The collector captures history with no HTTP server; the history module is also
a query/export CLI:

```sh
# Prove the pipeline locally on dummy data (no cloud, no creds):
.venv/bin/python -m metric_collector.collector --source dummy --db /tmp/history.db \
    --interval 2 --once-seconds 8
.venv/bin/python -m metric_collector.history --db /tmp/history.db --since 1h
.venv/bin/python -m metric_collector.history --db /tmp/history.db --since 1h --csv
```

Design notes (the sink seam, two-clients-one-account, cloud hosting options)
are in [`docs/history-and-cloud-collector.md`](docs/history-and-cloud-collector.md).

## Web dashboard

A React (Vite + MUI) dashboard for the API — room cards + temperature history
chart — lives in [`dashboard/`](dashboard/README.md):

```sh
cd dashboard
npm install
cp .env.example .env.local   # set VITE_API_URL + VITE_API_TOKEN
npm run dev                  # http://localhost:3000
```

## Layout

| file | responsibility |
|------|----------------|
| `metric_collector/server.py` | aiohttp API + auth/CORS middleware; wires source + recorder |
| `metric_collector/collector.py` | headless capture loop (no API), SIGTERM-clean |
| `metric_collector/history.py` | SQLite recorder (`StateSink`) + query/export CLI |
| `metric_collector/sources/toshiba.py` | real Toshiba cloud / Azure IoT source |
| `metric_collector/sources/dummy.py` | synthetic source (default; no network) |
| `metric_collector/sources/base.py` | the tiny `AcSource` interface |
| `metric_collector/state.py` | lock-guarded `SharedState` + immutable snapshots |
| `metric_collector/config.py` | `~/.toshiba_menubar` paths + device-state persistence |
| `metric_collector/credentials.py` | env-var (and, if present, keyring) credential resolution |
| `metric_collector/backoff.py` | exponential backoff with jitter |

> **Why is the data dir called `~/.toshiba_menubar`?** It's historical — this
> code was extracted from the combined `toshiba-ac-menubar` repo. Keeping the
> data dir and the systemd unit name means an existing install's `history.db`,
> `device.json`, and `server.env` keep working unchanged.

## Development

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The network-free logic (server API, history, state, config, backoff, dummy
source) is covered by tests; the real Toshiba source is verified by hand via
`smoke_test.py`.
