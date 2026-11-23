import time
from collections import deque


class RateLimiter:
    """In-process sliding-window limiter for user messages."""

    def __init__(self, rate_limit_max: int, rate_limit_window: int):
        if rate_limit_max < 1 or rate_limit_window < 1:
            raise ValueError("Rate limit and window must be positive")
        self.rate_limit_max = rate_limit_max
        self.rate_limit_window = rate_limit_window
        self.user_timestamps: dict[int, deque[float]] = {}

    async def allow(self, user_id: int) -> bool:
        now = time.monotonic()
        timestamps = self.user_timestamps.setdefault(user_id, deque())
        while timestamps and timestamps[0] <= now - self.rate_limit_window:
            timestamps.popleft()
        if len(timestamps) >= self.rate_limit_max:
            return False
        timestamps.append(now)
        return True

    async def reset(self, user_id: int) -> None:
        self.user_timestamps.pop(user_id, None)
