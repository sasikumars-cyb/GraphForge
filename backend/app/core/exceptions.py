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


class UnauthorizedError(AppError):
    """Raised when a request has no, or invalid/expired, credentials."""

    status_code = 401
    error_code = "unauthorized"


class ForbiddenError(AppError):
    """Raised when a user is authenticated but lacks permission."""

    status_code = 403
    error_code = "forbidden"


class NotImplementedYetError(AppError):
    """Raised by a prepared-but-unimplemented extension point (e.g. GitHub OAuth)."""

    status_code = 501
    error_code = "not_implemented"


class RateLimitedError(AppError):
    """Raised when a user exceeds a per-endpoint request rate limit."""

    status_code = 429
    error_code = "rate_limited"


class AgentDisabledError(AppError):
    """Raised when a run is requested for an agent an admin has disabled
    via the runtime kill switch (app.orchestrator.registry.AgentRegistry)."""

    status_code = 503
    error_code = "agent_disabled"
