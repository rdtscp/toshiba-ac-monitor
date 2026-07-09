"""Exponential backoff with jitter, for reconnect loops.

Toshiba's cloud rate-limits aggressive reconnects, so the real source must back
off when connect() fails. Kept here, dependency-free, so it can be unit-tested
and reused.

Usage:
    backoff = ExponentialBackoff(base=2.0, cap=300.0)
    while not stop.is_set():
        try:
            connect()
            backoff.reset()
            run_until_disconnect()
        except Exception:
            stop.wait(backoff.next_delay())
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExponentialBackoff:
    base: float = 2.0          # first delay, seconds
    factor: float = 2.0        # multiplier per failure
    cap: float = 300.0         # max delay, seconds (~5 min per the spec)
    jitter: float = 0.2        # +/- fraction of randomised jitter

    _attempt: int = 0

    def reset(self) -> None:
        self._attempt = 0

    def next_delay(self, *, rand: float | None = None) -> float:
        """Return the next delay and advance the attempt counter.

        `rand` (0..1) is injectable for deterministic tests; in production it's
        derived from the attempt count to avoid importing `random` here and to
        keep this pure. The jitter is small and only exists to de-synchronise
        many clients, so a cheap deterministic source is fine.
        """
        raw = self.base * (self.factor ** self._attempt)
        delay = min(raw, self.cap)
        self._attempt += 1

        if self.jitter <= 0:
            return delay
        # Cheap, dependency-free pseudo-jitter in [-jitter, +jitter].
        r = rand if rand is not None else ((self._attempt * 0.6180339887) % 1.0)
        offset = (r * 2 - 1) * self.jitter
        return max(0.0, delay * (1 + offset))
