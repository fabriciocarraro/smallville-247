"""
Cost governor.

Counter-intuitive result worth knowing before you build: the 1200 req/min
account limit is NOT the binding constraint for this project. Scene batching
puts us around 1 req/s, 20x under the limit. What actually binds is money.

    $8/day  = 190 MTok/day = ~2200 input tokens/sec
    a scene request with 8 agents ~ 2500 tokens
    => ~0.9 requests/sec => 300 agents each think about every 40s

So: spend the token budget on the *most interesting* scenes, not on round-robin.
A crowded tavern after a fight earns a request; one person asleep at home does not.
"""
from __future__ import annotations

import time

from .jev import REQ_PER_MIN_LIMIT, USD_PER_INPUT_TOKEN


class Governor:
    def __init__(self, daily_budget_usd: float = 8.0, burst_seconds: float = 20.0):
        self.daily_budget_usd = daily_budget_usd
        self.tokens_per_sec = (daily_budget_usd / 86400.0) / USD_PER_INPUT_TOKEN
        self.capacity = self.tokens_per_sec * burst_seconds
        self.bucket = self.capacity
        self.last = time.monotonic()
        self.spent_tokens = 0
        self.requests = 0
        self.window: list[tuple[float, int]] = []      # (t, tokens) for a rolling rate
        self._minute_start = time.monotonic()
        self._minute_requests = 0
        self.throttled_ticks = 0

    def refill(self, virtual_seconds: float | None = None) -> None:
        """Wall clock in production; `virtual_seconds` lets headless runs fast-forward
        a full simulated day without waiting one."""
        if virtual_seconds is not None:
            self.bucket = min(self.capacity, self.bucket + virtual_seconds * self.tokens_per_sec)
            self._virtual_elapsed = getattr(self, "_virtual_elapsed", 0.0) + virtual_seconds
            self._minute_requests = 0
            return
        now = time.monotonic()
        self.bucket = min(self.capacity, self.bucket + (now - self.last) * self.tokens_per_sec)
        self.last = now
        if now - self._minute_start >= 60:
            self._minute_start = now
            self._minute_requests = 0

    def can_afford(self, tokens: int) -> bool:
        if self._minute_requests >= REQ_PER_MIN_LIMIT * 0.8:   # stay clear of the hard limit
            return False
        return self.bucket >= tokens

    def charge(self, tokens: int, now: float | None = None) -> None:
        self.bucket -= tokens
        self.spent_tokens += tokens
        self.requests += 1
        self._minute_requests += 1
        t = now if now is not None else time.monotonic()
        self.window.append((t, tokens))
        cutoff = t - 90
        while self.window and self.window[0][0] < cutoff:
            self.window.pop(0)

    @property
    def spent_usd(self) -> float:
        return self.spent_tokens * USD_PER_INPUT_TOKEN

    WINDOW_S = 90.0

    def projected_daily_usd(self, elapsed_s: float, now: float | None = None) -> float:
        """Rate over the last 90s, not since boot - the burst bucket makes the
        cumulative average read ~4x high for the first minute.

        Divide by the *window*, never by the age of the oldest retained sample.
        In a small town requests are bursty, so the oldest sample is often much
        younger than 90s, and dividing by that gap overstated spend by ~2x.
        """
        if not self.window or elapsed_s < 5:
            return 0.0
        span = max(5.0, min(self.WINDOW_S, elapsed_s))
        tokens = sum(tok for _, tok in self.window)
        return tokens / span * 86400 * USD_PER_INPUT_TOKEN


def scene_priority(place: str, present: list, tick: int, heat: float) -> float:
    """What makes a scene worth paying for."""
    if not present:
        return 0.0
    staleness = min(300, tick - min(a.last_thought_tick for a in present))
    crowd = min(len(present), 10)
    # people who dislike each other in one room is the most valuable thing in the sim
    friction = 0.0
    ids = {a.id for a in present}
    for a in present:
        for other, v in a.trust.items():
            if other in ids and v < -0.3:
                friction += 0.6
    return staleness * 0.05 + crowd * 1.4 + friction + heat * 3.0
