"""Small in-process abuse controls for the single-process V1 runtime."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._expires: dict[str, float] = {}
        self._next_cleanup = 0.0
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> int | None:
        """Record an event and return retry seconds when the limit is exceeded."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            # Expire identities that never return; pruning only the current key
            # otherwise retains every historic IP/token for the process lifetime.
            if now >= self._next_cleanup:
                for expired in [key for key, deadline in self._expires.items() if deadline <= now]:
                    self._events.pop(expired, None)
                    del self._expires[expired]
                self._next_cleanup = now + 60
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return max(1, int(window_seconds - (now - events[0])))
            events.append(now)
            self._expires[key] = max(self._expires.get(key, 0), now + window_seconds)
            return None

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._expires.clear()
            self._next_cleanup = 0.0
