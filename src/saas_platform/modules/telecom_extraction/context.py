import json
import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from saas_platform.modules.lead_qualification.domain import ProfileField
from saas_platform.modules.telecom_extraction.domain import (
    ContextLimits,
    ConversationMessage,
    PreparedMessage,
    ProviderExtractionRequest,
    SpecialControl,
    SpecialControlFinding,
    TelecomExtractionCommand,
    extraction_idempotency_key,
)

EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?52[\s.-]?)?(?:\d[\s.-]?){10}(?!\d)")
SENSITIVE_ID_PATTERN = re.compile(
    r"\b(?:[A-Z]{4}\d{6}[A-Z0-9]{3}|[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d)\b",
    re.IGNORECASE,
)

DNC_PATTERNS = (
    re.compile(r"\bno\s+me\s+(?:escriban|escribas|contacten|contactes|llamen|llames)\b", re.I),
    re.compile(r"\b(?:dejen|deja)\s+de\s+(?:escribirme|contactarme|llamarme)\b", re.I),
    re.compile(r"\bno\s+quiero\s+(?:mas\s+)?(?:mensajes|llamadas|contacto)\b", re.I),
    re.compile(r"\b(?:stop messaging|do not contact|unsubscribe)\b", re.I),
)
HUMAN_PATTERNS = (
    re.compile(r"\b(?:hablar|comunicarme)\s+con\s+(?:un|una)\s+(?:asesor|persona|humano)\b", re.I),
    re.compile(r"\b(?:quiero|prefiero|necesito)\s+(?:un|una)?\s*(?:asesor|persona|humano)\b", re.I),
)
INJECTION_PATTERNS = (
    re.compile(r"\bignora\s+(?:tus|las|los)\s+instrucciones\b", re.I),
    re.compile(r"\b(?:marca|asigna|pon)\w*\s+(?:me\s+)?(?:con\s+)?100\s+puntos\b", re.I),
    re.compile(r"\bact[uú]a\s+como\s+administrador\b", re.I),
    re.compile(r"\b(?:muestra|dime)\s+(?:tus\s+)?instrucciones\s+internas\b", re.I),
    re.compile(r"\bdatos\s+de\s+otros\s+(?:clientes|tenants)\b", re.I),
)

EXCLUDED_PROFILE_CONTEXT = frozenset(
    {
        ProfileField.COVERAGE,
        ProfileField.INVENTORY,
        ProfileField.CUSTOMER_DECLARED_FINANCING_STATUS,
    }
)


@dataclass(frozen=True, slots=True)
class PreparedExtractionContext:
    request: ProviderExtractionRequest
    deterministic_controls: tuple[SpecialControlFinding, ...]
    original_messages: tuple[ConversationMessage, ...]


def redact_commercial_context(value: str) -> str:
    value = EMAIL_PATTERN.sub("[EMAIL_REDACTED]", value)
    value = PHONE_PATTERN.sub("[PHONE_REDACTED]", value)
    return SENSITIVE_ID_PATTERN.sub("[SENSITIVE_ID_REDACTED]", value)


class ExtractionContextPreparer:
    def __init__(self, limits: ContextLimits) -> None:
        self._limits = limits

    def prepare(
        self,
        command: TelecomExtractionCommand,
        *,
        now: object,
    ) -> PreparedExtractionContext:
        from datetime import datetime

        if not isinstance(now, datetime):
            raise TypeError("now must be a datetime")
        cutoff = now - timedelta(days=self._limits.max_age_days)
        chronological = sorted(command.messages, key=lambda item: item.sent_at)
        eligible = [item for item in chronological if item.sent_at >= cutoff]
        if not eligible:
            eligible = [chronological[-1]]
        eligible = eligible[-self._limits.max_messages :]

        selected_reversed: list[tuple[ConversationMessage, str]] = []
        consumed = 0
        for message in reversed(eligible):
            redacted = redact_commercial_context(message.content.strip())
            remaining = self._limits.max_characters - consumed
            if remaining <= 0:
                break
            if len(redacted) > remaining:
                if not selected_reversed:
                    redacted = redacted[-remaining:]
                else:
                    continue
            selected_reversed.append((message, redacted))
            consumed += len(redacted)

        selected = tuple(reversed(selected_reversed))
        original = tuple(item[0] for item in selected)
        prepared = tuple(
            PreparedMessage(
                id=message.id,
                role=message.role,
                content=content,
                sent_at=message.sent_at,
            )
            for message, content in selected
        )
        summary = (
            redact_commercial_context(command.previous_summary)[
                : self._limits.max_summary_characters
            ]
            if command.previous_summary
            else None
        )
        profile_context = self._profile_context(command)
        message_ids = tuple(message.id for message in original)
        request = ProviderExtractionRequest(
            tenant_id=command.profile.tenant_id,
            conversation_id=command.profile.conversation_id,
            messages=prepared,
            profile_context=profile_context,
            previous_summary=summary,
            recently_asked_fields=command.recently_asked_fields,
            expected_language=command.expected_language,
            prompt_version="telecom-extraction.v1",
            schema_version="telecom-extraction-result.v1",
            correlation_id=command.correlation_id,
            idempotency_key=extraction_idempotency_key(
                tenant_id=command.profile.tenant_id,
                conversation_id=command.profile.conversation_id,
                message_ids=message_ids,
            ),
            handoff_confirmed=command.handoff_confirmed,
        )
        return PreparedExtractionContext(
            request=request,
            deterministic_controls=self._controls(original),
            original_messages=original,
        )

    @staticmethod
    def _profile_context(command: TelecomExtractionCommand) -> dict[str, object]:
        facts = []
        for fact in command.profile.facts.values():
            if fact.field in EXCLUDED_PROFILE_CONTEXT:
                continue
            value = str(fact.value) if isinstance(fact.value, Decimal) else fact.value
            facts.append(
                {
                    "field": fact.field.value,
                    "value": value,
                    "status": fact.validation_status.value,
                    "confidence": str(fact.confidence),
                    "source_message_id": (
                        str(fact.source_message_id) if fact.source_message_id else None
                    ),
                }
            )
        facts.sort(key=lambda item: str(item["field"]))
        context: dict[str, object] = {
            "current_opportunity": command.profile.opportunity.primary.value,
            "secondary_opportunities": [
                item.value for item in command.profile.opportunity.secondary
            ],
            "facts": facts,
            "open_conflict_fields": [item.field.value for item in command.profile.conflicts],
        }
        json.dumps(context, ensure_ascii=True)
        return context

    @staticmethod
    def _controls(
        messages: tuple[ConversationMessage, ...],
    ) -> tuple[SpecialControlFinding, ...]:
        findings: list[SpecialControlFinding] = []
        patterns = (
            (SpecialControl.DO_NOT_CONTACT, DNC_PATTERNS),
            (SpecialControl.HUMAN_REQUESTED, HUMAN_PATTERNS),
            (SpecialControl.PROMPT_INJECTION, INJECTION_PATTERNS),
        )
        for message in messages:
            for control, expressions in patterns:
                match = None
                for expression in expressions:
                    match = expression.search(message.content)
                    if match is not None:
                        break
                if match is None:
                    continue
                findings.append(
                    SpecialControlFinding(
                        control=control,
                        confidence=Decimal("1.0"),
                        evidence=match.group(0)[:300],
                        source_message_id=message.id,
                        deterministic=True,
                    )
                )
        by_control: dict[SpecialControl, SpecialControlFinding] = {}
        for finding in findings:
            by_control[finding.control] = finding
        return tuple(by_control.values())
