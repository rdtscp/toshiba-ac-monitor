> **Note:** this doc came from the original combined repo. The menu-bar client it
> references lives in [`toshiba-ac-menubar`](https://github.com/rdtscp/toshiba-ac-menubar);
> the collector/server described here is THIS repo.

# History capture & the cloud collector

Two independent processes share one codebase:

| process | where it runs | UI | records history? |
|---------|---------------|----|------------------|
| **menu bar** (`python -m toshiba_menubar`) | your laptop | rumps | **no** — realtime only |
| **collector** (`python -m metric_collector.collector`) | a free always-on cloud host | none | **yes** — SQLite time-series |

They're separate on purpose: you didn't want history collection running on the
laptop, and history only matters when it's captured continuously — which means
it belongs on something that's always on.

## How it reuses the same code (no rework)

History is a `StateSink` on the write path. Every source publishes readings via
`AcSource.publish_room()`, which fans out to:

- `SharedState` (the realtime cache the menu bar reads), and
- any registered sinks — the collector registers a `HistoryRecorder`.

The menu bar passes **no** sinks. The collector passes a `HistoryRecorder`.
Same `ToshibaSource`, same telemetry path — only the consumer set differs.

```
ToshibaSource ─ publish_room(room) ─┬─► SharedState        (collector ignores)
                                    └─► HistoryRecorder ──► history.db (SQLite/WAL)
```

## Two Toshiba clients, one account

The collector registers its **own** generated `device_id` (in its own
`~/.toshiba_menubar/device.json` on the cloud host), so it's an independent
Azure IoT client from the laptop menu bar. Both receive push telemetry under the
same Toshiba account. (If the cloud ever objects to two clients, the collector
is the one that should "win" — point the menu bar at a read path instead; see
Open questions.)

## Test the pipeline locally (no cloud, no creds)

```sh
python -m metric_collector.collector --source dummy --db /tmp/history.db \
    --interval 2 --once-seconds 8
python -m metric_collector.history --db /tmp/history.db --since 1h
python -m metric_collector.history --db /tmp/history.db --since 1h --csv > temps.csv
```

## Running for real

```sh
TOSHIBA_USER=you@example.com TOSHIBA_PASS=... \
    python -m metric_collector.collector --source toshiba --db /data/history.db
```

Config via flags or env: `COLLECTOR_SOURCE`, `HISTORY_DB`, `HISTORY_INTERVAL`,
`HISTORY_RETENTION_DAYS`. The collector handles `SIGTERM` (what container hosts
send on redeploy) and shuts the recorder down cleanly.

## Cloud deployment — the open decision

The collector needs a **persistent process** (it holds a long-lived Azure IoT
connection), which rules out request-driven serverless (Vercel/Lambda/Cloud Run
scale-to-zero). The two axes to decide:

### Compute (must be always-on, free)
- **Fly.io** — a single tiny always-on machine fits the free allowance; simple
  `fly deploy` from a Dockerfile; supports a small persistent volume.
- **Oracle Cloud Always Free** — a genuinely always-on micro VM (generous, but
  more setup: you manage the box, systemd unit, updates).
- **Railway / Render** — easy, but free tiers sleep or are time-limited; a
  sleeping collector misses telemetry, so these are weaker fits.

### Storage (must survive redeploys)
- **SQLite on a persistent volume** (Fly volume / Oracle disk) — simplest; the
  current `HistoryRecorder` works unchanged.
- **Hosted free Postgres** (Neon / Supabase) — survives even ephemeral compute,
  queryable from anywhere, but needs a second `StateSink` implementation
  (`PostgresRecorder`). The sink seam already supports this — it's an additive
  class, not a rewrite.

Recommended starting point: **Fly.io + SQLite on a Fly volume** — least moving
parts, keeps today's recorder as-is, and is enough to prove value. Switch the
storage to Neon/Supabase later (new sink, no architecture change) if you want a
viewer that reads the DB directly without touching the collector host.

> Not built yet — deliberately deferred until the basics are reviewed and the
> smoke test confirms real indoor temperatures. When you pick compute + storage,
> the remaining work is a Dockerfile + a deploy config (+ a `PostgresRecorder`
> only if you choose hosted Postgres).
