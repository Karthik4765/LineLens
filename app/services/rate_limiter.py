"""An in-process sliding-window rate limiter keyed by user id."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request


@dataclass(frozen=True)
class RateLimitResult:
    """The outcome of one rate-limit check."""

    allowed: bool
    remaining: int
    retry_after: int


class SlidingWindowRateLimiter:
    """Allow at most ``limit`` requests in any rolling ``window_seconds`` window.

    Each user gets a deque of accepted request timestamps. Expired timestamps are
    pruned on every check, so the limiter recovers without a background job.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("Rate limit and window must be positive.")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._requests: dict[int, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, user_id: int) -> RateLimitResult:
        """Record an accepted request or report how long the user must wait."""

        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._requests.setdefault(user_id, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= self.limit:
                retry_after = max(
                    1, math.ceil(timestamps[0] + self.window_seconds - now)
                )
                return RateLimitResult(
                    allowed=False, remaining=0, retry_after=retry_after
                )

            timestamps.append(now)
            return RateLimitResult(
                allowed=True,
                remaining=self.limit - len(timestamps),
                retry_after=0,
            )


def get_rate_limiter(request: Request) -> SlidingWindowRateLimiter:
    """Return the limiter stored on the application."""

    return request.app.state.rate_limiter
