"""Application exception hierarchy.

`AppError` and its subclasses represent expected, handled failure states —
as opposed to unexpected bugs. Services and routers raise these; they never
build a `Response` or set a status code themselves. `core.error_handlers`
is the only place that translates an `AppError` into an HTTP response.
"""


class AppError(Exception):
    """Base class for all expected application errors."""

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    status_code = 404
    error_code = "not_found"


class ConflictError(AppError):
    """Raised when a request conflicts with the current state of a resource."""

    status_code = 409
    error_code = "conflict"
