from typing import Any


class APIError(Exception):
    status_code: int = 500
    default_message: str = "An error occurred while processing this request."
    extra: dict[str, Any]

    def __init__(self, message: str | None = None, **extra: Any):
        self.extra = extra
        super().__init__(message or self.default_message)


class BadRequest(APIError):
    status_code = 400
    default_message = "Bad request."


class AuthenticationFailed(APIError):
    status_code = 401
    default_message = "Authentication failed."


class PermissionDenied(APIError):
    status_code = 403
    default_message = "Permission denied."


class NotFound(APIError):
    status_code = 404
    default_message = "Not found."


class BadRequestData(APIError):
    status_code = 422
    default_message = "Bad request data."
