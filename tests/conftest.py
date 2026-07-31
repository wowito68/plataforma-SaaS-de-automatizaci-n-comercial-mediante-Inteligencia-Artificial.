import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
import uuid6
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, insert
from sqlalchemy.engine import make_url
from testcontainers.community.postgres import PostgresContainer

from saas_platform.config import Settings
from saas_platform.entrypoints.api import create_app
from saas_platform.infrastructure.schema import tenants

ADAPTER_TOKEN = "local-test-adapter-token-123456"


@dataclass(frozen=True, slots=True)
class DatabaseUrls:
    superuser: str
    migration: str
    runtime: str
    worker: str


def _role_url(superuser_url: str, username: str, password: str) -> str:
    url = make_url(superuser_url).set(username=username, password=password)
    return url.render_as_string(hide_password=False)


def _prepare_database(superuser_url: str) -> DatabaseUrls:
    database_name = make_url(superuser_url).database
    if database_name is None:
        raise RuntimeError("test database URL must include a database name")
    engine = create_engine(superuser_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE ROLE app_owner LOGIN PASSWORD 'test-owner' NOSUPERUSER NOBYPASSRLS"
            )
            connection.exec_driver_sql(
                "CREATE ROLE app_runtime LOGIN PASSWORD 'test-runtime' NOSUPERUSER NOBYPASSRLS"
            )
            connection.exec_driver_sql(
                "CREATE ROLE app_worker LOGIN PASSWORD 'test-worker' NOSUPERUSER BYPASSRLS"
            )
            connection.exec_driver_sql(f'ALTER DATABASE "{database_name}" OWNER TO app_owner')
            connection.exec_driver_sql("ALTER SCHEMA public OWNER TO app_owner")
            connection.exec_driver_sql(
                f'GRANT CONNECT ON DATABASE "{database_name}" TO app_runtime, app_worker'
            )
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO app_runtime, app_worker")
    finally:
        engine.dispose()

    urls = DatabaseUrls(
        superuser=superuser_url,
        migration=_role_url(superuser_url, "app_owner", "test-owner"),
        runtime=_role_url(superuser_url, "app_runtime", "test-runtime"),
        worker=_role_url(superuser_url, "app_worker", "test-worker"),
    )
    alembic = Config("alembic.ini")
    alembic.set_main_option("sqlalchemy.url", urls.migration)
    command.upgrade(alembic, "head")
    return urls


@pytest.fixture(scope="session")
def database_urls() -> Iterator[DatabaseUrls]:
    external_url = os.getenv("TEST_DATABASE_SUPERUSER_URL")
    if external_url:
        normalized = external_url.replace("postgresql://", "postgresql+psycopg://", 1)
        yield _prepare_database(normalized)
        return

    with PostgresContainer(
        image="postgres:16-alpine",
        username="postgres",
        password="test-postgres",
        dbname="iteration1",
        driver="psycopg",
    ) as postgres:
        yield _prepare_database(postgres.get_connection_url())


@pytest.fixture()
def admin_engine(database_urls: DatabaseUrls) -> Iterator[Engine]:
    engine = create_engine(database_urls.superuser)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "TRUNCATE audit_events, consumer_receipts, queue_messages, "
                "outbox_items, synthetic_events, tenants CASCADE"
            )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def tenant_ids(admin_engine: Engine) -> tuple[UUID, UUID]:
    first = uuid6.uuid7()
    second = uuid6.uuid7()
    now = datetime.now(UTC)
    with admin_engine.begin() as connection:
        connection.execute(
            insert(tenants),
            [
                {
                    "id": first,
                    "name": "Tenant Alpha",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "version": 1,
                },
                {
                    "id": second,
                    "name": "Tenant Beta",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "version": 1,
                },
            ],
        )
    return first, second


def build_settings(database_urls: DatabaseUrls, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "service_name": "saas-platform-test",
        "database_url": database_urls.runtime,
        "database_worker_url": database_urls.worker,
        "database_admin_url": database_urls.superuser,
        "synthetic_adapter_enabled": True,
        "synthetic_adapter_token": ADAPTER_TOKEN,
        "metrics_enabled": False,
        "queue_poll_interval_ms": 50,
        "queue_lease_seconds": 5,
        "queue_max_attempts": 3,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture()
def app_settings(database_urls: DatabaseUrls) -> Settings:
    return build_settings(database_urls)


@pytest.fixture()
def api_client(app_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(app_settings), raise_server_exceptions=True) as client:
        yield client


@pytest.fixture()
def api_context(app_settings: Settings) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(app_settings)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield app, client


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADAPTER_TOKEN}"}
