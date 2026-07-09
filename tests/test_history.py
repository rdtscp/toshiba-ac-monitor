from metric_collector.history import HistoryRecorder, query_readings, _parse_duration
from metric_collector.state import RoomState


def _room(ts, *, device_id="d1", indoor=20.0, power_on=True, mode="cooling"):
    return RoomState(
        device_id=device_id,
        toshiba_name="AC 1",
        display_name="Living room",
        indoor_temperature=indoor,
        outdoor_temperature=30.0,
        target_temperature=22.0,
        power_on=power_on,
        mode=mode,
        updated_at=ts,
    )


def test_throttle_limits_samples(tmp_path):
    db = tmp_path / "h.db"
    rec = HistoryRecorder(db, interval_seconds=60, retention_days=None)
    # First write always lands; subsequent writes within the interval are dropped.
    rec.record(_room(1000.0, indoor=20.0))
    rec.record(_room(1010.0, indoor=20.1))  # +10s < 60s, no state change -> skip
    rec.record(_room(1075.0, indoor=20.2))  # +75s -> write
    rec.close()

    rows = query_readings(db, since_seconds=None)
    assert [r.indoor_c for r in rows] == [20.0, 20.2]


def test_state_change_forces_write_within_interval(tmp_path):
    db = tmp_path / "h.db"
    rec = HistoryRecorder(db, interval_seconds=3600, retention_days=None)
    rec.record(_room(1000.0, power_on=True, mode="cooling"))
    rec.record(_room(1005.0, power_on=False, mode=None))   # power change -> write
    rec.record(_room(1006.0, power_on=False, mode="heating"))  # mode change -> write
    rec.close()

    rows = query_readings(db, since_seconds=None)
    assert [r.power_on for r in rows] == [True, False, False]
    assert len(rows) == 3


def test_none_indoor_stored_as_null(tmp_path):
    db = tmp_path / "h.db"
    rec = HistoryRecorder(db, interval_seconds=1, retention_days=None)
    rec.record(_room(1000.0, indoor=None, power_on=False, mode=None))
    rec.close()

    rows = query_readings(db, since_seconds=None)
    assert len(rows) == 1
    assert rows[0].indoor_c is None
    assert rows[0].power_on is False


def test_retention_prunes_old_rows(tmp_path):
    db = tmp_path / "h.db"
    rec = HistoryRecorder(db, interval_seconds=1, retention_days=7)
    old = 1_000_000.0
    rec.record(_room(old, device_id="d1"))
    # A much later write triggers a prune (>1h since last prune) that drops the old row.
    recent = old + 30 * 86400
    rec.record(_room(recent, device_id="d2"))
    rec.close()

    rows = query_readings(db, since_seconds=None)
    assert all(r.ts >= recent - 7 * 86400 for r in rows)
    assert [r.device_id for r in rows] == ["d2"]


def test_query_filters_by_device_and_window(tmp_path):
    db = tmp_path / "h.db"
    rec = HistoryRecorder(db, interval_seconds=1, retention_days=None)
    rec.record(_room(1000.0, device_id="d1"))
    rec.record(_room(1001.0, device_id="d2"))
    rec.close()

    only_d1 = query_readings(db, since_seconds=None, device_id="d1")
    assert [r.device_id for r in only_d1] == ["d1"]

    # since window: now=1002, since 1.5s -> only ts>=1000.5 (the d2 row).
    windowed = query_readings(db, since_seconds=1.5, now=1002.0)
    assert [r.device_id for r in windowed] == ["d2"]


def test_db_file_is_owner_only(tmp_path):
    db = tmp_path / "h.db"
    rec = HistoryRecorder(db, interval_seconds=1, retention_days=None)
    rec.close()
    assert (db.stat().st_mode & 0o077) == 0


def test_parse_duration():
    assert _parse_duration("24h") == 86400
    assert _parse_duration("7d") == 7 * 86400
    assert _parse_duration("90m") == 5400
    assert _parse_duration("30s") == 30
    assert _parse_duration("120") == 120
