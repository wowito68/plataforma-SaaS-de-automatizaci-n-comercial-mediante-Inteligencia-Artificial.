from dataclasses import replace
from decimal import Decimal

import pytest

from saas_platform.modules.lead_qualification.domain import (
    ExtractionMethod,
    FactSource,
    LeadProfile,
    OpportunityType,
    ProfileFact,
    ProfileField,
    SignalCode,
    ValidationStatus,
)
from saas_platform.modules.telecom_extraction.domain import (
    CommercialIntent,
    ConfidenceTreatment,
    ExtractionOutcome,
    HandoffKind,
    SpecialControl,
    TelecomExtractionResult,
)
from saas_platform.modules.telecom_extraction.processing import REQUIRED_FIELDS
from tests.extraction_fixtures import (
    MESSAGE_ID,
    NOW,
    SECOND_MESSAGE_ID,
    base_payload,
    make_command,
    make_control,
    make_field,
    make_message,
    make_profile,
    make_service,
    make_signal,
    provider_response,
)


def run_payload(
    payload: dict[str, object],
    *,
    content: str = "Quiero un telefono.",
    profile: LeadProfile | None = None,
) -> TelecomExtractionResult:
    service, _ = make_service([provider_response(payload)])
    command = make_command(
        (make_message(content),),
        profile=profile if profile is not None else None,
    )
    return service.execute(command)


@pytest.mark.parametrize(
    ("field", "value", "currency"),
    [
        ("monthly_budget", 600, None),
        ("monthly_budget", True, "MXN"),
        ("monthly_budget", -1, "MXN"),
        ("monthly_budget", "not-a-number", "MXN"),
        ("brand", "Samsung", "MXN"),
        ("line_count", 1.5, None),
        ("line_count", -1, None),
        ("calls_required", "yes", None),
        ("cutoff_date", 20260731, None),
        ("cutoff_date", "not-a-date", None),
        ("brand", " ", None),
    ],
)
def test_semantically_invalid_units_and_values_never_update_profile(
    field: str,
    value: str | int | float | bool,
    currency: str | None,
) -> None:
    payload = base_payload(
        fields=[
            make_field(
                field,
                value,
                evidence="telefono",
                currency=currency,
            )
        ]
    )

    result = run_payload(payload)

    assert result.outcome is ExtractionOutcome.DEGRADED
    assert result.update.updated_profile.facts == {}
    assert result.recoverable_errors[-1].code == "extraction_invalid_response"


def test_provider_cannot_reference_a_message_outside_prepared_context() -> None:
    payload = base_payload(message_id=SECOND_MESSAGE_ID)

    result = run_payload(payload)

    assert result.outcome is ExtractionOutcome.DEGRADED
    assert result.audit.validations == (
        "strict_schema",
        "known_enumerations",
        "bounded_values",
    )


def test_normalizer_canonicalizes_money_dates_text_boolean_and_numbers() -> None:
    content = "$600 12 llamadas 2026-08-01 SAMSUNG CONTADO PREPAGO persona 1.5 producto7"
    payload = base_payload(
        evidence="SAMSUNG",
        fields=[
            make_field("monthly_budget", 600.25, evidence="$600", currency="MXN"),
            make_field("line_count", 12, evidence="12"),
            make_field("calls_required", True, evidence="llamadas"),
            make_field("cutoff_date", "2026-08-01", evidence="2026-08-01"),
            make_field("brand", "SAMSUNG", evidence="SAMSUNG"),
            make_field("payment_method", "CONTADO", evidence="CONTADO"),
            make_field("service_modality", "PREPAGO", evidence="PREPAGO"),
            make_field("customer_type", "PERSONA", evidence="persona"),
            make_field("requested_action", 1.5, evidence="1.5"),
            make_field("product", 7, evidence="producto7"),
        ],
    )

    result = run_payload(payload, content=content)
    facts = result.update.updated_profile.facts

    assert result.outcome is ExtractionOutcome.COMPLETED
    assert facts[ProfileField.MONTHLY_BUDGET].value == Decimal("600.25")
    assert facts[ProfileField.LINE_COUNT].value == 12
    assert facts[ProfileField.CALLS_REQUIRED].value is True
    assert facts[ProfileField.CUTOFF_DATE].value == "2026-08-01"
    assert facts[ProfileField.BRAND].value == "Samsung"
    assert facts[ProfileField.PAYMENT_METHOD].value == "contado"
    assert facts[ProfileField.SERVICE_MODALITY].value == "prepago"
    assert facts[ProfileField.CUSTOMER_TYPE].value == "persona"
    assert facts[ProfileField.REQUESTED_ACTION].value == Decimal("1.5")
    assert facts[ProfileField.PRODUCT].value == 7


def test_confidence_uses_evidence_state_support_and_central_thresholds() -> None:
    first = make_message(
        "Samsung modelo azul equipos precio requisitos bajo",
        message_id=MESSAGE_ID,
    )
    second = make_message("Samsung", message_id=SECOND_MESSAGE_ID, sent_at=NOW.replace(minute=1))
    payload = base_payload(
        message_id=SECOND_MESSAGE_ID,
        evidence="Samsung",
        fields=[
            make_field("brand", "Samsung", evidence="Samsung", state="inferred"),
            make_field(
                "brand",
                "Samsung",
                message_id=SECOND_MESSAGE_ID,
                evidence="Samsung",
                state="inferred",
            ),
            make_field("model", "modelo", evidence="not-verbatim", confidence=0.9),
            make_field("color", "azul", evidence="azul", confidence=0.99, state="unknown"),
            make_field(
                "product_category",
                "equipos",
                evidence="equipos",
                confidence=0.55,
            ),
            make_field(
                "storage_capacity",
                "bajo",
                evidence="bajo",
                confidence=0.2,
            ),
        ],
        signals=[
            make_signal("asks_price", evidence="precio", confidence=0.95),
            make_signal("asks_requirements", evidence="requisitos", confidence=0.75),
            make_signal("requests_quote", evidence="modelo", confidence=0.5),
            make_signal("general_information", evidence="bajo", confidence=0.3),
            make_signal("compares_products", evidence="missing", confidence=0.95),
        ],
    )
    service, _ = make_service([provider_response(payload)])

    result = service.execute(make_command((first, second)))

    fields = {item.value.field: item for item in result.accepted.fields}
    rejected = {item.value.field: item for item in result.accepted.rejected_fields}
    assert fields[ProfileField.BRAND].confidence == Decimal("0.85")
    assert fields[ProfileField.BRAND].treatment is ConfidenceTreatment.PROVISIONAL
    assert fields[ProfileField.MODEL].confidence == Decimal("0.70")
    assert rejected[ProfileField.COLOR].treatment is ConfidenceTreatment.REJECTED
    assert (
        rejected[ProfileField.PRODUCT_CATEGORY].treatment
        is ConfidenceTreatment.CONFIRMATION_REQUIRED
    )
    assert rejected[ProfileField.STORAGE_CAPACITY].treatment is ConfidenceTreatment.REJECTED
    assert result.update.updated_profile.facts[ProfileField.BRAND].validation_status is (
        ValidationStatus.HYPOTHESIS
    )
    assert {item.value.code for item in result.accepted.signals} == {SignalCode.ASKS_PRICE}
    assert {item.treatment for item in result.accepted.rejected_signals} == {
        ConfidenceTreatment.PROVISIONAL,
        ConfidenceTreatment.CONFIRMATION_REQUIRED,
        ConfidenceTreatment.REJECTED,
    }


def test_two_new_competing_values_create_conflict_without_choosing_either() -> None:
    first = make_message("Quiero rojo.", message_id=MESSAGE_ID)
    second = make_message(
        "Mejor azul.",
        message_id=SECOND_MESSAGE_ID,
        sent_at=NOW.replace(minute=1),
    )
    payload = base_payload(
        message_id=SECOND_MESSAGE_ID,
        evidence="azul",
        fields=[
            make_field("color", "rojo", evidence="rojo", message_id=MESSAGE_ID),
            make_field("color", "azul", evidence="azul", message_id=SECOND_MESSAGE_ID),
        ],
    )
    service, _ = make_service([provider_response(payload)])

    result = service.execute(make_command((first, second)))

    assert ProfileField.COLOR not in result.update.updated_profile.facts
    assert result.update.conflicts_added == 1
    assert result.accepted.contradictions[0].previous_evidence == "rojo"
    assert result.accepted.rejected_fields[0].treatment is ConfidenceTreatment.CONTRADICTED


def test_confirmed_fact_is_preserved_even_against_high_confidence_ai() -> None:
    confirmed = ProfileFact(
        field=ProfileField.BRAND,
        value="Apple",
        source=FactSource.HUMAN_ADVISOR,
        source_message_id=None,
        observed_at=NOW,
        confidence=Decimal("1"),
        extraction_method=ExtractionMethod.HUMAN,
        validation_status=ValidationStatus.CONFIRMED,
        confirmed_by="advisor-1",
    )
    profile = make_profile(facts={ProfileField.BRAND: confirmed})
    payload = base_payload(
        evidence="Samsung",
        fields=[make_field("brand", "Samsung", evidence="Samsung")],
    )

    result = run_payload(payload, content="Quiero Samsung.", profile=profile)

    assert result.update.updated_profile.facts[ProfileField.BRAND] is confirmed
    assert "confirmed value preserved" in result.update.updated_profile.conflicts[-1].reason


def test_same_existing_fact_is_preserved_without_a_conflict() -> None:
    existing = ProfileFact(
        field=ProfileField.BRAND,
        value="Samsung",
        source=FactSource.CUSTOMER,
        source_message_id=MESSAGE_ID,
        observed_at=NOW,
        confidence=Decimal("0.8"),
        extraction_method=ExtractionMethod.AI,
        validation_status=ValidationStatus.HYPOTHESIS,
    )
    profile = make_profile(facts={ProfileField.BRAND: existing})
    payload = base_payload(
        evidence="Samsung",
        fields=[make_field("brand", "Samsung", evidence="Samsung")],
    )

    result = run_payload(payload, content="Samsung", profile=profile)

    assert result.update.facts_preserved == (ProfileField.BRAND,)
    assert result.update.conflicts_added == 0
    assert result.update.updated_profile.facts[ProfileField.BRAND] is existing


@pytest.mark.parametrize("opportunity", tuple(OpportunityType))
def test_missing_information_is_relevant_to_each_opportunity(
    opportunity: OpportunityType,
) -> None:
    payload = base_payload(
        opportunity=opportunity.value,
        evidence="telefono",
    )

    result = run_payload(payload)

    assert result.accepted.missing_information == REQUIRED_FIELDS[opportunity]


def test_recently_asked_field_is_skipped_when_selecting_next_question() -> None:
    payload = base_payload()
    service, _ = make_service([provider_response(payload)])

    result = service.execute(make_command(recently_asked_fields=(ProfileField.BRAND,)))

    assert result.accepted.next_question is not None
    assert result.accepted.next_question.target_field is ProfileField.DEVICE_BUDGET


def test_no_question_is_emitted_when_all_relevant_fields_are_known() -> None:
    facts = {
        field: ProfileFact(
            field=field,
            value="known",
            source=FactSource.CUSTOMER,
            source_message_id=MESSAGE_ID,
            observed_at=NOW,
            confidence=Decimal("1"),
            extraction_method=ExtractionMethod.EXPLICIT,
            validation_status=ValidationStatus.ACCEPTED,
        )
        for field in REQUIRED_FIELDS[OpportunityType.DEVICE_PURCHASE]
    }
    profile = make_profile(OpportunityType.DEVICE_PURCHASE, facts=facts)

    result = run_payload(base_payload(), profile=profile)

    assert result.accepted.missing_information == ()
    assert result.accepted.next_question is None


@pytest.mark.parametrize(
    ("control", "expected"),
    [
        ("complaint", HandoffKind.SPECIALIZED),
        ("suspected_fraud", HandoffKind.SPECIALIZED),
    ],
)
def test_risk_controls_trigger_specialized_handoff(control: str, expected: HandoffKind) -> None:
    payload = base_payload(
        controls=[make_control(control, evidence="telefono")],
    )

    result = run_payload(payload)

    assert result.accepted.handoff.kind is expected


def test_high_confidence_provider_handoff_is_advisory_but_validatable() -> None:
    payload = base_payload(
        handoff="general",
        handoff_confidence=0.95,
    )
    result = run_payload(payload)

    assert result.accepted.handoff.kind is HandoffKind.GENERAL
    assert result.accepted.handoff.reason_codes == ("validated_provider_suggestion",)


def test_low_confidence_controls_and_handoff_are_ignored() -> None:
    payload = base_payload(
        controls=[make_control("complaint", evidence="telefono", confidence=0.5)],
        handoff="specialized",
        handoff_confidence=0.5,
    )

    result = run_payload(payload)

    assert result.accepted.controls == ()
    assert result.accepted.handoff.kind is HandoffKind.NONE


def test_unverified_control_intent_urgency_and_handoff_evidence_are_downgraded() -> None:
    payload = base_payload(
        opportunity="portability",
        opportunity_confidence=0.8,
        evidence="not-present",
        controls=[make_control("complaint", evidence="not-present")],
        intent="purchase",
        urgency="immediate",
        handoff="general",
        handoff_confidence=0.95,
    )
    intent_finding = payload["intent"]
    urgency_finding = payload["urgency"]
    assert isinstance(intent_finding, dict)
    assert isinstance(urgency_finding, dict)
    intent_finding["confidence"] = 0.95
    urgency_finding["confidence"] = 0.95

    result = run_payload(payload)

    assert result.accepted.opportunity.opportunity.primary is OpportunityType.UNIDENTIFIED
    assert result.accepted.intent_treatment is ConfidenceTreatment.PROVISIONAL
    assert result.accepted.urgency_treatment is ConfidenceTreatment.PROVISIONAL
    assert result.accepted.controls == ()
    assert result.accepted.handoff.kind is HandoffKind.NONE
    assert result.update.qualification_request.signals == ()


@pytest.mark.parametrize(
    "unsafe_question",
    [
        "¿Cuál es tu teléfono y RFC?",
        "¿Qué marca quieres? ¿Cuál es tu correo?",
        "x" * 181,
    ],
)
def test_provider_question_must_pass_local_safety_rules(unsafe_question: str) -> None:
    payload = base_payload(
        next_question={
            "target_field": "brand",
            "text": unsafe_question,
            "reason": "Complete brand.",
            "priority": 1,
            "alternatives": [],
            "skippable": True,
        }
    )

    result = run_payload(payload)

    assert result.accepted.next_question is not None
    assert result.accepted.next_question.target_field is ProfileField.BRAND
    assert result.accepted.next_question.text != unsafe_question
    assert "telefono" not in result.accepted.next_question.text.casefold()


def test_confirmed_handoff_suppresses_further_qualification_questions() -> None:
    service, _ = make_service([provider_response(base_payload())])
    command = replace(make_command(), handoff_confirmed=True)

    result = service.execute(command)

    assert result.outcome is ExtractionOutcome.COMPLETED
    assert result.accepted.missing_information
    assert result.accepted.next_question is None


def test_authoritative_language_in_summary_is_replaced() -> None:
    payload = base_payload(summary="Lead score: 100 and financing approved.")

    result = run_payload(payload)

    assert "100" not in result.accepted.summary
    assert "blocked_authoritative_summary" in result.warnings


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("comparison", SignalCode.COMPARES_PRODUCTS),
        ("price_inquiry", SignalCode.ASKS_PRICE),
        ("monthly_payment_inquiry", SignalCode.ASKS_MONTHLY_PAYMENT),
        ("inventory_inquiry", SignalCode.ASKS_AVAILABILITY),
        ("requirements_inquiry", SignalCode.ASKS_REQUIREMENTS),
        ("quote_request", SignalCode.REQUESTS_QUOTE),
        ("portability_request", SignalCode.REQUESTS_PORTABILITY),
        ("purchase", SignalCode.WANTS_CONTRACT_NOW),
    ],
)
def test_validated_intent_maps_to_existing_domain_signal(
    intent: str,
    expected: SignalCode,
) -> None:
    result = run_payload(base_payload(intent=intent))

    assert expected in {item.code for item in result.update.qualification_request.signals}


def test_opportunity_update_is_conservative_and_merges_only_compatible_secondary_types() -> None:
    existing = make_profile(OpportunityType.DEVICE_PURCHASE)
    different = run_payload(
        base_payload(opportunity="plan_subscription"),
        profile=existing,
    )
    assert different.update.updated_profile.opportunity.primary is OpportunityType.DEVICE_PURCHASE

    payload = base_payload(opportunity="device_purchase")
    opportunity = payload["opportunity"]
    assert isinstance(opportunity, dict)
    opportunity["secondary"] = ["accessory"]
    merged = run_payload(payload, profile=existing)
    assert merged.update.updated_profile.opportunity.secondary == (OpportunityType.ACCESSORY,)


def test_audit_retains_stages_without_chain_of_thought() -> None:
    result = run_payload(base_payload())

    assert result.audit.raw_structured_payload is not None
    assert result.audit.validations == (
        "strict_schema",
        "known_enumerations",
        "bounded_values",
        "tenant_message_references",
        "semantic_units",
        "authority_boundaries",
    )
    assert "reasoning" not in result.audit.raw_structured_payload
    assert result.audit.message_ids == (MESSAGE_ID,)


def test_provider_dnc_control_is_accepted_only_at_high_confidence() -> None:
    payload = base_payload(
        controls=[make_control("do_not_contact", evidence="telefono")],
    )

    result = run_payload(payload)

    control = next(
        item for item in result.accepted.controls if item.control is SpecialControl.DO_NOT_CONTACT
    )
    signal = next(
        item
        for item in result.update.qualification_request.signals
        if item.code is SignalCode.DO_NOT_CONTACT
    )
    assert control.deterministic is False
    assert signal.extraction_method is ExtractionMethod.AI


def test_unknown_intent_adds_no_intent_signal() -> None:
    result = run_payload(base_payload(intent=CommercialIntent.UNKNOWN.value))

    assert result.update.qualification_request.signals == ()
