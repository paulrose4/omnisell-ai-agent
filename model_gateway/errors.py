class ModelGatewayError(RuntimeError):
    """Base class for bounded model gateway failures."""


class ModelUnavailable(ModelGatewayError):
    pass


class ModelConnectionError(ModelUnavailable):
    pass


class ModelProviderError(ModelUnavailable):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelRateLimited(ModelUnavailable):
    pass


class ModelContextRejected(ModelGatewayError):
    pass


class ModelUsageError(ModelGatewayError):
    pass


class ModelTimeout(ModelGatewayError):
    pass


class ModelBudgetExceeded(ModelGatewayError):
    pass


class ModelSchemaError(ModelGatewayError):
    pass


class ModelSafetyRejected(ModelGatewayError):
    pass


class ModelFailoverError(ModelGatewayError):
    def __init__(self, *, primary_error: Exception, fallback_error: Exception) -> None:
        self.primary_error_category = type(primary_error).__name__
        self.fallback_error_category = type(fallback_error).__name__
        self.retryable = _is_retryable_error(fallback_error)
        super().__init__(
            "Primary model provider failed and configured fallback provider also failed "
            f"({self.primary_error_category} -> {self.fallback_error_category})"
        )


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, ModelProviderError):
        return error.retryable
    return bool(getattr(error, "retryable", False)) or isinstance(error, (ModelUnavailable, ModelTimeout))
