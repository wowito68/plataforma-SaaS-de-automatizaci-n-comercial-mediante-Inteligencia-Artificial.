import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as postgres_insert

from saas_platform.config import get_settings
from saas_platform.infrastructure.database import Database
from saas_platform.infrastructure.schema import tenants

DEMO_TENANTS = (
    (UUID("019867ab-cdef-7abc-8def-0123456789ab"), "Demo Alpha"),
    (UUID("019867ab-cdef-7abc-8def-0123456789ac"), "Demo Beta"),
)


def main() -> None:
    settings = get_settings()
    database = Database(settings)
    if database.admin_engine is None:
        raise RuntimeError("DATABASE_ADMIN_URL is required to bootstrap demo tenants")
    now = datetime.now(UTC)
    try:
        for tenant_id, name in DEMO_TENANTS:
            with database.admin_engine.begin() as connection:
                connection.exec_driver_sql(
                    "SELECT set_config('app.current_tenant_id', %s, true)",
                    (str(tenant_id),),
                )
                connection.execute(
                    postgres_insert(tenants)
                    .values(
                        id=tenant_id,
                        name=name,
                        status="active",
                        created_at=now,
                        updated_at=now,
                        version=1,
                    )
                    .on_conflict_do_nothing(index_elements=[tenants.c.id])
                )
        print(
            json.dumps(
                {"tenants": [{"id": str(item[0]), "name": item[1]} for item in DEMO_TENANTS]}
            )
        )
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
