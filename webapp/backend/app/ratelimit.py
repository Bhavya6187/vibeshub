"""In-memory sliding-window rate limiter.

Per-process and deliberately simple (the spec's v1 rate limiting is
in-memory). Keys are pruned lazily on access; a hard cap on tracked keys
prevents unbounded growth from IP churn.
"""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from typing import Callable, Deque


class SlidingWindowLimiter:
    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 4096,
    ) -> None:
        self._max = max_events
        self._window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._events: OrderedDict[str, Deque[float]] = OrderedDict()

    def allow(self, key: str) -> bool:
        """True if the event fits in the window; True consumes a slot."""
        now = self._clock()
        q = self._events.get(key)
        if q is None:
            q = deque()
            self._events[key] = q
            while len(self._events) > self._max_keys:
                self._events.popitem(last=False)
        self._events.move_to_end(key)
        cutoff = now - self._window
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= self._max:
            return False
        q.append(now)
        return True
