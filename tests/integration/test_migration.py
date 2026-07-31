import pytest
from sqlalchemy import Engine, inspect, text

from tests.conftest import DatabaseUrls

pytestmark = pytest.mark.integration


def test_migration_creates_expected_schema_and_forces_rls(
    admin_engine: Engine,
    database_urls: DatabaseUrls,
) -> None:
    expected = {
        "alembic_version",
        "audit_events",
        "consumer_receipts",
        "outbox_items",
        "queue_messages",
        "synthetic_events",
        "tenants",
    }
    assert set(inspect(admin_engine).get_table_names()) == expected

    with admin_engine.connect() as connection:
        rls_rows = connection.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(:tables) ORDER BY relname"
            ),
            {"tables": sorted(expected - {"alembic_version"})},
        ).all()
        runtime_bypass = connection.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_runtime'")
        ).scalar_one()

    assert len(rls_rows) == 6
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in rls_rows)
    assert runtime_bypass is False
    assert database_urls.runtime != database_urls.worker
