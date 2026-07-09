"""The AcSource interface.

A source owns a background daemon thread and is the sole writer to SharedState.
The contract is intentionally tiny so the UI is fully decoupled from how data
arrives (synthetic vs. Toshiba cloud).

Lifecycle:
    src = SomeSource(state, config)
    src.start()            # spawns daemon thread, returns immediately
    ...
    src.request_refresh()  # hint: push latest cached values now (non-blocking)
    ...
    src.stop()             # signal shutdown; returns after best-effort cleanup

`request_refresh()` must NEVER block the UI thread on network I/O. For a push
source it typically just re-emits the last cached attributes; it must not
trigger a synchronous round-trip to the cloud.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, Sequence, runtime_checkable

from ..config import Config
from ..state import RoomState, SharedState


@runtime_checkable
class StateSink(Protocol):
    """A consumer of room updates on the write path.

    SharedState is the realtime cache; additional sinks (e.g. the SQLite
    HistoryRecorder) observe every published RoomState for durable storage.
    Sinks run on the source's daemon thread, never the UI thread.
    """

    def record(self, room: RoomState) -> None: ...

    def close(self) -> None: ...


class AcSource(ABC):
    def __init__(
        self,
        state: SharedState,
        config: Config,
        recorders: Sequence[StateSink] = (),
    ) -> None:
        self.state = state
        self.config = config
        self.recorders = tuple(recorders)

    def publish_room(self, room: RoomState, *, mark_update: bool = True) -> None:
        """Fan a room update out to the realtime cache and every recorder.

        This is the single write choke point. Concrete sources MUST publish
        through here rather than touching SharedState directly, so history
        capture (and any future sink) sees every sample. Recorder failures are
        isolated — a broken sink must never take down the data feed.
        """
        self.state.upsert_room(room, mark_update=mark_update)
        for recorder in self.recorders:
            try:
                recorder.record(room)
            except Exception as exc:  # never let a sink break the live feed
                print(f"[source] recorder {type(recorder).__name__} failed: {exc}")

    def close_recorders(self) -> None:
        """Flush/close all sinks. Called from stop()."""
        for recorder in self.recorders:
            try:
                recorder.close()
            except Exception as exc:
                print(f"[source] recorder {type(recorder).__name__} close failed: {exc}")

    @abstractmethod
    def start(self) -> None:
        """Spawn the background thread. Must return immediately."""

    @abstractmethod
    def stop(self) -> None:
        """Signal shutdown and best-effort join. Safe to call more than once."""

    @abstractmethod
    def request_refresh(self) -> None:
        """Non-blocking hint to re-emit the latest cached values."""
