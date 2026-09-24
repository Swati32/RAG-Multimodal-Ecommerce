import threading
import time


class RateLimiter:
    """Paces calls to a fixed rate across all threads, rather than relying
    on worker count to indirectly stay under a requests-per-minute quota."""

    def __init__(self, max_per_minute: int):
        self._interval = 60.0 / max_per_minute
        self._lock = threading.Lock()
        self._next_time = time.monotonic()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait_time = max(0.0, self._next_time - now)
            self._next_time = max(now, self._next_time) + self._interval
        if wait_time > 0:
            time.sleep(wait_time)
