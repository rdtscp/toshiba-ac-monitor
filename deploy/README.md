# Deploying the microservice on a Raspberry Pi

This runs the **read-only HTTP microservice** (`metric_collector.server`) as a
systemd service: it holds the Toshiba credentials, keeps the single push
connection to Toshiba, records history, and serves `/realtime` + `/history` on
your LAN. Clients — the macOS menu bar and web dashboard from
[`toshiba-ac-menubar`](https://github.com/rdtscp/toshiba-ac-menubar), or plain
`curl` — talk to this API and hold no Toshiba credentials.

## 1. Prerequisites on the Pi

**Python:** the only hard rule is **not 3.14** (the Azure IoT SDK hangs in
`connect()` there). 3.11, 3.12, and 3.13 all work — so just use the Pi's system
Python if it's one of those. Raspberry Pi OS (Bookworm) ships 3.11, which is
fine.

```sh
python3 --version          # anything 3.11–3.13 is good; avoid 3.14
sudo apt-get update
sudo apt-get install -y python3-venv git
# If pip has to BUILD azure-iot-device (no prebuilt aarch64 wheel for the rc),
# it needs a compiler + headers:
sudo apt-get install -y build-essential libssl-dev libffi-dev python3-dev
```

## 2. Clone + venv

Paths below assume user `pi` and `/home/pi/toshiba-ac-monitor`. Adjust the
`.service` file if you use a different user or location.

```sh
cd /home/pi
git clone https://github.com/rdtscp/toshiba-ac-monitor.git
cd toshiba-ac-monitor
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

## 3. Credentials + API token

```sh
mkdir -p /home/pi/.toshiba_menubar
cp deploy/toshiba-menubar.env.example /home/pi/.toshiba_menubar/server.env
# Edit it: set TOSHIBA_USER, TOSHIBA_PASS and a real TOSHIBA_API_TOKEN.
#   token:  openssl rand -hex 32
chmod 600 /home/pi/.toshiba_menubar/server.env
```

## 4. Smoke test before installing the service

Confirm credentials + connectivity once, by hand (it connects, lists units,
shuts down):

```sh
set -a; . /home/pi/.toshiba_menubar/server.env; set +a
.venv/bin/python smoke_test.py
```

If that prints your units, you're good. (If you hit HTTP 403, you're being
rate-limited — wait ~30 min and avoid repeated reconnects.)

## 5. Install the systemd unit

```sh
sudo cp deploy/toshiba-menubar.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now toshiba-menubar.service
```

> The unit is still named `toshiba-menubar.service` (and the data dir
> `~/.toshiba_menubar`) — kept from the original combined repo so existing
> installs upgrade in place.

Check it:

```sh
systemctl status toshiba-menubar.service
journalctl -u toshiba-menubar.service -f      # live logs

# From the Pi or another LAN machine (replace TOKEN + pi-host):
curl -s http://PI-HOST:8787/healthz
curl -s -H "Authorization: Bearer TOKEN" http://PI-HOST:8787/realtime | python3 -m json.tool
```

## 6. Point clients at the Pi

The macOS menu bar (from the companion repo) runs as a thin client with no
credentials — on the Mac, put this in `~/.toshiba_menubar/config.json`:

```json
{
  "source": "http",
  "api_url": "http://PI-HOST:8787",
  "api_token": "the-same-TOSHIBA_API_TOKEN"
}
```

(Or keep the token out of the file and pass it via the `API_TOKEN` env var.)
See [`toshiba-ac-menubar`](https://github.com/rdtscp/toshiba-ac-menubar) for
the menu bar setup.

The **React dashboard** lives in this repo — it's configured the same way:
`VITE_API_URL` + `VITE_API_TOKEN` in `dashboard/.env.local`. See
[`dashboard/README.md`](../dashboard/README.md).

## Migrating an existing Pi install from `toshiba-ac-menubar`

If the Pi is already running the service from the old combined repo:

```sh
cd /home/pi
git clone https://github.com/rdtscp/toshiba-ac-monitor.git
cd toshiba-ac-monitor
python3 -m venv .venv
.venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt
sudo cp deploy/toshiba-menubar.service /etc/systemd/system/   # now points at /home/pi/toshiba-ac-monitor
sudo systemctl daemon-reload
sudo systemctl restart toshiba-menubar.service
```

Nothing under `/home/pi/.toshiba_menubar/` moves — `server.env`, `history.db`,
and `device.json` are reused as-is (same env vars, same unit name, same data
dir). Once it's healthy, remove the old clone:
`rm -rf /home/pi/toshiba-ac-menubar`.

## Operations

```sh
sudo systemctl restart toshiba-menubar.service   # after a code update (git pull)
sudo systemctl stop toshiba-menubar.service
sudo systemctl disable toshiba-menubar.service   # stop starting at boot
journalctl -u toshiba-menubar.service --since "1 hour ago"
```

## Notes / decisions

- **LAN only.** The service binds `0.0.0.0` but you should NOT port-forward it
  to the internet. For remote access, use Tailscale (encrypted, no port
  forwarding, no certs) rather than opening your router.
- **No TLS here.** Fine on a trusted home LAN with the bearer token. Put it
  behind a reverse proxy (or Tailscale) if you want encryption.
- **Hardening.** The unit runs unprivileged with `ProtectSystem=strict`,
  `ProtectHome=read-only`, and a single `ReadWritePaths` for its data dir
  (`~/.toshiba_menubar`, which holds `device.json` + `history.db`).
- **History DB** lives at `/home/pi/.toshiba_menubar/history.db`. Back it up if
  you care about the long-term series.
