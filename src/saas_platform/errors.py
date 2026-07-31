from collections.abc import Mapping
from typing import Any


class ApplicationError(Exception):
    code = "application_error"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


class ValidationError(ApplicationError):
    code = "validation_error"


class NotFoundError(ApplicationError):
    code = "not_found"


class ConflictError(ApplicationError):
    code = "conflict"


class UnauthorizedError(ApplicationError):
    code = "unauthorized"


class ForbiddenError(ApplicationError):
    code = "forbidden"


class IntegrationError(ApplicationError):
    code = "integration_error"


class TransientError(IntegrationError):
    code = "transient_error"


class PermanentError(IntegrationError):
    code = "permanent_error"


class InfrastructureError(ApplicationError):
    code = "infrastructure_error"
