from django.http import HttpRequest

from .base import APIMiddleware
from .exceptions import AuthenticationFailed, PermissionDenied


class Throttle(APIMiddleware):
    def __init__(self, spec: str):
        super().__init__()


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

    def on_request(self, request: HttpRequest) -> None:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            raise AuthenticationFailed()
        if self.check_perms and not user.has_perms(self.check_perms):
            raise PermissionDenied()
