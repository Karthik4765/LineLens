"""Unit tests for the custom sliding-window rate limiter."""

from __future__ import annotations

from app.services.rate_limiter import SlidingWindowRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_limiter(limit: int = 10, window: int = 60):
    clock = FakeClock()
    return SlidingWindowRateLimiter(limit, window, clock=clock), clock


def test_allows_requests_up_to_the_limit_then_blocks():
    limiter, _ = make_limiter(limit=3)

    first = limiter.check(user_id=1)
    second = limiter.check(user_id=1)
    third = limiter.check(user_id=1)
    blocked = limiter.check(user_id=1)

    assert first.allowed and first.remaining == 2
    assert second.allowed and second.remaining == 1
    assert third.allowed and third.remaining == 0
    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.retry_after == 60


def test_window_recovers_after_oldest_request_expires():
    limiter, clock = make_limiter(limit=2, window=60)

    assert limiter.check(1).allowed
    assert limiter.check(1).allowed
    assert limiter.check(1).allowed is False

    clock.advance(60)

    recovered = limiter.check(1)
    assert recovered.allowed is True
    assert recovered.remaining == 1


def test_users_have_separate_windows():
    limiter, _ = make_limiter(limit=1)

    assert limiter.check(user_id=1).allowed
    assert limiter.check(user_id=1).allowed is False
    assert limiter.check(user_id=2).allowed is True


def test_retry_after_counts_down_as_window_moves():
    limiter, clock = make_limiter(limit=1, window=60)
    assert limiter.check(1).allowed

    clock.advance(45)
    blocked = limiter.check(1)

    assert blocked.allowed is False
    assert blocked.retry_after == 15
