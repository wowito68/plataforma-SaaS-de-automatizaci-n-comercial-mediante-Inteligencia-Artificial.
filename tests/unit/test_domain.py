from uuid import uuid4

import pytest

from saas_platform.errors import ValidationError
from saas_platform.modules.synthetic_events.domain import IdempotencyKey, SyntheticPayload
from saas_platform.modules.tenancy.domain import Tenant, TenantId


def test_payload_hash_is_canonical() -> None:
    first = SyntheticPayload("demo.accepted", {"count": 2, "active": True})
    second = SyntheticPayload("demo.accepted", {"active": True, "count": 2})

    assert first.sha256() == second.sha256()
    assert first.as_dict()["kind"] == "demo.accepted"


@pytest.mark.parametrize(
    ("kind", "attributes"),
    [
        ("Invalid Kind", {}),
        ("valid", {"Invalid Key": "value"}),
        ("valid", {f"key{index}": index for index in range(21)}),
        ("valid", {"text": "x" * 257}),
    ],
)
def test_payload_rejects_invalid_values(kind: str, attributes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SyntheticPayload(kind, attributes)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", "contains space", "x" * 129])
def test_idempotency_key_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValidationError):
        IdempotencyKey(value)


def test_tenant_validates_and_normalizes_name() -> None:
    tenant = Tenant(TenantId(uuid4()), "  Example  ")

    assert tenant.name == "Example"


def test_tenant_id_parse_translates_invalid_uuid() -> None:
    with pytest.raises(ValidationError, match="valid UUID"):
        TenantId.parse("not-a-uuid")
