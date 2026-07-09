import json
import time

from metric_collector.config import (
    Config,
    DeviceState,
    load_device_state,
    save_device_state,
)
from metric_collector.sources.dummy import DummySource
from metric_collector.state import ConnectionStatus, SharedState


def test_display_name_precedence():
    cfg = Config(room_names={"AC 1": "Living room", "dev-xyz": "By id"})
    assert cfg.display_name_for("AC 1", "dev-1") == "Living room"   # by name
    assert cfg.display_name_for("AC 9", "dev-xyz") == "By id"        # by id wins
    assert cfg.display_name_for("Unknown", "dev-9") == "Unknown"     # passthrough


def test_config_load_defaults_and_unknown_keys(tmp_path, capsys):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"unit": "F", "bogus": 1}))
    cfg = Config.load(p)
    assert cfg.unit == "F"
    assert "bogus" in capsys.readouterr().out


def test_config_missing_file_is_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nope.json")
    assert cfg.source == "dummy"


def test_poll_seconds_floor():
    assert Config(staleness_seconds=30).poll_seconds == 15.0   # floored
    assert Config(staleness_seconds=600).poll_seconds == 200.0


def test_device_state_roundtrip_and_generation(tmp_path):
    p = tmp_path / "device.json"
    s1 = load_device_state(p)               # first call generates + persists
    assert s1.device_id and s1.sas_token is None
    assert p.exists()

    s1.sas_token = "token-123"
    save_device_state(s1, p)

    s2 = load_device_state(p)               # reload keeps id + token
    assert s2.device_id == s1.device_id
    assert s2.sas_token == "token-123"
    # file perms are owner-only
    assert (p.stat().st_mode & 0o077) == 0


def test_dummy_source_populates_state():
    state = SharedState()
    src = DummySource(state, Config(), tick_seconds=0.05)
    src.start()
    try:
        # Wait for the simulated connect + first publish.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            snap = state.snapshot()
            if snap.status == ConnectionStatus.CONNECTED and snap.rooms:
                break
            time.sleep(0.05)
        snap = state.snapshot()
        assert snap.status == ConnectionStatus.CONNECTED
        assert len(snap.rooms) == 3
        # The mock mirrors a 3-head home: Living Room, Office, Bedroom.
        names = {r.display_name for r in snap.rooms}
        assert names == {"Living Room", "Office", "Bedroom"}
        # All units are powered on, so each reports a numeric indoor temp.
        assert all(r.power_on for r in snap.rooms)
        assert all(isinstance(r.indoor_temperature, float) for r in snap.rooms)
    finally:
        src.stop()
