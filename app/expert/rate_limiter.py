"""
Sliding-window rate limiter for Expert API (OpenAI) calls.

Prevents accidental runaway spend. Default: 60 calls per 60-second window.
Configurable via environment variables:
  EXPERT_RATE_LIMIT_CALLS   (default 60)
  EXPERT_RATE_LIMIT_WINDOW  (default 60, seconds)
"""

from __future__ import annotations

import os
import time
from collections import deque

_MAX_CALLS: int = int(os.getenv("EXPERT_RATE_LIMIT_CALLS", "60"))
_WINDOW_SECONDS: int = int(os.getenv("EXPERT_RATE_LIMIT_WINDOW", "60"))


class RateLimitExceededError(Exception):
    """Raised when the Expert API rate limit is hit."""


class SlidingWindowRateLimiter:
    """
    Thread-safe* sliding window counter.
    (*Single-process only — sufficient for a personal server.)
    """

    def __init__(self, max_calls: int = _MAX_CALLS, window: int = _WINDOW_SECONDS) -> None:
        self.max_calls = max_calls
        self.window = window
        self._calls: deque[float] = deque()

    def check_and_record(self) -> None:
        """
        Record a call attempt.
        Raises RateLimitExceededError if the limit would be exceeded.
        """
        now = time.time()
        cutoff = now - self.window

        # Evict timestamps outside the window
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()

        if len(self._calls) >= self.max_calls:
            raise RateLimitExceededError(
                f"Expert API rate limit reached: {self.max_calls} calls "
                f"per {self.window}s window. Try again later."
            )

        self._calls.append(now)

    def remaining(self) -> int:
        """How many calls are left in the current window."""
        now = time.time()
        cutoff = now - self.window
        active = sum(1 for t in self._calls if t >= cutoff)
        return max(0, self.max_calls - active)

    def reset(self) -> None:
        """Clear all recorded calls (useful in tests)."""
        self._calls.clear()


# Module-level singleton used by OpenAIExpertClient
rate_limiter = SlidingWindowRateLimiter()
