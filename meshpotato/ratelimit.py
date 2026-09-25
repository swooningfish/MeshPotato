"""Rate limiting: sliding windows per user, per channel and for the whole bot."""

import time
from collections import defaultdict, deque

from . import config as cfg


class SlidingWindowLimiter:
    """Allows max_events per window_sec for each key."""

    def __init__(self, max_events: int, window_sec: float):
        self.max_events = max_events
        self.window_sec = window_sec
        self._hits: dict[str, deque] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque:
        q = self._hits[key]
        while q and now - q[0] >= self.window_sec:
            q.popleft()
        return q

    def allowed(self, key: str, now: float) -> bool:
        return len(self._prune(key, now)) < self.max_events

    def record(self, key: str, now: float) -> None:
        self._hits[key].append(now)

    def retry_after(self, key: str, now: float) -> int:
        q = self._prune(key, now)
        if len(q) < self.max_events:
            return 0
        return max(1, int(self.window_sec - (now - q[0])) + 1)

    def cleanup(self, now: float) -> None:
        for key in list(self._hits):
            if not self._prune(key, now):
                del self._hits[key]


class RateLimiter:
    """Checks user, channel and global buckets. Records a hit only if all pass."""

    def __init__(self):
        self.user = SlidingWindowLimiter(*cfg.RATE_LIMIT_PER_USER)
        self.channel = SlidingWindowLimiter(*cfg.RATE_LIMIT_PER_CHANNEL)
        self.glob = SlidingWindowLimiter(*cfg.RATE_LIMIT_GLOBAL)
        self._notified: dict[str, float] = {}

    def check(self, user_key: str, chan_key: str) -> tuple[bool, str, int]:
        now = time.monotonic()
        for name, lim, key in (("user", self.user, user_key),
                               ("channel", self.channel, chan_key),
                               ("global", self.glob, "global")):
            if not lim.allowed(key, now):
                return False, name, lim.retry_after(key, now)
        self.user.record(user_key, now)
        self.channel.record(chan_key, now)
        self.glob.record("global", now)
        return True, "", 0

    def should_notify(self, user_key: str) -> bool:
        """One 'slow down' notice per user per user-window."""
        now = time.monotonic()
        last = self._notified.get(user_key)
        if last is None or now - last >= self.user.window_sec:
            self._notified[user_key] = now
            return True
        return False

    def cleanup(self) -> None:
        now = time.monotonic()
        for lim in (self.user, self.channel, self.glob):
            lim.cleanup(now)
        for k, t in list(self._notified.items()):
            if now - t >= self.user.window_sec:
                del self._notified[k]
