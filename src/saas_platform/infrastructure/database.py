from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from saas_platform.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        common = {"pool_pre_ping": True, "future": True}
        self.runtime_engine = create_engine(settings.database_url, **common)
        self.worker_engine = create_engine(settings.database_worker_url, **common)
        self.admin_engine = (
            create_engine(settings.database_admin_url, **common)
            if settings.database_admin_url
            else None
        )

    @contextmanager
    def tenant_transaction(self, tenant_id: UUID) -> Iterator[Connection]:
        with self.runtime_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            yield connection

    @contextmanager
    def worker_transaction(self) -> Iterator[Connection]:
        with self.worker_engine.begin() as connection:
            yield connection

    def ping(self) -> None:
        with self.runtime_engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.runtime_engine.dispose()
        self.worker_engine.dispose()
        if self.admin_engine is not None:
            self.admin_engine.dispose()
