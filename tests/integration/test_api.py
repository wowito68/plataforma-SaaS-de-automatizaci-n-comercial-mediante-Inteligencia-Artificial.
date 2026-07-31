from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from saas_platform.entrypoints.api import create_app
from tests.conftest import DatabaseUrls, build_settings

pytestmark = pytest.mark.integration


def _event_url(tenant_id: object) -> str:
    return f"/internal/v1/tenants/{tenant_id}/synthetic-events"


def test_liveness_and_readiness(
    api_client: TestClient,
    tenant_ids: tuple[object, object],
) -> None:
    assert tenant_ids
    assert api_client.get("/health/live").json()["status"] == "ok"
    assert api_client.get("/health/ready").json()["status"] == "ready"


def test_adapter_requires_authentication(
    api_client: TestClient,
    tenant_ids: tuple[object, object],
) -> None:
    response = api_client.post(
        _event_url(tenant_ids[0]),
        headers={"Idempotency-Key": "unauthorized-1"},
        json={"kind": "demo.event", "attributes": {}},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_accept_get_and_idempotent_replay(
    api_client: TestClient,
    tenant_ids: tuple[object, object],
    auth_headers: dict[str, str],
) -> None:
    correlation_id = uuid4()
    headers = auth_headers | {
        "Idempotency-Key": "event-001",
        "X-Correlation-ID": str(correlation_id),
    }
    payload = {"kind": "demo.event", "attributes": {"source": "test", "count": 1}}

    created = api_client.post(_event_url(tenant_ids[0]), headers=headers, json=payload)
    replay = api_client.post(_event_url(tenant_ids[0]), headers=headers, json=payload)
    fetched = api_client.get(
        f"{_event_url(tenant_ids[0])}/{created.json()['id']}",
        headers=auth_headers,
    )

    assert created.status_code == 202
    assert replay.status_code == 200
    assert fetched.status_code == 200
    assert created.json()["id"] == replay.json()["id"] == fetched.json()["id"]
    assert created.json()["correlation_id"] == str(correlation_id)
    assert replay.json()["idempotent_replay"] is True
    assert created.headers["X-Correlation-ID"] == str(correlation_id)
    assert created.headers["X-Request-ID"]


def test_same_idempotency_key_with_different_payload_conflicts(
    api_client: TestClient,
    tenant_ids: tuple[object, object],
    auth_headers: dict[str, str],
) -> None:
    headers = auth_headers | {"Idempotency-Key": "event-conflict"}
    first = api_client.post(
        _event_url(tenant_ids[0]),
        headers=headers,
        json={"kind": "demo.event", "attributes": {"count": 1}},
    )
    second = api_client.post(
        _event_url(tenant_ids[0]),
        headers=headers,
        json={"kind": "demo.event", "attributes": {"count": 2}},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"


def test_validation_does_not_echo_invalid_input(
    api_client: TestClient,
    tenant_ids: tuple[object, object],
    auth_headers: dict[str, str],
) -> None:
    response = api_client.post(
        _event_url(tenant_ids[0]),
        headers=auth_headers | {"Idempotency-Key": "invalid-body"},
        json={"kind": "demo.event", "attributes": {"token": ["must-not-echo"]}},
    )

    assert response.status_code == 422
    assert "must-not-echo" not in response.text


def test_invalid_correlation_id_is_rejected(
    api_client: TestClient,
    tenant_ids: tuple[object, object],
    auth_headers: dict[str, str],
) -> None:
    response = api_client.get(
        f"{_event_url(tenant_ids[0])}/{uuid4()}",
        headers=auth_headers | {"X-Correlation-ID": "invalid"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_unknown_tenant_is_not_disclosed(
    api_client: TestClient,
    tenant_ids: tuple[object, object],
    auth_headers: dict[str, str],
) -> None:
    assert tenant_ids
    response = api_client.post(
        _event_url(uuid4()),
        headers=auth_headers | {"Idempotency-Key": "unknown-tenant"},
        json={"kind": "demo.event", "attributes": {}},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_metrics_are_exposed_and_adapter_can_be_disabled(
    database_urls: DatabaseUrls,
) -> None:
    settings = build_settings(
        database_urls,
        metrics_enabled=True,
        synthetic_adapter_enabled=False,
        synthetic_adapter_token=None,
    )
    with TestClient(create_app(settings)) as client:
        metrics = client.get("/metrics")
        unavailable = client.post(
            _event_url(uuid4()),
            headers={"Idempotency-Key": "disabled"},
            json={"kind": "demo.event", "attributes": {}},
        )

    assert metrics.status_code == 200
    assert "saas_http_requests_total" in metrics.text
    assert unavailable.status_code == 404
