"""
Tests for the Expert API sliding-window rate limiter.
"""

import pytest

from app.expert.rate_limiter import RateLimitExceededError, SlidingWindowRateLimiter


def test_allows_calls_within_limit():
    limiter = SlidingWindowRateLimiter(max_calls=5, window=60)
    for _ in range(5):
        limiter.check_and_record()  # Should not raise


def test_raises_when_limit_exceeded():
    limiter = SlidingWindowRateLimiter(max_calls=3, window=60)
    for _ in range(3):
        limiter.check_and_record()
    with pytest.raises(RateLimitExceededError):
        limiter.check_and_record()


def test_remaining_decrements():
    limiter = SlidingWindowRateLimiter(max_calls=10, window=60)
    assert limiter.remaining() == 10
    limiter.check_and_record()
    assert limiter.remaining() == 9


def test_reset_clears_calls():
    limiter = SlidingWindowRateLimiter(max_calls=2, window=60)
    limiter.check_and_record()
    limiter.check_and_record()
    limiter.reset()
    assert limiter.remaining() == 2
    limiter.check_and_record()  # Should not raise after reset


def test_window_expiry_allows_new_calls(monkeypatch):
    import time
    limiter = SlidingWindowRateLimiter(max_calls=2, window=1)
    limiter.check_and_record()
    limiter.check_and_record()

    # Simulate time advancing past the window
    original_time = time.time
    monkeypatch.setattr("app.expert.rate_limiter.time", type("T", (), {"time": staticmethod(lambda: original_time() + 2)})())
    # After window expires, calls should be allowed again
    limiter.check_and_record()  # Should not raise


def test_rate_limit_error_message_is_informative():
    limiter = SlidingWindowRateLimiter(max_calls=1, window=60)
    limiter.check_and_record()
    with pytest.raises(RateLimitExceededError, match="rate limit"):
        limiter.check_and_record()
