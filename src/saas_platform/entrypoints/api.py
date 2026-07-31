import hmac
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import uuid6
import uvicorn
from fastapi import Depends, FastAPI, Header, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint

from saas_platform.bootstrap import Container
from saas_platform.config import Settings, get_settings
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
from saas_platform.modules.synthetic_events.domain import (
    IdempotencyKey,
    SyntheticEvent,
    SyntheticPayload,
)
from saas_platform.modules.synthetic_events.ports import AcceptSyntheticEventCommand
from saas_platform.modules.tenancy.domain import TenantId
from saas_platform.observability import (
    HTTP_DURATION,
    HTTP_REQUESTS,
    SYNTHETIC_EVENTS,
    bind_context,
    configure_logging,
    reset_context,
)

logger = logging.getLogger(__name__)
SyntheticScalar = str | int | bool


class SyntheticEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: str = Field(min_length=1, max_length=64)
    attributes: dict[str, SyntheticScalar] = Field(default_factory=dict)


class SyntheticEventResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    status: str
    correlation_id: UUID
    created_at: datetime
    processed_at: datetime | None
    version: int
    idempotent_replay: bool


class HealthResponse(BaseModel):
    status: str
    service: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str | None
    correlation_id: str | None


def _error_response(request: Request, error: ApplicationError, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": error.code,
            "message": error.message,
            "details": error.details,
            "request_id": getattr(request.state, "request_id", None),
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )


def _application_status(error: ApplicationError) -> int:
    if isinstance(error, ValidationError):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    if isinstance(error, UnauthorizedError):
        return status.HTTP_401_UNAUTHORIZED
    if isinstance(error, ForbiddenError):
        return status.HTTP_403_FORBIDDEN
    if isinstance(error, NotFoundError):
        return status.HTTP_404_NOT_FOUND
    if isinstance(error, ConflictError):
        return status.HTTP_409_CONFLICT
    if isinstance(error, (TransientError, InfrastructureError)):
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if isinstance(error, PermanentError):
        return status.HTTP_502_BAD_GATEWAY
    if isinstance(error, IntegrationError):
        return status.HTTP_502_BAD_GATEWAY
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def _event_response(event: SyntheticEvent, *, replay: bool) -> SyntheticEventResponse:
    return SyntheticEventResponse(
        id=event.id,
        tenant_id=event.tenant_id.value,
        status=event.status.value,
        correlation_id=event.correlation_id,
        created_at=event.created_at,
        processed_at=event.processed_at,
        version=event.version,
        idempotent_replay=replay,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(
        environment=resolved_settings.app_env,
        service=resolved_settings.service_name,
        level=resolved_settings.log_level,
    )
    container = Container.build(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("API started", extra={"operation": "application.start", "result": "ready"})
        try:
            yield
        finally:
            container.close()
            logger.info("API stopped", extra={"operation": "application.stop", "result": "ok"})

    app = FastAPI(
        title="SaaS Sales Automation",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.container = container

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        request_id = str(uuid6.uuid7())
        raw_correlation = request.headers.get("X-Correlation-ID")
        try:
            correlation_id = str(UUID(raw_correlation)) if raw_correlation else str(uuid6.uuid7())
        except ValueError:
            error = ValidationError("X-Correlation-ID must be a valid UUID")
            request.state.request_id = request_id
            request.state.correlation_id = str(uuid6.uuid7())
            invalid_response = _error_response(
                request, error, status.HTTP_422_UNPROCESSABLE_CONTENT
            )
            invalid_response.headers["X-Request-ID"] = request.state.request_id
            invalid_response.headers["X-Correlation-ID"] = request.state.correlation_id
            return invalid_response

        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        bound = bind_context(request_id=request_id, correlation_id=correlation_id)
        try:
            response = await call_next(request)
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            HTTP_REQUESTS.labels(
                method=request.method,
                route=route_path,
                status=str(response.status_code),
            ).inc()
            HTTP_DURATION.labels(method=request.method, route=route_path).observe(
                time.perf_counter() - started
            )
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Correlation-ID"] = correlation_id
            logger.info(
                "HTTP request completed",
                extra={"operation": "http.request", "result": str(response.status_code)},
            )
            return response
        finally:
            reset_context(bound)

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, error: ApplicationError) -> JSONResponse:
        status_code = _application_status(error)
        logger.warning(
            "application request failed",
            extra={
                "operation": "http.error",
                "error_type": type(error).__name__,
                "result": str(status_code),
            },
        )
        return _error_response(request, error, status_code)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        details = {
            "errors": [
                {
                    "location": [str(part) for part in item.get("loc", ())],
                    "message": item.get("msg", "invalid value"),
                    "type": item.get("type", "validation_error"),
                }
                for item in error.errors()
            ]
        }
        return _error_response(
            request,
            ValidationError("request validation failed", details=details),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        logger.exception(
            "database request failure",
            extra={
                "operation": "database.error",
                "error_type": type(error).__name__,
                "result": "503",
            },
        )
        return _error_response(
            request,
            InfrastructureError("database operation failed"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception(
            "unexpected request failure",
            extra={
                "operation": "http.error",
                "error_type": type(error).__name__,
                "result": "500",
            },
        )
        return _error_response(
            request,
            ApplicationError("an unexpected internal error occurred"),
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    def liveness() -> HealthResponse:
        return HealthResponse(status="ok", service=resolved_settings.service_name)

    @app.get(
        "/health/ready",
        response_model=HealthResponse,
        responses={503: {"model": ErrorBody}},
        tags=["health"],
    )
    def readiness() -> HealthResponse:
        try:
            container.database.ping()
        except Exception as error:
            raise InfrastructureError("database readiness check failed") from error
        return HealthResponse(status="ready", service=resolved_settings.service_name)

    if resolved_settings.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        def metrics() -> Response:
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if resolved_settings.synthetic_adapter_enabled:
        configured_token = resolved_settings.synthetic_adapter_token
        if configured_token is None:  # Guarded by Settings; keeps the closure fully typed.
            raise RuntimeError("synthetic adapter token is missing")
        expected_token = configured_token.get_secret_value()

        def authenticate_adapter(
            authorization: Annotated[str | None, Header()] = None,
        ) -> None:
            scheme, _, token = (authorization or "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected_token):
                raise UnauthorizedError("valid bearer authentication is required")

        AdapterAuth = Annotated[None, Depends(authenticate_adapter)]

        @app.post(
            "/internal/v1/tenants/{tenant_id}/synthetic-events",
            response_model=SyntheticEventResponse,
            status_code=status.HTTP_202_ACCEPTED,
            responses={
                200: {"model": SyntheticEventResponse},
                401: {"model": ErrorBody},
                404: {"model": ErrorBody},
                409: {"model": ErrorBody},
                422: {"model": ErrorBody},
            },
            tags=["internal"],
        )
        def accept_synthetic_event(
            tenant_id: UUID,
            body: SyntheticEventRequest,
            response: Response,
            _: AdapterAuth,
            request: Request,
            idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        ) -> SyntheticEventResponse:
            tenant = TenantId(tenant_id)
            correlation_id = UUID(request.state.correlation_id)
            bound = bind_context(tenant_id=tenant_id)
            try:
                result = container.accept_synthetic_event.execute(
                    AcceptSyntheticEventCommand(
                        tenant_id=tenant,
                        idempotency_key=IdempotencyKey(idempotency_key),
                        payload=SyntheticPayload(kind=body.kind, attributes=body.attributes),
                        correlation_id=correlation_id,
                    )
                )
                result_label = "created" if result.created else "duplicate"
                logger.info(
                    "synthetic event accepted",
                    extra={
                        "event_id": str(result.event.id),
                        "operation": "synthetic_event.accept",
                        "result": result_label,
                    },
                )
            finally:
                reset_context(bound)
            response.status_code = (
                status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
            )
            SYNTHETIC_EVENTS.labels(result=result_label).inc()
            return _event_response(result.event, replay=not result.created)

        @app.get(
            "/internal/v1/tenants/{tenant_id}/synthetic-events/{event_id}",
            response_model=SyntheticEventResponse,
            responses={
                401: {"model": ErrorBody},
                404: {"model": ErrorBody},
                422: {"model": ErrorBody},
            },
            tags=["internal"],
        )
        def get_synthetic_event(
            tenant_id: UUID,
            event_id: UUID,
            _: AdapterAuth,
        ) -> SyntheticEventResponse:
            bound = bind_context(tenant_id=tenant_id)
            try:
                event = container.get_synthetic_event.execute(TenantId(tenant_id), event_id)
            finally:
                reset_context(bound)
            return _event_response(event, replay=False)

    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
