import tempfile
import time
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase

from metric_collector.history import HistoryRecorder
from metric_collector.server import build_app
from metric_collector.state import ConnectionStatus, RoomState, SharedState


class _NoopSource:
    """Stand-in source so the app's startup/cleanup hooks have something to call."""

    def start(self): pass
    def stop(self): pass
    def request_refresh(self): pass


def _room(device_id="d1", name="Living Room", indoor=22.0, on=True, ts=1000.0):
    return RoomState(
        device_id=device_id, toshiba_name=name, display_name=name,
        indoor_temperature=indoor, outdoor_temperature=30.0, target_temperature=21.0,
        power_on=on, mode="cooling", updated_at=ts,
    )


class _Base(AioHTTPTestCase):
    TOKEN = "secret123"

    async def get_application(self):
        self.state = SharedState()
        self.state.set_status(ConnectionStatus.CONNECTED)
        self.state.upsert_room(_room())  # fixed ts=1000.0 for the realtime assertion
        self.db = Path(tempfile.mkdtemp()) / "h.db"
        # History rows use recent timestamps so the ?since= time filter includes them.
        now = time.time()
        rec = HistoryRecorder(self.db, interval_seconds=1, retention_days=None)
        rec.record(_room(ts=now - 2))
        rec.record(_room(indoor=22.5, ts=now))  # +2s > interval -> second row lands
        rec.close()
        return build_app(self.state, _NoopSource(), self.db, token=self.TOKEN)

    def _auth(self):
        return {"Authorization": f"Bearer {self.TOKEN}"}


class TestAuth(_Base):
    async def test_healthz_is_open(self):
        resp = await self.client.get("/healthz")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "connected" and body["rooms"] == 1

    async def test_realtime_requires_token(self):
        assert (await self.client.get("/realtime")).status == 401
        assert (await self.client.get("/realtime", headers={"Authorization": "Bearer wrong"})).status == 401
        assert (await self.client.get("/realtime", headers=self._auth())).status == 200

    async def test_history_requires_token(self):
        assert (await self.client.get("/history")).status == 401
        assert (await self.client.get("/history", headers=self._auth())).status == 200


class TestPayloads(_Base):
    async def test_realtime_shape(self):
        resp = await self.client.get("/realtime", headers=self._auth())
        data = await resp.json()
        assert data["status"] == "connected"
        assert data["last_update"] == 1000.0
        room = data["rooms"][0]
        assert room["display_name"] == "Living Room"
        assert room["indoor_c"] == 22.0
        assert room["power_on"] is True

    async def test_history_shape_and_filter(self):
        resp = await self.client.get("/history?since=1h", headers=self._auth())
        rows = await resp.json()
        assert len(rows) == 2
        assert {r["device_id"] for r in rows} == {"d1"}
        assert rows[0]["indoor_c"] in (22.0, 22.5)

        # Unknown device -> empty.
        resp2 = await self.client.get("/history?device=nope", headers=self._auth())
        assert await resp2.json() == []

    async def test_history_bad_since(self):
        resp = await self.client.get("/history?since=bogus", headers=self._auth())
        assert resp.status == 400


class TestCors(_Base):
    async def test_preflight_options_no_auth_needed(self):
        # Preflight carries no Authorization header; must NOT 401.
        resp = await self.client.options("/realtime")
        assert resp.status == 204
        assert resp.headers["Access-Control-Allow-Origin"] == "*"
        assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]

    async def test_cors_header_on_get(self):
        resp = await self.client.get("/healthz")
        assert resp.headers["Access-Control-Allow-Origin"] == "*"

    async def test_cors_header_even_on_401(self):
        resp = await self.client.get("/realtime")  # no token
        assert resp.status == 401
        assert resp.headers["Access-Control-Allow-Origin"] == "*"


class TestNoTokenMode(AioHTTPTestCase):
    """With no token configured (loopback dev), endpoints are open."""

    async def get_application(self):
        state = SharedState()
        state.set_status(ConnectionStatus.CONNECTED)
        db = Path(tempfile.mkdtemp()) / "h.db"
        HistoryRecorder(db, retention_days=None).close()
        return build_app(state, _NoopSource(), db, token=None)

    async def test_open_without_token(self):
        assert (await self.client.get("/realtime")).status == 200
