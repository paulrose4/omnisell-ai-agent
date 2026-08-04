class CommerceError(RuntimeError):
    code = "commerce_error"
    retryable = False


class RateLimited(CommerceError):
    code = "rate_limited"
    retryable = True

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AuthorizationExpired(CommerceError):
    code = "authorization_expired"


class ValidationFailed(CommerceError):
    code = "validation_failed"

    def __init__(self, message: str, issues: list[dict] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class Conflict(CommerceError):
    code = "conflict"


class PlatformUnavailable(CommerceError):
    code = "platform_unavailable"
    retryable = True


class ResourceNotFound(CommerceError):
    code = "resource_not_found"
