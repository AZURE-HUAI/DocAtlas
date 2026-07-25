"""HTTP 抓取层。

三件事决定了整库的抓取速度，都在这个文件里：

1. **连接复用**：每个线程对每个主机保持一条长连接。原来每抓一页都要重新
   做一次 TLS 握手，握手本身比取数据还慢。
2. **限速**：只在服务器真的回 429/403 时才全局冷却，其余时间不人为设上限。
3. **有界重试**：网络抖动会重试，但不会无限等下去。
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

# 每个线程最多同时保持几个主机的长连接（图片可能来自多个 CDN 域名）。
MAX_POOLED_HOSTS = 6
MAX_REDIRECTS = 5

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip",
    "Accept": "application/json,text/xml,text/html;q=0.8,*/*;q=0.5",
    "Connection": "keep-alive",
}


class GlobalRateLimiter:
    """跨线程共享的自适应节流器。

    Epic 会对文档接口限流（回 429），而具体阈值没有公开、也会随时间变化，
    所以这里不写死速率，用和 TCP 拥塞控制一样的 AIMD 策略自己找上限：

    * 连续成功 → 每 `PROBE_EVERY` 次成功把速率**加**一点（加性增长）
    * 撞上 429/403 → 速率**减半**并全局冷却（乘性下降）

    结果是速率会稳定在服务器当下能接受的水平附近，人不用调参。
    `configure(N)`（N > 0）可以关掉自适应、锁定固定速率。
    """

    # 实测 Epic 的可持续上限在每秒 8~10 个请求附近。上限设得比它高太多，
    # 只会不停冲顶再被打回，平均反而更慢——所以顶到 10 就不再往上试。
    MIN_RATE = 1.0
    MAX_RATE = 10.0
    INITIAL_RATE = 3.0
    INCREASE_STEP = 0.3
    PROBE_EVERY = 12
    # 每次退让只减 25%：减太狠要花很久才爬回来，平均吞吐会掉。
    # 短时间内反复被拒时，连乘 0.75 依然能快速把速率压下去。
    BACKOFF_FACTOR = 0.75

    # 第一次被拒只停一小会儿；短时间内反复被拒才逐级加长。
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
        """`0` 表示自适应；正数表示锁定该速率。"""
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
        """服务器回了 429/403：全局冷却，并把速率降一档。

        一次限流"事件"通常会让所有在途请求同时失败。如果每个失败都降一次速，
        速率会在一瞬间跌到地板上再也回不来——这正是第一版慢到 0.5 页/秒的原因。
        所以：**已经在冷却中就只延长冷却，不再重复降速**。
        """
        with self.lock:
            now = time.monotonic()
            if now < self.cooldown_until:
                # 同一次事件的其他失败请求：不重复降速，也不叠加冷却。
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
    """保持与原有调用方一致的异常类型（调用方只看 .code）。"""


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


def close_connections() -> None:
    """线程收尾时主动关闭连接，避免留下大量 CLOSE_WAIT。"""
    pool = getattr(_thread_local, "pool", None)
    if not pool:
        return
    for connection in pool.values():
        with contextlib.suppress(Exception):
            connection.close()
    pool.clear()


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
    """发一次请求并跟随重定向，返回正文、最终 URL、Content-Type。"""
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
            # 长连接可能被服务器静默关掉；丢弃它，让上层重试时重新建立。
            _drop_connection(scheme, host)
            raise
        status = response.status
        if status in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if location:
                current = urllib.parse.urljoin(current, location)
                continue
            # Epic 的文档接口用"302 + 没有 Location + 正文里写 redirect_url"
            # 来表示页面搬家了。这不是错误，把正文交给上层去解释。
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
    """抓一个 URL，返回正文、最终 URL、Content-Type。"""
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
            # 没有 Retry-After 时交给限速器自己决定停多久（会逐级加长）。
            default_wait = 0.0 if throttled else min(2**attempt, 20)
            wait_seconds = retry_after_seconds(
                exc.headers.get("Retry-After") if exc.headers else None,
                default_wait,
            )
            if throttled:
                # 服务器在推我们走：让所有线程一起退让，而不是各自空转。
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
