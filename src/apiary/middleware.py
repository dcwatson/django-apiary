import time

import msgspec
from django.core.cache import DEFAULT_CACHE_ALIAS, caches
from django.http import HttpRequest, HttpResponse

from .base import APIMiddleware
from .exceptions import AuthenticationFailed, PermissionDenied
from .utils import get_ip


class CacheEntry(msgspec.Struct):
    # A list of request times, oldest first, most recent at the end.
    times: list[int] = msgspec.field(default_factory=list)
    # The number of available tokens.
    tokens: int = 0

    def trim(self, before: int) -> bool:
        changed = False
        while self.times and self.times[0] <= before:
            self.times.pop(0)
            changed = True
        return changed

    def check_window(self, num_requests: int, since: int) -> bool:
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

    def _parse_rate(self, rate: str) -> tuple[int, int]:
        num, period = rate.split("/", 1)
        return (int(num), DURATION_SPECS[period[0]])

    def cache_key(self, request: HttpRequest) -> str:
        ip = get_ip(request)
        return f"apiary:throttle:{ip}"

    def now(self) -> int:
        return int(time.time())

    def load(self, key: str) -> CacheEntry:
        data = self.cache.get(key, default={})
        return msgspec.convert(data, type=CacheEntry, strict=False)

    def store(self, key, entry: CacheEntry):
        data = msgspec.to_builtins(entry)
        self.cache.set(key, data, timeout=self.max_duration)

    def on_request(self, request: HttpRequest) -> HttpResponse | None:
        if not self.windows:
            return
        cache_key = self.cache_key(request)
        entry = self.load(cache_key)
        now = self.now()
        # We need to keep at least `max_duration` request times to check all windows.
        changed = entry.trim(now - self.max_duration)
        # The request is throttled if *any* of the rate limits apply.
        allowed = True
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
