"""The HTTP fetching layer.

Three things decide the crawl speed of an entire library, all of them here:

1. **Connection reuse**: each thread keeps one long-lived connection per host.
   Re-doing a TLS handshake per page costs more than fetching the data.
2. **Rate limiting**: a global cooldown only when the server actually answers
   429/403; no artificial ceiling the rest of the time.
3. **Bounded retries**: network flakiness is retried, but never waited on
   forever.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import email.utils
import gzip
import http.client
import threading
import time
import urllib.error
import urllib.parse
import zlib

from .config import RETRYABLE_HTTP_CODES, USER_AGENT

_thread_local = threading.local()

# Long-lived connections per thread, at most one per host (images may come from
# several CDN domains).
MAX_POOLED_HOSTS = 6
MAX_REDIRECTS = 5

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip",
    "Accept": "application/json,text/xml,text/html;q=0.8,*/*;q=0.5",
    "Connection": "keep-alive",
}


class GlobalRateLimiter:
    """Adaptive throttle shared across threads.

    Documentation endpoints rate-limit (answering 429) without publishing the
    threshold, and the threshold moves over time, so no fixed rate is hardcoded.
    It finds the ceiling itself with the same AIMD strategy as TCP congestion
    control:

    * a run of successes -> **add** a little rate every `PROBE_EVERY` successes
      (additive increase)
    * hitting 429/403 -> **halve** the rate and cool down globally
      (multiplicative decrease)

    The rate therefore settles near whatever the server currently tolerates, with
    nothing to tune by hand. `configure(N)` with N > 0 disables adaptation and
    locks a fixed rate.
    """

    # Measured sustainable ceilings sit near 8-10 requests per second. Setting the
    # cap far above that only means repeatedly overshooting and being pushed back,
    # which lowers the average, so probing stops at 10.
    MIN_RATE = 1.0
    MAX_RATE = 10.0
    INITIAL_RATE = 3.0
    INCREASE_STEP = 0.3
    PROBE_EVERY = 12
    # Back off by only 25% each time: cutting harder takes a long time to climb
    # back and lowers average throughput. Repeated refusals still compound
    # quickly, since 0.75 multiplies.
    BACKOFF_FACTOR = 0.75

    # A first refusal pauses only briefly; repeats within a short window lengthen
    # it step by step.
    BASE_COOLDOWN = 4.0
    MAX_COOLDOWN = 45.0
    ESCALATION_WINDOW = 90.0

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.adaptive = True
        self.requests_per_second = self.INITIAL_RATE
        self.next_request_at = 0.0
        self.cooldown_until = 0.0
        self.successes_since_change = 0
        self.throttle_events = 0
        self.last_throttle_at = -1e9
        self.consecutive_throttles = 0

    def configure(self, requests_per_second: float) -> None:
        """`0` selects adaptive behaviour; a positive value locks that rate."""
        with self.lock:
            if requests_per_second and requests_per_second > 0:
                self.adaptive = False
                self.requests_per_second = requests_per_second
            else:
                self.adaptive = True
                self.requests_per_second = self.INITIAL_RATE
            self.successes_since_change = 0

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                ready_at = max(self.next_request_at, self.cooldown_until)
                if now >= ready_at:
                    self.next_request_at = now + 1.0 / self.requests_per_second
                    return
                wait_seconds = ready_at - now
            time.sleep(min(wait_seconds, 5.0))

    def record_success(self) -> None:
        if not self.adaptive:
            return
        with self.lock:
            if self.requests_per_second >= self.MAX_RATE:
                return
            self.successes_since_change += 1
            if self.successes_since_change >= self.PROBE_EVERY:
                self.successes_since_change = 0
                self.requests_per_second = min(
                    self.MAX_RATE, self.requests_per_second + self.INCREASE_STEP
                )

    def penalize(self, seconds: float) -> None:
        """The server answered 429/403: cool down globally and drop a rate step.

        One throttling *event* usually fails every in-flight request at once. If
        each failure dropped the rate again, the rate would hit the floor
        instantly and never recover. So: **while already cooling down, only extend
        the cooldown, never drop the rate again**.
        """
        with self.lock:
            now = time.monotonic()
            if now < self.cooldown_until:
                # Another failure from the same event: no extra rate drop, and no
                # stacked cooldown.
                return
            self.throttle_events += 1
            if now - self.last_throttle_at <= self.ESCALATION_WINDOW:
                self.consecutive_throttles += 1
            else:
                self.consecutive_throttles = 1
            self.last_throttle_at = now
            cooldown = min(
                self.MAX_COOLDOWN,
                max(seconds, self.BASE_COOLDOWN * self.consecutive_throttles),
            )
            self.cooldown_until = now + cooldown
            if self.adaptive:
                self.successes_since_change = 0
                self.requests_per_second = max(
                    self.MIN_RATE, self.requests_per_second * self.BACKOFF_FACTOR
                )

    @property
    def cooling_down(self) -> bool:
        return time.monotonic() < self.cooldown_until

    def snapshot(self) -> dict[str, float]:
        with self.lock:
            return {
                "rate": round(self.requests_per_second, 2),
                "adaptive": self.adaptive,
                "throttle_events": self.throttle_events,
            }


REQUEST_LIMITER = GlobalRateLimiter()


class HTTPResponseError(urllib.error.HTTPError):
    """Exception type kept as callers expect it (they only read .code)."""


def _pool() -> dict[tuple[str, str], http.client.HTTPConnection]:
    pool = getattr(_thread_local, "pool", None)
    if pool is None:
        pool = {}
        _thread_local.pool = pool
    return pool


def _connection(scheme: str, host: str, timeout: int) -> http.client.HTTPConnection:
    pool = _pool()
    key = (scheme, host)
    connection = pool.get(key)
    if connection is None:
        if len(pool) >= MAX_POOLED_HOSTS:
            _, stale = pool.popitem()
            with contextlib.suppress(Exception):
                stale.close()
        factory = (
            http.client.HTTPSConnection
            if scheme == "https"
            else http.client.HTTPConnection
        )
        connection = factory(host, timeout=timeout)
        pool[key] = connection
    return connection


def _drop_connection(scheme: str, host: str) -> None:
    connection = _pool().pop((scheme, host), None)
    if connection is not None:
        with contextlib.suppress(Exception):
            connection.close()


def _decode_body(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if "gzip" in encoding:
        return gzip.decompress(raw)
    if "deflate" in encoding:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def retry_after_seconds(value: str | None, default: float) -> float:
    if not value:
        return default
    with contextlib.suppress(ValueError):
        return max(float(value), 1.0)
    with contextlib.suppress(ValueError, TypeError, OverflowError):
        retry_at = email.utils.parsedate_to_datetime(value)
        now = dt.datetime.now(retry_at.tzinfo or dt.timezone.utc)
        return max((retry_at - now).total_seconds(), 1.0)
    return default


def _request_once(url: str, timeout: int) -> tuple[bytes, str, str | None]:
    """Make one request, following redirects; returns body, final URL, type."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlsplit(current)
        scheme = parsed.scheme or "https"
        host = parsed.netloc
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        connection = _connection(scheme, host, timeout)
        try:
            connection.request("GET", target, headers=DEFAULT_HEADERS)
            response = connection.getresponse()
            raw = response.read()
        except Exception:
            # The server may silently close a pooled connection; drop it so the
            # retry above rebuilds one.
            _drop_connection(scheme, host)
            raise
        status = response.status
        if status in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if location:
                current = urllib.parse.urljoin(current, location)
                continue
            # Some documentation endpoints signal "this page moved" with a 302
            # carrying no Location and a redirect_url in the body. Not an error;
            # hand the body up for the caller to interpret.
            if raw:
                body = _decode_body(
                    raw, response.headers.get("Content-Encoding", "")
                )
                return body, current, response.headers.get("Content-Type")
            raise HTTPResponseError(
                current, status, response.reason, response.headers, None
            )
        if status >= 400:
            raise HTTPResponseError(
                current, status, response.reason, response.headers, None
            )
        body = _decode_body(raw, response.headers.get("Content-Encoding", ""))
        return body, current, response.headers.get("Content-Type")
    raise HTTPResponseError(url, 310, "Too many redirects", None, None)


def fetch_bytes(
    url: str,
    *,
    timeout: int = 90,
    retries: int = 5,
    delay: float = 0.0,
) -> tuple[bytes, str, str | None]:
    """Fetch one URL; returns body, final URL and Content-Type."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        REQUEST_LIMITER.wait()
        try:
            body, final_url, content_type = _request_once(url, timeout)
            REQUEST_LIMITER.record_success()
            if delay:
                time.sleep(delay)
            return body, final_url, content_type
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE_HTTP_CODES or attempt == retries:
                raise
            throttled = exc.code in {403, 429}
            # With no Retry-After, let the limiter decide the pause (it lengthens
            # step by step).
            default_wait = 0.0 if throttled else min(2**attempt, 20)
            wait_seconds = retry_after_seconds(
                exc.headers.get("Retry-After") if exc.headers else None,
                default_wait,
            )
            if throttled:
                # The server is pushing back: make every thread yield together
                # rather than each spinning on its own.
                REQUEST_LIMITER.penalize(wait_seconds)
            else:
                time.sleep(min(wait_seconds, 20.0))
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            OSError,
        ) as exc:
            last_error = exc
            if attempt == retries:
                raise
            time.sleep(min(2**attempt, 15))
    raise RuntimeError(f"Unable to fetch {url}: {last_error}")
