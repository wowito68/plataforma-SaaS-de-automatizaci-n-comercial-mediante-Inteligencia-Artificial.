import logging
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from time import perf_counter, sleep

from saas_platform.errors import PermanentError, TransientError
from saas_platform.foundation import Clock, IdGenerator
from saas_platform.modules.lead_qualification.domain import OpportunityType
from saas_platform.modules.telecom_extraction.context import (
    ExtractionContextPreparer,
    PreparedExtractionContext,
)
from saas_platform.modules.telecom_extraction.domain import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    AcceptedExtraction,
    CommercialIntent,
    CommercialUrgency,
    ConfidencePolicy,
    ExtractedOpportunity,
    ExtractionAuditRecord,
    ExtractionFailure,
    ExtractionOutcome,
    HandoffKind,
    NormalizedExtraction,
    ProfileUpdateProposal,
    ProviderExtractionResponse,
    ProviderUsage,
    SpecialControl,
    TelecomExtractionCommand,
    TelecomExtractionResult,
    ValidatedExtraction,
)
from saas_platform.modules.telecom_extraction.errors import ExtractionInvalidResponseError
from saas_platform.modules.telecom_extraction.ports import (
    CommercialExtractionProvider,
    ExtractionTelemetry,
    RetrySleeper,
)
from saas_platform.modules.telecom_extraction.processing import (
    ConfidenceResolver,
    ContradictionReconciler,
    ExtractionDecisionService,
    ExtractionDomainMapper,
    ExtractionNormalizer,
)
from saas_platform.modules.telecom_extraction.schema import validate_provider_payload
from saas_platform.modules.telecom_extraction.telemetry import NullExtractionTelemetry
from saas_platform.observability import correlation_id_var, tenant_id_var

logger = logging.getLogger(__name__)


class SystemRetrySleeper:
    def sleep(self, seconds: float) -> None:
        sleep(seconds)


class ExtractTelecomConversation:
    def __init__(
        self,
        provider: CommercialExtractionProvider,
        context_preparer: ExtractionContextPreparer,
        confidence_policy: ConfidencePolicy,
        id_generator: IdGenerator,
        clock: Clock,
        *,
        provider_name: str,
        model: str,
        max_retries: int = 1,
        telemetry: ExtractionTelemetry | None = None,
        retry_sleeper: RetrySleeper | None = None,
    ) -> None:
        if max_retries < 0 or max_retries > 3:
            raise ValueError("max_retries must be between 0 and 3")
        self._provider = provider
        self._context_preparer = context_preparer
        self._confidence = ConfidenceResolver(confidence_policy)
        self._reconciler = ContradictionReconciler()
        self._decisions = ExtractionDecisionService(confidence_policy)
        self._normalizer = ExtractionNormalizer()
        self._mapper = ExtractionDomainMapper()
        self._id_generator = id_generator
        self._clock = clock
        self._provider_name = provider_name
        self._model = model
        self._max_retries = max_retries
        self._telemetry = telemetry or NullExtractionTelemetry()
        self._sleeper = retry_sleeper or SystemRetrySleeper()

    def execute(self, command: TelecomExtractionCommand) -> TelecomExtractionResult:
        execution_id = self._id_generator.new()
        executed_at = self._clock.now()
        prepared = self._context_preparer.prepare(command, now=executed_at)
        tenant_token = tenant_id_var.set(str(command.profile.tenant_id.value))
        correlation_token = correlation_id_var.set(str(command.correlation_id))
        started = perf_counter()
        self._telemetry.started(
            provider=self._provider_name,
            model=self._model,
            prompt_version=PROMPT_VERSION,
        )
        try:
            return self._attempt(
                command=command,
                prepared=prepared,
                execution_id=execution_id,
                executed_at=executed_at,
                started=started,
            )
        finally:
            correlation_id_var.reset(correlation_token)
            tenant_id_var.reset(tenant_token)

    def _attempt(
        self,
        *,
        command: TelecomExtractionCommand,
        prepared: PreparedExtractionContext,
        execution_id: object,
        executed_at: datetime,
        started: float,
    ) -> TelecomExtractionResult:
        from uuid import UUID

        if not isinstance(execution_id, UUID):
            raise TypeError("execution_id must be a UUID")
        failures: list[ExtractionFailure] = []
        raw_response: ProviderExtractionResponse | None = None
        validated: ValidatedExtraction | None = None
        for attempt in range(self._max_retries + 1):
            request = replace(
                prepared.request,
                repair_schema=any(
                    item.code == ExtractionInvalidResponseError.code for item in failures
                ),
            )
            try:
                raw_response = self._provider.extract_commercial_data(request)
                payload, validated = validate_provider_payload(raw_response.payload)
                normalized = self._normalizer.normalize(
                    payload,
                    prepared.original_messages,
                    prepared.deterministic_controls,
                )
                validated = replace(
                    validated,
                    validations=validated.validations
                    + ("tenant_message_references", "semantic_units", "authority_boundaries"),
                )
                return self._complete(
                    command=command,
                    prepared=prepared,
                    response=raw_response,
                    validated=validated,
                    normalized=normalized,
                    execution_id=execution_id,
                    executed_at=executed_at,
                    started=started,
                    failures=tuple(failures),
                )
            except (TransientError, PermanentError) as exc:
                failure = ExtractionFailure(
                    code=exc.code,
                    retryable=isinstance(
                        exc,
                        (TransientError, ExtractionInvalidResponseError),
                    ),
                )
                failures.append(failure)
                self._telemetry.failed(
                    provider=self._provider_name,
                    model=self._model,
                    error_code=exc.code,
                )
                can_retry = failure.retryable and attempt < self._max_retries
                if not can_retry:
                    break
                if isinstance(exc, ExtractionInvalidResponseError):
                    self._telemetry.repair_attempted(
                        provider=self._provider_name,
                        model=self._model,
                    )
                self._sleeper.sleep(0.25 * (2**attempt))

        return self._degraded(
            command=command,
            prepared=prepared,
            execution_id=execution_id,
            executed_at=executed_at,
            started=started,
            failures=tuple(failures),
            response=raw_response,
            validated=validated,
        )

    def _complete(
        self,
        *,
        command: TelecomExtractionCommand,
        prepared: PreparedExtractionContext,
        response: ProviderExtractionResponse,
        validated: ValidatedExtraction,
        normalized: NormalizedExtraction,
        execution_id: object,
        executed_at: datetime,
        started: float,
        failures: tuple[ExtractionFailure, ...],
    ) -> TelecomExtractionResult:
        from uuid import UUID

        assert isinstance(execution_id, UUID)
        resolved_fields = self._confidence.resolve_fields(
            normalized,
            prepared.original_messages,
        )
        reconciled_fields, contradictions = self._reconciler.reconcile(
            resolved_fields,
            command.profile,
        )
        resolved_signals = self._confidence.resolve_signals(
            normalized,
            prepared.original_messages,
        )
        accepted = self._decisions.accept(
            normalized,
            reconciled_fields,
            contradictions,
            resolved_signals,
            command,
        )
        update = self._mapper.apply(
            command,
            accepted,
            execution_id=execution_id,
            provider=response.provider,
            model=response.model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            now=executed_at,
        )
        audit = self._audit(
            execution_id=execution_id,
            prepared=prepared,
            response=response,
            validated=validated,
            accepted=accepted,
            update=update,
            failures=failures,
        )
        duration = perf_counter() - started
        dnc = any(item.control is SpecialControl.DO_NOT_CONTACT for item in accepted.controls)
        self._telemetry.completed(
            provider=response.provider,
            model=response.model,
            prompt_version=PROMPT_VERSION,
            duration_seconds=duration,
            confidence=float(accepted.overall_confidence),
            field_count=len(accepted.fields),
            contradiction_count=len(accepted.contradictions),
            no_contact=dnc,
            handoff=accepted.handoff.required,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            estimated_cost_usd=(
                float(response.usage.estimated_cost_usd)
                if response.usage.estimated_cost_usd is not None
                else None
            ),
        )
        self._log_result(
            execution_id,
            response.provider,
            response.model,
            response.latency_ms,
            ExtractionOutcome.COMPLETED,
            failures,
            response.usage,
        )
        return TelecomExtractionResult(
            execution_id=execution_id,
            tenant_id=command.profile.tenant_id,
            conversation_id=command.profile.conversation_id,
            message_ids=tuple(item.id for item in prepared.original_messages),
            idempotency_key=prepared.request.idempotency_key,
            outcome=ExtractionOutcome.COMPLETED,
            accepted=accepted,
            update=update,
            provider=response.provider,
            model=response.model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            executed_at=executed_at,
            usage=response.usage,
            latency_ms=response.latency_ms,
            warnings=normalized.warnings,
            recoverable_errors=failures,
            audit=audit,
        )

    def _degraded(
        self,
        *,
        command: TelecomExtractionCommand,
        prepared: PreparedExtractionContext,
        execution_id: object,
        executed_at: datetime,
        started: float,
        failures: tuple[ExtractionFailure, ...],
        response: ProviderExtractionResponse | None,
        validated: ValidatedExtraction | None,
    ) -> TelecomExtractionResult:
        from uuid import UUID

        assert isinstance(execution_id, UUID)
        fallback = NormalizedExtraction(
            language=command.expected_language,
            opportunity=ExtractedOpportunity(
                opportunity=command.profile.opportunity,
                confidence=(
                    Decimal("1")
                    if command.profile.opportunity.primary is not OpportunityType.UNIDENTIFIED
                    else Decimal("0")
                ),
                evidence="existing profile only",
                source_message_id=prepared.original_messages[-1].id,
            ),
            fields=(),
            signals=(),
            intent=CommercialIntent.UNKNOWN,
            intent_confidence=Decimal("0"),
            intent_evidence="",
            intent_source_message_id=None,
            urgency=CommercialUrgency.UNSPECIFIED,
            urgency_confidence=Decimal("0"),
            urgency_evidence="",
            urgency_source_message_id=None,
            provider_missing_information=(),
            controls=prepared.deterministic_controls,
            provider_handoff=HandoffKind.NONE,
            provider_handoff_reason="",
            provider_handoff_confidence=Decimal("0"),
            provider_question_target=None,
            provider_question=None,
            provider_question_reason=None,
            provider_question_priority=None,
            provider_question_alternatives=(),
            provider_question_skippable=True,
            summary="Structured extraction unavailable; the existing profile was preserved.",
            declared_overall_confidence=Decimal("0"),
            warnings=("provider_degraded_mode",),
            recoverable_errors=tuple(item.code for item in failures),
        )
        accepted = self._decisions.accept(fallback, (), (), (), command)
        accepted = replace(accepted, next_question=None)
        provider = response.provider if response is not None else self._provider_name
        model = response.model if response is not None else self._model
        usage = response.usage if response is not None else ProviderUsage()
        latency_ms = (
            response.latency_ms
            if response is not None
            else round((perf_counter() - started) * 1000)
        )
        update = self._mapper.apply(
            command,
            accepted,
            execution_id=execution_id,
            provider=provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            now=executed_at,
        )
        audit = self._audit(
            execution_id=execution_id,
            prepared=prepared,
            response=response,
            validated=validated,
            accepted=accepted,
            update=update,
            failures=failures,
        )
        self._log_result(
            execution_id,
            provider,
            model,
            latency_ms,
            ExtractionOutcome.DEGRADED,
            failures,
            usage,
        )
        return TelecomExtractionResult(
            execution_id=execution_id,
            tenant_id=command.profile.tenant_id,
            conversation_id=command.profile.conversation_id,
            message_ids=tuple(item.id for item in prepared.original_messages),
            idempotency_key=prepared.request.idempotency_key,
            outcome=ExtractionOutcome.DEGRADED,
            accepted=accepted,
            update=update,
            provider=provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            executed_at=executed_at,
            usage=usage,
            latency_ms=latency_ms,
            warnings=fallback.warnings,
            recoverable_errors=failures,
            audit=audit,
        )

    @staticmethod
    def _audit(
        *,
        execution_id: object,
        prepared: PreparedExtractionContext,
        response: ProviderExtractionResponse | None,
        validated: ValidatedExtraction | None,
        accepted: AcceptedExtraction,
        update: ProfileUpdateProposal,
        failures: tuple[ExtractionFailure, ...],
    ) -> ExtractionAuditRecord:
        from uuid import UUID

        assert isinstance(execution_id, UUID)
        return ExtractionAuditRecord(
            execution_id=execution_id,
            message_ids=tuple(item.id for item in prepared.original_messages),
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            provider=response.provider if response else "unavailable",
            model=response.model if response else "unavailable",
            raw_structured_payload=(dict(response.payload) if response else None),
            validations=validated.validations if validated else (),
            accepted_fields=tuple(item.value.field for item in accepted.fields),
            rejected_fields=tuple(item.value.field for item in accepted.rejected_fields),
            contradictions=tuple(item.field for item in accepted.contradictions),
            proposed_updates=update.facts_added,
            handoff_reason_codes=accepted.handoff.reason_codes,
            failures=failures,
        )

    @staticmethod
    def _log_result(
        execution_id: object,
        provider: str,
        model: str,
        latency_ms: int,
        outcome: ExtractionOutcome,
        failures: tuple[ExtractionFailure, ...],
        usage: ProviderUsage,
    ) -> None:
        logger.info(
            "structured telecom extraction finished",
            extra={
                "execution_id": execution_id,
                "provider": provider,
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "duration_ms": latency_ms,
                "outcome": outcome.value,
                "error_type": failures[-1].code if failures else None,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        )
