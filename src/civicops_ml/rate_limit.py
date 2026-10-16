"""Bounded sliding-window limits for local and horizontally scaled runtimes."""

from __future__ import annotations

import threading
import time
from hashlib import sha256
from math import ceil
from collections import defaultdict, deque
from collections.abc import Callable

from psycopg_pool import NullConnectionPool


class SlidingWindowRateLimiter:
    """Enforce bounded actions without retaining request payloads."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate-limit values must be positive")
        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0])) + 1)
                return False, retry_after
            events.append(now)
            return True, 0


class PostgresSlidingWindowRateLimiter:
    """Serialize each hashed key across instances using PostgreSQL row locks."""

    def __init__(self, pool: NullConnectionPool) -> None:
        self.pool = pool

    def check(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate-limit values must be positive")
        key_hash = sha256(key.encode("utf-8")).hexdigest()
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO civicops.rate_limits (key_sha256) VALUES (%s) "
                "ON CONFLICT (key_sha256) DO NOTHING",
                (key_hash,),
            )
            previous = connection.execute(
                "SELECT event_times FROM civicops.rate_limits "
                "WHERE key_sha256 = %s FOR UPDATE",
                (key_hash,),
            ).fetchone()[0]
            now = float(
                connection.execute(
                    "SELECT EXTRACT(EPOCH FROM clock_timestamp())"
                ).fetchone()[0]
            )
            events = [event for event in previous if event > now - window_seconds]
            if len(events) >= limit:
                return False, max(1, ceil(events[0] + window_seconds - now))
            events.append(now)
            connection.execute(
                "UPDATE civicops.rate_limits SET event_times = %s, "
                "expires_at = to_timestamp(%s) WHERE key_sha256 = %s",
                (events, now + window_seconds, key_hash),
            )
        return True, 0

    def cleanup(self) -> None:
        """Remove inactive keys without recording user identities or payloads."""
        with self.pool.connection() as connection:
            connection.execute(
                "DELETE FROM civicops.rate_limits "
                "WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '1 day'"
            )
