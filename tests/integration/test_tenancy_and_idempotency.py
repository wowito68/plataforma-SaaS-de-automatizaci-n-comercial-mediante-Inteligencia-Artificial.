from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text

from saas_platform.infrastructure.schema import synthetic_events
from tests.conftest import ADAPTER_TOKEN, DatabaseUrls

pytestmark = pytest.mark.integration


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ADAPTER_TOKEN}",
        "Idempotency-Key": key,
    }


def test_tenant_scope_blocks_cross_tenant_reads(
    api_client: TestClient,
    tenant_ids: tuple[object, object],
    database_urls: DatabaseUrls,
) -> None:
    tenant_a, tenant_b = tenant_ids
    created = api_client.post(
        f"/internal/v1/tenants/{tenant_a}/synthetic-events",
        headers=_headers("shared-key"),
        json={"kind": "demo.event", "attributes": {}},
    )
    event_id = created.json()["id"]

    hidden = api_client.get(
        f"/internal/v1/tenants/{tenant_b}/synthetic-events/{event_id}",
        headers=_headers("unused"),
    )
    separate = api_client.post(
        f"/internal/v1/tenants/{tenant_b}/synthetic-events",
        headers=_headers("shared-key"),
        json={"kind": "demo.event", "attributes": {}},
    )

    runtime = create_engine(database_urls.runtime)
    try:
        with runtime.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_b)},
            )
            visible = connection.execute(
                select(synthetic_events.c.id).where(synthetic_events.c.id == event_id)
            ).all()
    finally:
        runtime.dispose()

    assert created.status_code == 202
    assert hidden.status_code == 404
    assert separate.status_code == 202
    assert separate.json()["id"] != event_id
    assert visible == []


def test_concurrent_retries_create_one_event(
    api_client: TestClient,
    tenant_ids: tuple[object, object],
    admin_engine: Engine,
) -> None:
    tenant_id = tenant_ids[0]
    workers = 8
    barrier = Barrier(workers)

    def submit(_: int) -> tuple[int, str]:
        barrier.wait()
        response = api_client.post(
            f"/internal/v1/tenants/{tenant_id}/synthetic-events",
            headers=_headers("concurrent-key"),
            json={"kind": "demo.concurrent", "attributes": {"same": True}},
        )
        return response.status_code, response.json()["id"]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(submit, range(workers)))

    with admin_engine.connect() as connection:
        count = connection.execute(
            select(text("count(*)"))
            .select_from(synthetic_events)
            .where(synthetic_events.c.tenant_id == tenant_id)
        ).scalar_one()

    assert len({event_id for _, event_id in results}) == 1
    assert [code for code, _ in results].count(202) == 1
    assert [code for code, _ in results].count(200) == workers - 1
    assert count == 1
