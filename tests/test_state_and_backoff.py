from metric_collector.backoff import ExponentialBackoff
from metric_collector.state import ConnectionStatus, RoomState, SharedState


def _room(device_id="d1", name="AC 1", updated_at=1.0, indoor=20.0):
    return RoomState(
        device_id=device_id,
        toshiba_name=name,
        display_name=name,
        indoor_temperature=indoor,
        updated_at=updated_at,
    )


def test_upsert_and_snapshot_sorted_by_display_name():
    s = SharedState()
    s.upsert_room(_room("d1", name="Zulu"))
    s.upsert_room(_room("d2", name="Alpha"))
    snap = s.snapshot()
    assert [r.display_name for r in snap.rooms] == ["Alpha", "Zulu"]


def test_last_update_tracks_latest():
    s = SharedState()
    s.upsert_room(_room("d1", updated_at=10.0))
    s.upsert_room(_room("d2", updated_at=25.0))
    assert s.snapshot().last_update == 25.0


def test_mark_update_false_does_not_advance_clock():
    s = SharedState()
    s.upsert_room(_room("d1", updated_at=10.0))
    s.upsert_room(_room("d2", updated_at=99.0), mark_update=False)
    assert s.snapshot().last_update == 10.0


def test_set_display_name_in_place():
    s = SharedState()
    s.upsert_room(_room("d1", name="AC 1"))
    s.set_display_name("d1", "Kitchen")
    assert s.snapshot().rooms[0].display_name == "Kitchen"


def test_status_error_clears_on_recovery():
    s = SharedState()
    s.set_status(ConnectionStatus.ERROR, "boom")
    assert s.snapshot().last_error == "boom"
    s.set_status(ConnectionStatus.CONNECTED)
    assert s.snapshot().last_error is None


def test_seconds_since_update():
    s = SharedState()
    s.upsert_room(_room("d1", updated_at=100.0))
    assert s.snapshot().seconds_since_update(now=160.0) == 60.0


def test_backoff_grows_and_caps():
    b = ExponentialBackoff(base=2.0, factor=2.0, cap=20.0, jitter=0.0)
    assert b.next_delay() == 2.0
    assert b.next_delay() == 4.0
    assert b.next_delay() == 8.0
    assert b.next_delay() == 16.0
    assert b.next_delay() == 20.0  # capped
    assert b.next_delay() == 20.0


def test_backoff_reset():
    b = ExponentialBackoff(base=2.0, cap=20.0, jitter=0.0)
    b.next_delay()
    b.next_delay()
    b.reset()
    assert b.next_delay() == 2.0


def test_backoff_jitter_within_bounds():
    b = ExponentialBackoff(base=10.0, cap=100.0, jitter=0.2)
    d = b.next_delay(rand=1.0)  # max positive jitter
    assert 10.0 <= d <= 12.0
    b.reset()
    d = b.next_delay(rand=0.0)  # max negative jitter
    assert 8.0 <= d <= 10.0
