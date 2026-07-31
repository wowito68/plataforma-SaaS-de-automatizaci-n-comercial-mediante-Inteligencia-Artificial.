from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from saas_platform.errors import ValidationError
from saas_platform.modules.lead_qualification.domain import OpportunityType, ProfileField
from saas_platform.modules.telecom_extraction.domain import (
    ConfidencePolicy,
    ContextLimits,
    ProviderExtractionResponse,
    ProviderUsage,
    TelecomExtractionCommand,
    extraction_idempotency_key,
)
from saas_platform.modules.telecom_extraction.errors import ExtractionInvalidResponseError
from saas_platform.modules.telecom_extraction.schema import (
    ProviderExtractionPayload,
    validate_provider_payload,
)
from tests.extraction_fixtures import (
    CONVERSATION_ID,
    MESSAGE_ID,
    OTHER_TENANT_ID,
    TENANT_ID,
    base_payload,
    make_command,
    make_field,
    make_message,
    make_profile,
)


def test_valid_payload_is_strictly_validated_and_canonicalized() -> None:
    payload = base_payload(
        fields=[make_field("brand", "Samsung", evidence="telefono")],
        missing=["device_budget"],
    )

    parsed, validated = validate_provider_payload(payload)

    assert parsed.opportunity.primary is OpportunityType.DEVICE_PURCHASE
    assert parsed.fields[0].field is ProfileField.BRAND
    assert validated.payload["language"] == "es"
    assert validated.validations == (
        "strict_schema",
        "known_enumerations",
        "bounded_values",
    )
    schema = ProviderExtractionPayload.model_json_schema()
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("forbidden", ["score", "eligibility", "weights", "tenant_id"])
def test_authoritative_or_unknown_top_level_fields_are_rejected(forbidden: str) -> None:
    payload = base_payload()
    payload[forbidden] = 100

    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)


def test_unknown_nested_fields_are_rejected() -> None:
    payload = base_payload()
    opportunity = payload["opportunity"]
    assert isinstance(opportunity, dict)
    opportunity["score"] = 100

    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("opportunity", "primary"), "new_magic_opportunity"),
        (("opportunity", "confidence"), 1.01),
        (("opportunity", "confidence"), "0.9"),
        (("intent", "value"), "approved"),
        (("urgency", "value"), "yesterday"),
        (("language",), "x"),
    ],
)
def test_invalid_types_enumerations_and_ranges_are_rejected(
    path: tuple[str, ...],
    value: object,
) -> None:
    payload = base_payload()
    target: dict[str, object] = payload
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)


def test_invalid_currency_and_field_enumerations_are_rejected() -> None:
    payload = base_payload(
        fields=[make_field("monthly_budget", 600, currency="EUR", evidence="telefono")]
    )
    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)

    payload = base_payload(fields=[make_field("magic_field", "x", evidence="telefono")])
    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)


def test_excessive_text_and_non_scalar_values_are_rejected() -> None:
    payload = base_payload(summary="x" * 1_001)
    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)

    payload = base_payload(fields=[make_field("brand", "Samsung", evidence="telefono")])
    fields = payload["fields"]
    assert isinstance(fields, list)
    fields[0]["value"] = ["Samsung"]
    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)


def test_duplicate_opportunities_and_missing_fields_are_rejected() -> None:
    payload = base_payload()
    opportunity = payload["opportunity"]
    assert isinstance(opportunity, dict)
    opportunity["secondary"] = ["device_purchase"]
    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)


@pytest.mark.parametrize("finding", ["intent", "urgency"])
def test_known_intent_and_urgency_require_evidence_provenance(finding: str) -> None:
    payload = base_payload(urgency="immediate")
    value = payload[finding]
    assert isinstance(value, dict)
    value["message_id"] = None
    value["evidence"] = ""

    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)


def test_handoff_requires_reason_evidence_and_source_message() -> None:
    payload = base_payload(handoff="general", handoff_confidence=0.95)
    handoff = payload["handoff"]
    assert isinstance(handoff, dict)
    handoff["evidence"] = ""

    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)

    payload = base_payload(missing=["brand", "brand"])
    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)


def test_non_json_serializable_payload_is_normalized_as_invalid_response() -> None:
    payload = base_payload()
    payload["warnings"] = {uuid4()}
    with pytest.raises(ExtractionInvalidResponseError):
        validate_provider_payload(payload)


def test_conversation_and_command_enforce_input_and_tenant_boundaries() -> None:
    with pytest.raises(ValidationError, match="empty"):
        make_message(" ")
    with pytest.raises(ValidationError, match="absolute size"):
        make_message("x" * 50_001)
    with pytest.raises(ValidationError, match="timezone"):
        make_message(sent_at=datetime(2026, 7, 31, 12, 0))
    with pytest.raises(ValidationError, match="cross tenant"):
        make_command((make_message(tenant_id=OTHER_TENANT_ID),))
    with pytest.raises(ValidationError, match="at least one"):
        TelecomExtractionCommand(
            profile=make_profile(),
            messages=(),
            correlation_id=uuid4(),
        )


def test_command_rejects_bad_language_summary_and_duplicate_recent_fields() -> None:
    with pytest.raises(ValidationError, match="language"):
        TelecomExtractionCommand(
            profile=make_profile(),
            messages=(make_message(),),
            correlation_id=uuid4(),
            expected_language="",
        )
    with pytest.raises(ValidationError, match="summary"):
        make_command(previous_summary="x" * 10_001)
    with pytest.raises(ValidationError, match="recently asked"):
        TelecomExtractionCommand(
            profile=make_profile(),
            messages=(make_message(),),
            correlation_id=uuid4(),
            recently_asked_fields=(ProfileField.BRAND, ProfileField.BRAND),
        )


def test_policy_and_provider_metadata_validate_ranges() -> None:
    assert ContextLimits().max_messages == 12
    with pytest.raises(ValidationError, match="positive"):
        ContextLimits(max_messages=0)
    with pytest.raises(ValidationError, match="ordered"):
        ConfidencePolicy(
            auto_accept=Decimal("0.5"),
            provisional=Decimal("0.8"),
        )
    with pytest.raises(ValidationError, match="between 0 and 1"):
        ConfidencePolicy(auto_accept=Decimal("1.1"))
    with pytest.raises(ValidationError, match="token"):
        ProviderUsage(input_tokens=-1)
    with pytest.raises(ValidationError, match="cost"):
        ProviderUsage(estimated_cost_usd=Decimal("-1"))
    with pytest.raises(ValidationError, match="metadata"):
        ProviderExtractionResponse({}, "", "model", 0, "completed")
    with pytest.raises(ValidationError, match="latency"):
        ProviderExtractionResponse({}, "provider", "model", -1, "completed")


def test_idempotency_key_is_stable_and_version_sensitive() -> None:
    first = extraction_idempotency_key(
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_ids=(MESSAGE_ID,),
    )
    replay = extraction_idempotency_key(
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_ids=(MESSAGE_ID,),
    )
    changed = extraction_idempotency_key(
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_ids=(MESSAGE_ID,),
        prompt_version="telecom-extraction.v2",
    )

    assert first == replay
    assert first != changed
    assert len(first) == 64


def test_payload_copy_is_immutable_at_the_provider_boundary() -> None:
    payload = base_payload()
    original = deepcopy(payload)
    response = ProviderExtractionResponse(payload, "provider", "model", 0, "completed")
    payload["language"] = "en"

    assert response.payload["language"] == original["language"]
