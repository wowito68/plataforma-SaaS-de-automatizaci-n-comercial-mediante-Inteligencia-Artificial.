import pytest
from fastapi import status

from saas_platform.entrypoints.api import _application_status
from saas_platform.errors import (
    ApplicationError,
    ConflictError,
    ForbiddenError,
    InfrastructureError,
    IntegrationError,
    NotFoundError,
    PermanentError,
    TransientError,
    UnauthorizedError,
    ValidationError,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValidationError("invalid"), status.HTTP_422_UNPROCESSABLE_CONTENT),
        (UnauthorizedError("missing"), status.HTTP_401_UNAUTHORIZED),
        (ForbiddenError("denied"), status.HTTP_403_FORBIDDEN),
        (NotFoundError("missing"), status.HTTP_404_NOT_FOUND),
        (ConflictError("conflict"), status.HTTP_409_CONFLICT),
        (TransientError("retry"), status.HTTP_503_SERVICE_UNAVAILABLE),
        (InfrastructureError("down"), status.HTTP_503_SERVICE_UNAVAILABLE),
        (PermanentError("rejected"), status.HTTP_502_BAD_GATEWAY),
        (IntegrationError("provider"), status.HTTP_502_BAD_GATEWAY),
        (ApplicationError("unexpected"), status.HTTP_500_INTERNAL_SERVER_ERROR),
    ],
)
def test_application_errors_have_stable_http_translation(
    error: ApplicationError,
    expected: int,
) -> None:
    assert _application_status(error) == expected
