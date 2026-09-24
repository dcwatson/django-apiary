import time

import msgspec
from django.core.cache import DEFAULT_CACHE_ALIAS, caches
from django.http import HttpRequest, HttpResponse

from .base import APIMiddleware
from .exceptions import AuthenticationFailed, PermissionDenied
from .utils import get_ip


class Bucket(msgspec.Struct):
    size: int
    fill_rate: int


class CacheEntry(msgspec.Struct):
    # A list of request times, oldest first, most recent at the end.
    times: list[int] = msgspec.field(default_factory=list)
    # The number of available tokens.
    tokens: int = 0
    # Time of the last token refill.
    last_refill: int = 0

    def trim(self, before: int) -> bool:
        """
        Removes all stored request times before the specified timestamp, and returns
        whether any were removed.
        """
        changed = False
        while self.times and self.times[0] <= before:
            self.times.pop(0)
            changed = True
        return changed

    def fill(self, bucket: Bucket, now: int):
        """
        Adjusts the number of available tokens based on the given `Bucket` specs and
        how long since the last refill.
        """
        if self.last_refill:
            elapsed = now - self.last_refill
            self.tokens = min(bucket.size, self.tokens + (elapsed * bucket.fill_rate))
        else:
            self.tokens = bucket.size
        self.last_refill = now

    def check_window(self, num_requests: int, since: int) -> bool:
        """
        Returns whether there have been fewer than `num_requests` since the given
        timestamp.
        """
        window_count = sum(1 for t in reversed(self.times) if t > since)
        return window_count < num_requests


# Use the same rate specifications as django-rest-framework.
DURATION_SPECS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class Throttle(APIMiddleware):
    def __init__(
        self,
        *rates: str,
        bucket_size: int | None = None,
        fill_rate: int | None = None,
        cache: str = DEFAULT_CACHE_ALIAS,
    ):
        super().__init__()
        self.windows = tuple(self._parse_rate(r) for r in rates)
        self.max_duration = max(w[1] for w in self.windows)
        self.cache = caches[cache]
        self.bucket = (
            Bucket(size=bucket_size, fill_rate=fill_rate)
            if bucket_size and fill_rate
            else None
        )

    def _parse_rate(self, rate: str) -> tuple[int, int]:
        num, period = rate.split("/", 1)
        return (int(num), DURATION_SPECS[period[0]])

    def cache_key(self, request: HttpRequest) -> str:
        ip = get_ip(request)
        return f"apiary:throttle:{ip}"

    def token_count(self, request: HttpRequest) -> int:
        return 1

    def now(self) -> int:
        return int(time.time())

    def load(self, key: str) -> CacheEntry:
        data = self.cache.get(key, default={})
        return msgspec.convert(data, type=CacheEntry, strict=False)

    def store(self, key, entry: CacheEntry):
        data = msgspec.to_builtins(entry)
        self.cache.set(key, data, timeout=self.max_duration)

    def on_request(self, request: HttpRequest) -> HttpResponse | None:
        if not self.windows and not self.bucket:
            return
        cache_key = self.cache_key(request)
        entry = self.load(cache_key)
        now = self.now()
        allowed = True
        # We need to keep at least `max_duration` request times to check all windows.
        changed = entry.trim(now - self.max_duration)
        # Refill bucket tokens and check whether we have enough, if necessary.
        if self.bucket:
            entry.fill(self.bucket, now)
            changed = True
            request_tokens = self.token_count(request)
            if entry.tokens >= request_tokens:
                entry.tokens -= request_tokens
            else:
                allowed = False
        # No need to check all the windows if the bucket is empty.
        if allowed:
            # The request is throttled if *any* of the rate limits apply.
            for num_requests, duration in self.windows:
                if not entry.check_window(num_requests, now - duration):
                    allowed = False
        if allowed:
            entry.times.append(now)
            changed = True
        if changed:
            self.store(cache_key, entry)
        if not allowed:
            return HttpResponse(status=429)


class RequireAuth(APIMiddleware):
    check_perms: tuple[str, ...]

    def __init__(self, *children: APIMiddleware | str):
        self.children = ()
        self.check_perms = ()
        for child in children:
            if isinstance(child, APIMiddleware):
                self.children += (child,)
            elif isinstance(child, str):
                self.check_perms += (child,)

    def on_request(self, request: HttpRequest):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            raise AuthenticationFailed()
        if self.check_perms and not user.has_perms(self.check_perms):
            raise PermissionDenied()
