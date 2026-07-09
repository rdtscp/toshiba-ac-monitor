# Toshiba AC dashboard

A small React dashboard for the microservice: current per-room temperature + AC
status, plus an indoor-temperature history chart.

UI stack matches the finance-dashboard repo — **MUI v5** + **`@mui/x-charts`**
(`LineChart`) + dayjs + the same muted palette. Build tool is **Vite** (not
Create React App): CRA is deprecated and breaks on Node 23+, and this runs on
Node 25. Same components, modern tooling.

## Run

```sh
cd dashboard
npm install
cp .env.example .env.local      # set VITE_API_URL + VITE_API_TOKEN
npm run dev                     # http://localhost:3000
```

`.env.local`:

```
VITE_API_URL=http://rdtscp-pi.local:8787
VITE_API_TOKEN=<the Pi's TOSHIBA_API_TOKEN>
```

> The Pi must be running the microservice with CORS enabled (it is by default,
> `Access-Control-Allow-Origin: *`). If you locked CORS down with `--cors-origin`,
> set it to this dashboard's origin (e.g. `http://localhost:3000`).

## What it shows

- **Room cards** — current indoor temp (blue when actively cooling, mirroring
  the menu bar), on/off chip, mode, setpoint, outdoor temp. Polls `/realtime`
  every 5s.
- **History chart** — indoor temperature per room over 24h / 7d / 30d, from
  `/history`. Refreshes each minute.
- **Status chip** — Connected / Stale / Disconnected from `/realtime` + last
  update age.

## Build

```sh
npm run build      # type-checks then builds to dist/
npm run preview    # serve the production build locally
```

## Security note

`VITE_API_TOKEN` is compiled into the JS bundle (it's a client app), so treat
the built dashboard as holding the token. Fine for a personal LAN tool; do not
expose it (or the API) to the internet. For remote access, use Tailscale.
