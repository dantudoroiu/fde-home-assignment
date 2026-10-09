"""Circuit breaker for the model API.

During an outage, every call would otherwise wait through the timeout and the SDK's retries (up to
~90 s) while holding a concurrency slot, so a backlog of tickets takes hours to fail. After
`failure_threshold` consecutive availability failures the breaker opens: calls fail immediately for
`cooldown_seconds`, then a single trial call is let through (half-open). Its outcome closes the breaker
or opens it for another cooldown.

Thread-safe: background tasks call the model from many threads at once.
"""

import threading
import time
from typing import Callable, Literal

from app.observability import get_logger

log = get_logger(__name__)

State = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> State:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if self._trial_in_flight:
                return "half_open"  # a probe call is out right now
            if self._clock() - self._opened_at < self._cooldown:
                return "open"
            return "half_open"  # cooldown over: the next call will be the probe

    def is_rejecting(self) -> bool:
        """Read-only check, no side effects: True while calls would be refused right now. Lets callers
        fail fast before queueing; allow_request() remains the authoritative gate."""
        with self._lock:
            if self._opened_at is None:
                return False
            return self._trial_in_flight or self._clock() - self._opened_at < self._cooldown

    def allow_request(self) -> bool:
        """Whether a call may go out now. Every allowed call must be followed by exactly one
        record_success() or record_failure(), or a half-open trial would never resolve."""
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at < self._cooldown or self._trial_in_flight:
                return False
            self._trial_in_flight = True  # half-open: this call is the probe
            return True

    def record_success(self) -> None:
        with self._lock:
            was_open = self._opened_at is not None
            self._consecutive_failures = 0
            self._opened_at = None
            self._trial_in_flight = False
        if was_open:
            log.info("llm_circuit_closed")

    def record_failure(self) -> None:
        with self._lock:
            self._trial_in_flight = False
            if self._opened_at is not None:
                # A failed trial (or a call that was already in flight): stay open, restart cooldown.
                self._opened_at = self._clock()
                return
            self._consecutive_failures += 1
            if self._consecutive_failures < self._threshold:
                return
            self._opened_at = self._clock()
            failures = self._consecutive_failures
        log.warning("llm_circuit_opened", consecutive_failures=failures, cooldown_s=self._cooldown)
