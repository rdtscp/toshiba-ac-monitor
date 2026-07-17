"""Configuration and on-disk paths.

Everything user-specific or secret lives under ~/.toshiba_menubar (the app's
"home"), NEVER in the repo:

    ~/.toshiba_menubar/
        config.json        # user settings + room-name remap (gitignored anyway)
        device.json        # generated client device_id + persisted SAS token
        device.json is written by the real Toshiba source so we don't
        re-register a new IoT device on every launch.

config.json is optional here: the server and collector run on flags + env vars.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# Overridable so a second collector (e.g. flat-monitor embedding this as a
# library) keeps its own device.json — two processes sharing one Azure IoT
# client identity kick each other off in a reconnect fight.
APP_HOME = Path(os.path.expanduser(os.environ.get("TOSHIBA_APP_HOME", "~/.toshiba_menubar")))
CONFIG_PATH = APP_HOME / "config.json"
DEVICE_STATE_PATH = APP_HOME / "device.json"


def ensure_app_home() -> Path:
    """Create ~/.toshiba_menubar with tight permissions if it doesn't exist."""
    APP_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Re-assert perms in case it was created with a looser umask previously.
    try:
        APP_HOME.chmod(0o700)
    except OSError:
        pass
    return APP_HOME


@dataclass
class Config:
    """Runtime configuration.

    `room_names` maps a Toshiba device name (or device id) to the label you
    actually want shown — the Toshiba app's names are often unhelpful.
    """

    # Mark the title stale once data is older than this many seconds.
    staleness_seconds: int = 300
    # Temperature unit shown in the UI. "C" or "F" (display only; the cloud
    # reports Celsius — Fahrenheit is converted at render time).
    unit: str = "C"
    # device-name-or-id -> preferred display name.
    room_names: dict[str, str] = field(default_factory=dict)
    # Which data source to run:
    #   "dummy"   — synthetic data, no network (default)
    #   "toshiba" — direct Toshiba cloud connection (holds credentials)
    #   "http"    — thin client: poll a microservice's /realtime endpoint
    source: str = "dummy"
    # How often the UI re-renders from cached state, in seconds.
    ui_refresh_seconds: float = 5.0
    # For source="http": base URL of the microservice (e.g. the Raspberry Pi)
    # and its bearer token. The token is read from config OR the API_TOKEN env
    # var (env wins) so it needn't sit in a plaintext config file.
    api_url: Optional[str] = None
    api_token: Optional[str] = None

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        """Load config from disk, falling back to defaults for any missing key.

        A missing file is fine (returns defaults). A malformed file raises, so
        the user notices rather than silently running on defaults.
        """
        if not path.exists():
            return cls()
        raw: dict[str, Any] = json.loads(path.read_text())
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = set(raw) - known
        if unknown:
            # Don't crash, but surface it — typos in config are a common footgun.
            print(f"[config] ignoring unknown keys: {sorted(unknown)}")
        return cls(**{k: v for k, v in raw.items() if k in known})

    @property
    def poll_seconds(self) -> float:
        """How often the real source re-publishes cached device attributes."""
        return max(15.0, self.staleness_seconds / 3)

    @property
    def resolved_api_url(self) -> Optional[str]:
        """Base URL for source='http'. Env API_URL overrides config."""
        return os.environ.get("API_URL") or self.api_url

    @property
    def resolved_api_token(self) -> Optional[str]:
        """Bearer token for source='http'. Env API_TOKEN overrides config."""
        return os.environ.get("API_TOKEN") or self.api_token

    def display_name_for(self, toshiba_name: str, device_id: str) -> str:
        """Resolve the label to show for a unit.

        Precedence: exact device_id match > exact Toshiba-name match > the
        Toshiba name unchanged.
        """
        if device_id in self.room_names:
            return self.room_names[device_id]
        if toshiba_name in self.room_names:
            return self.room_names[toshiba_name]
        return toshiba_name


# --- persisted device registration (real source only) --------------------
#
# ToshibaAcDeviceManager(device_id=...) takes a SHORT id suffix; internally it
# becomes f"{username}_{device_id}". The default suffix is a hardcoded constant
# shared by every install, so we generate our own random suffix once and reuse
# it, alongside the SAS token returned by connect(). Persisting both means we
# don't re-register a new IoT client on every launch (which the cloud dislikes).


@dataclass
class DeviceState:
    device_id: str            # our generated short suffix
    sas_token: Optional[str]  # last token from connect()/renewal, if any


def load_device_state(path: Path = DEVICE_STATE_PATH) -> DeviceState:
    """Load (or first-time generate) the persisted device id + SAS token.

    If no state exists, a fresh random device_id is generated and written
    immediately so it's stable across runs even before the first connect.
    """
    ensure_app_home()
    if path.exists():
        raw = json.loads(path.read_text())
        device_id = raw.get("device_id") or _new_device_id()
        return DeviceState(device_id=device_id, sas_token=raw.get("sas_token"))

    state = DeviceState(device_id=_new_device_id(), sas_token=None)
    save_device_state(state, path)
    return state


def save_device_state(state: DeviceState, path: Path = DEVICE_STATE_PATH) -> None:
    ensure_app_home()
    payload = json.dumps({"device_id": state.device_id, "sas_token": state.sas_token})
    # Write with 0o600 and atomically-ish replace.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload)
    tmp.chmod(0o600)
    tmp.replace(path)


def _new_device_id() -> str:
    # 16 hex chars, matching the length of the library's default suffix.
    return secrets.token_hex(8)
