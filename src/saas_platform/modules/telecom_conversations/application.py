import hashlib
import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from time import perf_counter
from uuid import UUID

from saas_platform.errors import PermanentError, TransientError, ValidationError
from saas_platform.foundation import Clock, IdGenerator
from saas_platform.modules.lead_qualification.domain import (
    EligibilityStatus,
    LeadClassification,
    LeadState,
)
from saas_platform.modules.telecom_conversations.domain import (
    Contact,
    Conversation,
    ConversationState,
    ConversationStateMachine,
    HandoffType,
    IngestConversationResult,
    Lead,
    LeadStateMachine,
    MessageDirection,
    MessageProcessingStatus,
    NormalizedConversationMessage,
    ProcessConversationResult,
    ProcessingClaim,
    ProcessingOutcome,
    StoredMessage,
    TransitionActor,
)
from saas_platform.modules.telecom_conversations.errors import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
    InvalidExtractionError,
)
from saas_platform.modules.telecom_conversations.ports import (
    AuditEventData,
    ConversationUnitOfWork,
    ConversationUnitOfWorkFactory,
    OutboxEventData,
    ProcessingContext,
    QualificationEvaluator,
    TelecomExtractionUseCase,
)
from saas_platform.modules.telecom_conversations.postgres import (
    extraction_result_hash,
)
from saas_platform.modules.telecom_conversations.profile import (
    IncrementalLeadProfileApplier,
)
from saas_platform.modules.telecom_extraction.context import DNC_PATTERNS
from saas_platform.modules.telecom_extraction.domain import (
    ExtractionOutcome,
    HandoffKind,
    SpecialControl,
    TelecomExtractionCommand,
    TelecomExtractionResult,
)

logger = logging.getLogger(__name__)


def _stable_key(kind: str, *values: object) -> str:
    canonical = ":".join(str(item) for item in values)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{kind}:{digest}"


def _durable_extraction_key(result: TelecomExtractionResult, processing_version: int) -> str:
    return hashlib.sha256(
        f"{result.idempotency_key}:processing-v{processing_version}".encode()
    ).hexdigest()


def _is_dnc_message(content: str) -> bool:
    return any(pattern.search(content) is not None for pattern in DNC_PATTERNS)


@dataclass(frozen=True, slots=True)
class MessageProcessingView:
    message_id: UUID
    conversation_id: UUID
    status: MessageProcessingStatus
    processing_id: UUID
    processing_version: int
    attempts: int
    extraction_execution_id: UUID | None
    evaluation_id: UUID | None
    last_error_code: str | None


class IngestConversationMessage:
    def __init__(
        self,
        uow_factory: ConversationUnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._ids = ids
        self._clock = clock

    def execute(self, command: NormalizedConversationMessage) -> IngestConversationResult:
        if command.direction is not MessageDirection.INBOUND:
            raise ValidationError("this ingest use case accepts inbound messages only")
        now = self._clock.now()
        existing_result = self._existing(command, now=now)
        if existing_result is not None:
            return existing_result

        race = False
        with self._uow_factory.create(command.tenant_id) as uow:
            uow.tenants.require_active()
            resolved_contact = uow.contacts.resolve(
                command,
                contact_id=self._ids.new(),
                now=now,
            )
            resolved_conversation = uow.conversations.resolve_with_lead(
                resolved_contact.value,
                channel=command.channel,
                external_id=command.conversation_reference,
                conversation_id=self._ids.new(),
                lead_id=self._ids.new(),
                now=now,
            )
            inserted = uow.messages.insert(
                command,
                message_id=self._ids.new(),
                conversation_id=resolved_conversation.conversation.id,
                now=now,
            )
            if not inserted.created:
                uow.rollback()
                race = True
            else:
                uow.processing.create(
                    processing_id=self._ids.new(),
                    conversation_id=resolved_conversation.conversation.id,
                    message_id=inserted.value.id,
                    now=now,
                )
                conversation = uow.conversations.touch_with_message(
                    resolved_conversation.conversation,
                    inserted.value,
                    now=now,
                )
                lead = uow.leads.get(resolved_conversation.lead_id)
                self._audit_created_entities(
                    uow,
                    command=command,
                    contact=resolved_contact.value,
                    contact_created=resolved_contact.created,
                    conversation=conversation,
                    conversation_created=resolved_conversation.conversation_created,
                    lead=lead,
                    lead_created=resolved_conversation.lead_created,
                    message=inserted.value,
                    now=now,
                )
                self._outbox_message_ingested(uow, command, inserted.value, now=now)
                uow.commit()
                return IngestConversationResult(
                    contact=resolved_contact.value,
                    conversation=conversation,
                    lead=lead,
                    message=inserted.value,
                    contact_created=resolved_contact.created,
                    conversation_created=resolved_conversation.conversation_created,
                    lead_created=resolved_conversation.lead_created,
                    message_created=True,
                )
        if race:
            replay = self._existing(command, now=now)
            if replay is not None:
                return replay
        raise ConcurrencyConflictError("message ingest race could not be resolved")

    def _existing(
        self,
        command: NormalizedConversationMessage,
        *,
        now: object,
    ) -> IngestConversationResult | None:
        from datetime import datetime

        assert isinstance(now, datetime)
        with self._uow_factory.create(command.tenant_id) as uow:
            uow.tenants.require_active()
            existing = uow.messages.find_by_idempotency_key(command.idempotency_key())
            if existing is None:
                uow.rollback()
                return None
            conversation = uow.conversations.get(existing.conversation_id)
            contact = uow.contacts.get(conversation.contact_id)
            lead = uow.leads.get(conversation.lead_id)
            matches = existing.fingerprint == command.payload_hash()
            action = (
                "conversation_message.duplicate_detected"
                if matches
                else "conversation_message.idempotency_conflict"
            )
            self._append_audit(
                uow,
                action=action,
                category="message_ingest",
                target_type="conversation_message",
                target_id=existing.id,
                correlation_id=command.correlation_id,
                causation_id=existing.id,
                metadata={
                    "channel": command.channel.value,
                    "payload_matches": matches,
                },
                now=now,
                actor_type="internal_adapter",
            )
            uow.commit()
            if not matches:
                raise IdempotencyConflictError(
                    "message identifier was reused with a different payload",
                    details={"event_type": command.event_type},
                )
            return IngestConversationResult(
                contact=contact,
                conversation=conversation,
                lead=lead,
                message=existing,
                contact_created=False,
                conversation_created=False,
                lead_created=False,
                message_created=False,
            )

    def _audit_created_entities(
        self,
        uow: ConversationUnitOfWork,
        *,
        command: NormalizedConversationMessage,
        contact: Contact,
        contact_created: bool,
        conversation: Conversation,
        conversation_created: bool,
        lead: Lead,
        lead_created: bool,
        message: StoredMessage,
        now: object,
    ) -> None:
        from datetime import datetime

        assert isinstance(now, datetime)
        if contact_created:
            self._append_audit(
                uow,
                action="contact.created",
                category="contact",
                target_type="contact",
                target_id=contact.id,
                correlation_id=command.correlation_id,
                causation_id=message.id,
                metadata={"channel": contact.channel.value, "kind": contact.kind.value},
                now=now,
                actor_type="internal_adapter",
            )
        if lead_created:
            self._append_audit(
                uow,
                action="lead.created",
                category="lead",
                target_type="lead",
                target_id=lead.id,
                correlation_id=command.correlation_id,
                causation_id=message.id,
                metadata={"state": lead.state.value},
                now=now,
                actor_type="internal_adapter",
            )
        if conversation_created:
            self._append_audit(
                uow,
                action="conversation.created",
                category="conversation",
                target_type="conversation",
                target_id=conversation.id,
                correlation_id=command.correlation_id,
                causation_id=message.id,
                metadata={"channel": conversation.channel.value},
                now=now,
                actor_type="internal_adapter",
            )
        self._append_audit(
            uow,
            action="conversation_message.received",
            category="message_ingest",
            target_type="conversation_message",
            target_id=message.id,
            correlation_id=command.correlation_id,
            causation_id=message.id,
            metadata={
                "direction": message.direction.value,
                "content_type": message.content_type.value,
                "fingerprint": message.fingerprint,
            },
            now=now,
            actor_type="internal_adapter",
        )

    def _outbox_message_ingested(
        self,
        uow: ConversationUnitOfWork,
        command: NormalizedConversationMessage,
        message: StoredMessage,
        *,
        now: object,
    ) -> None:
        from datetime import datetime

        assert isinstance(now, datetime)
        event_id = self._ids.new()
        created = uow.outbox.add(
            OutboxEventData(
                event_key=_stable_key("conversation-message-ingested.v1", message.id),
                event_type="ConversationMessageIngested",
                aggregate_type="conversation",
                aggregate_id=message.conversation_id,
                payload={
                    "message_id": str(message.id),
                    "conversation_id": str(message.conversation_id),
                    "channel": message.channel.value,
                    "processing_status": message.processing_status.value,
                },
                correlation_id=command.correlation_id,
                causation_id=message.id,
            ),
            event_id=event_id,
            now=now,
        )
        if created:
            self._append_audit(
                uow,
                action="conversation_outbox.created",
                category="outbox",
                target_type="outbox_event",
                target_id=event_id,
                correlation_id=command.correlation_id,
                causation_id=message.id,
                metadata={"event_type": "ConversationMessageIngested"},
                now=now,
                actor_type="internal_adapter",
            )

    def _append_audit(
        self,
        uow: ConversationUnitOfWork,
        *,
        action: str,
        category: str,
        target_type: str,
        target_id: UUID,
        correlation_id: UUID,
        causation_id: UUID,
        metadata: dict[str, object],
        now: object,
        actor_type: str,
    ) -> None:
        from datetime import datetime

        assert isinstance(now, datetime)
        uow.audit.append(
            AuditEventData(
                action=action,
                category=category,
                target_type=target_type,
                target_id=target_id,
                actor_type=actor_type,
                actor_id=None,
                correlation_id=correlation_id,
                causation_id=causation_id,
                metadata=metadata,
            ),
            event_id=self._ids.new(),
            now=now,
        )


class ProcessConversationMessage:
    def __init__(
        self,
        uow_factory: ConversationUnitOfWorkFactory,
        extractor: TelecomExtractionUseCase,
        evaluator: QualificationEvaluator,
        ids: IdGenerator,
        clock: Clock,
        *,
        max_attempts: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._extractor = extractor
        self._evaluator = evaluator
        self._ids = ids
        self._clock = clock
        self._max_attempts = max_attempts
        self._profile_applier = IncrementalLeadProfileApplier()
        self._conversation_states = ConversationStateMachine()
        self._lead_states = LeadStateMachine()

    def execute(self, claim: ProcessingClaim) -> ProcessConversationResult:
        started = perf_counter()
        try:
            prepared = self._prepare(claim)
            if isinstance(prepared, ProcessConversationResult):
                return prepared
            extraction = self._extractor.execute(
                TelecomExtractionCommand(
                    profile=prepared.profile,
                    messages=prepared.messages,
                    correlation_id=claim.correlation_id,
                    previous_summary=prepared.conversation.commercial_summary,
                    recently_asked_fields=prepared.recently_asked_fields,
                    eligibility=EligibilityStatus(prepared.eligibility_value),
                    handoff_confirmed=prepared.active_handoff is not None,
                )
            )
            self._validate_extraction(claim, extraction)
            result = self._finalize(claim, prepared, extraction)
            logger.info(
                "conversation message processed",
                extra={
                    "operation": "conversation_message.process",
                    "message_id": str(claim.message_id),
                    "message_processing_id": str(claim.processing_id),
                    "outcome": result.outcome.value,
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "result": result.outcome.value,
                },
            )
            return result
        except ConcurrencyConflictError as error:
            self._try_record_failure(claim, error, retryable=True)
            return ProcessConversationResult(
                tenant_id=claim.tenant_id,
                processing_id=claim.processing_id,
                message_id=claim.message_id,
                outcome=ProcessingOutcome.CONCURRENCY_CONFLICT,
            )
        except TransientError as error:
            return self._record_failure(claim, error, retryable=True)
        except (PermanentError, ValidationError) as error:
            return self._record_failure(
                claim,
                error,
                retryable=False,
                invalid=isinstance(error, InvalidExtractionError),
            )

    def _prepare(
        self,
        claim: ProcessingClaim,
    ) -> ProcessingContext | ProcessConversationResult:
        now = self._clock.now()
        with self._uow_factory.create(claim.tenant_id, worker=True) as uow:
            uow.tenants.require_active()
            processing = uow.processing.get_for_claim(claim, lock=True)
            if (
                processing.status is not MessageProcessingStatus.PROCESSING
                or processing.lease_token != claim.lease_token
            ):
                raise ConcurrencyConflictError("processing claim is stale")
            conversation = uow.conversations.get(claim.conversation_id, lock=True)
            message = uow.messages.get(claim.message_id, lock=True)
            contact = uow.contacts.get(conversation.contact_id, lock=True)
            lead = uow.leads.get(conversation.lead_id, lock=True)
            active_handoff = uow.handoffs.active(conversation.id, lock=True)
            current_is_dnc = _is_dnc_message(message.content)

            if contact.do_not_contact:
                return self._complete_blocked(
                    uow,
                    claim,
                    MessageProcessingStatus.BLOCKED_DO_NOT_CONTACT,
                    ProcessingOutcome.BLOCKED_DO_NOT_CONTACT,
                    reason="contact_do_not_contact_active",
                    now=now,
                )
            if active_handoff is not None and not current_is_dnc:
                return self._complete_blocked(
                    uow,
                    claim,
                    MessageProcessingStatus.BLOCKED_HANDOFF,
                    ProcessingOutcome.BLOCKED_HANDOFF,
                    reason="active_handoff_pauses_automation",
                    now=now,
                )
            if conversation.state is ConversationState.CLOSED:
                raise ValidationError("closed conversation cannot be processed")

            context = ProcessingContext(
                contact=contact,
                conversation=conversation,
                lead_id=lead.id,
                lead_version=lead.version,
                lead_state=lead.state,
                profile=uow.profiles.load(
                    lead_id=lead.id,
                    conversation_id=conversation.id,
                ),
                message=message,
                processing=processing,
                messages=uow.messages.list_context(conversation.id, limit=50),
                recently_asked_fields=uow.questions.recently_asked(
                    conversation.id,
                    limit=5,
                ),
                active_handoff=active_handoff,
                eligibility_value=lead.eligibility.value,
            )
            uow.commit()
            return context

    def _complete_blocked(
        self,
        uow: ConversationUnitOfWork,
        claim: ProcessingClaim,
        status: MessageProcessingStatus,
        outcome: ProcessingOutcome,
        *,
        reason: str,
        now: object,
    ) -> ProcessConversationResult:
        from datetime import datetime

        assert isinstance(now, datetime)
        uow.processing.complete(
            claim,
            status=status,
            extraction_execution_id=None,
            evaluation_id=None,
            now=now,
        )
        self._audit(
            uow,
            action=f"conversation_message.{outcome.value}",
            category="conversation_processing",
            target_type="message_processing",
            target_id=claim.processing_id,
            claim=claim,
            metadata={"reason": reason},
            now=now,
        )
        uow.commit()
        return ProcessConversationResult(
            tenant_id=claim.tenant_id,
            processing_id=claim.processing_id,
            message_id=claim.message_id,
            outcome=outcome,
        )

    @staticmethod
    def _validate_extraction(
        claim: ProcessingClaim,
        extraction: TelecomExtractionResult,
    ) -> None:
        if extraction.tenant_id != claim.tenant_id:
            raise InvalidExtractionError("extraction tenant does not match processing tenant")
        if extraction.conversation_id != claim.conversation_id:
            raise InvalidExtractionError("extraction conversation does not match claim")
        if claim.message_id not in extraction.message_ids:
            raise InvalidExtractionError("extraction does not include the claimed message")

    def _finalize(
        self,
        claim: ProcessingClaim,
        prepared: ProcessingContext,
        extraction: TelecomExtractionResult,
    ) -> ProcessConversationResult:
        now = self._clock.now()
        durable_key = _durable_extraction_key(extraction, claim.processing_version)
        result_hash = extraction_result_hash(extraction)
        input_hash = hashlib.sha256(
            ":".join(str(item) for item in extraction.message_ids).encode("utf-8")
        ).hexdigest()
        with self._uow_factory.create(claim.tenant_id, worker=True) as uow:
            processing = uow.processing.get_for_claim(claim, lock=True)
            if (
                processing.status is not MessageProcessingStatus.PROCESSING
                or processing.lease_token != claim.lease_token
            ):
                raise ConcurrencyConflictError("processing lease changed before commit")
            conversation = uow.conversations.get(claim.conversation_id, lock=True)
            message = uow.messages.get(claim.message_id, lock=True)
            contact = uow.contacts.get(conversation.contact_id, lock=True)
            lead = uow.leads.get(conversation.lead_id, lock=True)
            if lead.version != prepared.lead_version:
                raise ConcurrencyConflictError("lead changed while extraction was running")

            dnc = any(
                item.control is SpecialControl.DO_NOT_CONTACT
                for item in extraction.accepted.controls
            )
            active_handoff = uow.handoffs.active(conversation.id, lock=True)
            if contact.do_not_contact and not dnc:
                return self._complete_blocked(
                    uow,
                    claim,
                    MessageProcessingStatus.BLOCKED_DO_NOT_CONTACT,
                    ProcessingOutcome.BLOCKED_DO_NOT_CONTACT,
                    reason="do_not_contact_detected_before_commit",
                    now=now,
                )
            if active_handoff is not None and not dnc:
                return self._complete_blocked(
                    uow,
                    claim,
                    MessageProcessingStatus.BLOCKED_HANDOFF,
                    ProcessingOutcome.BLOCKED_HANDOFF,
                    reason="handoff_activated_before_commit",
                    now=now,
                )

            existing_execution = uow.extractions.find_by_key(durable_key, lock=True)
            if existing_execution is not None:
                if existing_execution.result_hash != result_hash:
                    raise IdempotencyConflictError("durable extraction key has a different payload")
                evaluation_id = uow.evaluations.find_id_by_extraction(existing_execution.id)
                uow.processing.complete(
                    claim,
                    status=MessageProcessingStatus.COMPLETED,
                    extraction_execution_id=existing_execution.id,
                    evaluation_id=evaluation_id,
                    now=now,
                )
                self._audit(
                    uow,
                    action="telecom_extraction.duplicate_application_prevented",
                    category="idempotency",
                    target_type="extraction_execution",
                    target_id=existing_execution.id,
                    claim=claim,
                    metadata={"processing_version": claim.processing_version},
                    now=now,
                )
                uow.commit()
                return ProcessConversationResult(
                    tenant_id=claim.tenant_id,
                    processing_id=claim.processing_id,
                    message_id=claim.message_id,
                    outcome=ProcessingOutcome.DUPLICATE,
                    extraction_execution_id=existing_execution.id,
                    evaluation_id=evaluation_id,
                )

            stored_execution = uow.extractions.add(
                extraction,
                durable_idempotency_key=durable_key,
                input_hash=input_hash,
                result_hash=result_hash,
                lead_id=lead.id,
                message_id=message.id,
                processing_id=claim.processing_id,
                processing_version=claim.processing_version,
                correlation_id=claim.correlation_id,
                now=now,
            )
            current_profile = uow.profiles.load(
                lead_id=lead.id,
                conversation_id=conversation.id,
            )
            plan = self._profile_applier.plan(
                current_profile,
                extraction.accepted,
                message_content=message.content,
                applied_at=now,
            )
            persisted_profile = uow.profiles.apply(
                lead_id=lead.id,
                execution_id=stored_execution.id,
                plan=plan,
                now=now,
            )
            uow.profiles.persist_signals(
                lead_id=lead.id,
                execution_id=stored_execution.id,
                signals=extraction.update.qualification_request.signals,
                now=now,
            )
            updated_profile = uow.profiles.load(
                lead_id=lead.id,
                conversation_id=conversation.id,
            )
            signals = uow.profiles.load_active_signals(lead.id)
            qualification_request = replace(
                extraction.update.qualification_request,
                profile=updated_profile,
                signals=signals,
                trigger_id=message.id,
                correlation_id=claim.correlation_id,
                eligibility=lead.eligibility,
            )
            evaluation = self._evaluator.execute(qualification_request)
            uow.evaluations.add(
                evaluation,
                extraction_execution_id=stored_execution.id,
                message_id=message.id,
                processing_version=claim.processing_version,
                causation_id=message.id,
            )

            target_lead_state = self._lead_target(lead, evaluation, dnc=dnc)
            handoff = self._create_handoff_if_needed(
                uow,
                claim=claim,
                conversation=conversation,
                lead=lead,
                extraction=extraction,
                evaluation=evaluation,
                extraction_execution_id=stored_execution.id,
                dnc=dnc,
                now=now,
            )
            question_id = self._update_question(
                uow,
                conversation=conversation,
                lead=lead,
                extraction=extraction,
                extraction_execution_id=stored_execution.id,
                profile_changed=bool(persisted_profile.changed_fields),
                handoff_created=handoff is not None,
                dnc=dnc,
                now=now,
            )

            if dnc:
                uow.preferences.register_do_not_contact(
                    preference_id=self._ids.new(),
                    contact_id=contact.id,
                    conversation_id=conversation.id,
                    lead_id=lead.id,
                    source_message_id=message.id,
                    extraction_execution_id=stored_execution.id,
                    evidence_hash=hashlib.sha256(message.content.encode("utf-8")).hexdigest(),
                    correlation_id=claim.correlation_id,
                    now=now,
                )
                contact = uow.contacts.register_do_not_contact(contact, now=now)
                uow.handoffs.cancel_active(
                    conversation.id,
                    reason="do_not_contact",
                    now=now,
                )
                uow.questions.cancel_pending(
                    conversation.id,
                    reason="do_not_contact",
                    now=now,
                )

            target_conversation_state, automation_active = self._conversation_target(
                conversation,
                dnc=dnc,
                handoff=handoff is not None,
                question=question_id is not None,
            )
            if conversation.state is not target_conversation_state:
                self._conversation_states.transition(
                    conversation.state,
                    target_conversation_state,
                    actor=TransitionActor.WORKER,
                    cause=(
                        "explicit_do_not_contact"
                        if dnc
                        else "handoff_requested"
                        if handoff is not None
                        else "qualification_processed"
                    ),
                )
            updated_conversation = uow.conversations.update_after_processing(
                conversation,
                state=target_conversation_state,
                automation_active=automation_active,
                commercial_summary=extraction.accepted.summary[:2000],
                now=now,
            )
            if lead.state is not target_lead_state:
                self._lead_states.transition(
                    lead.state,
                    target_lead_state,
                    actor=TransitionActor.WORKER,
                    cause="deterministic_qualification",
                )
            updated_lead = uow.leads.update_after_evaluation(
                lead_id=lead.id,
                expected_version=lead.version,
                opportunity=plan.opportunity,
                state=target_lead_state,
                evaluation=evaluation,
                now=now,
            )
            self._audit_final_state(
                uow,
                claim=claim,
                extraction=extraction,
                execution_id=stored_execution.id,
                evaluation_id=evaluation.id,
                previous_lead=lead,
                updated_lead=updated_lead,
                previous_conversation=conversation,
                updated_conversation=updated_conversation,
                changed_fields=persisted_profile.changed_fields,
                conflict_count=persisted_profile.conflict_count,
                handoff_id=handoff,
                dnc=dnc,
                now=now,
            )
            self._outbox_final_state(
                uow,
                claim=claim,
                extraction=extraction,
                execution_id=stored_execution.id,
                evaluation_id=evaluation.id,
                previous_lead=lead,
                updated_lead=updated_lead,
                changed_fields=persisted_profile.changed_fields,
                conflict_count=persisted_profile.conflict_count,
                handoff_id=handoff,
                dnc=dnc,
                automation_paused=not automation_active,
                now=now,
            )
            processing_status = (
                MessageProcessingStatus.COMPLETED
                if extraction.outcome is ExtractionOutcome.COMPLETED
                else MessageProcessingStatus.COMPLETED_DEGRADED
            )
            uow.processing.complete(
                claim,
                status=processing_status,
                extraction_execution_id=stored_execution.id,
                evaluation_id=evaluation.id,
                now=now,
            )
            uow.commit()
            return ProcessConversationResult(
                tenant_id=claim.tenant_id,
                processing_id=claim.processing_id,
                message_id=claim.message_id,
                outcome=(
                    ProcessingOutcome.COMPLETED
                    if extraction.outcome is ExtractionOutcome.COMPLETED
                    else ProcessingOutcome.COMPLETED_DEGRADED
                ),
                extraction_execution_id=stored_execution.id,
                evaluation_id=evaluation.id,
                handoff_id=handoff,
                question_id=question_id,
                profile_changed=bool(persisted_profile.changed_fields),
            )

    def _lead_target(
        self,
        lead: Lead,
        evaluation: object,
        *,
        dnc: bool,
    ) -> LeadState:
        from saas_platform.modules.lead_qualification.domain import QualificationResult

        assert isinstance(evaluation, QualificationResult)
        if dnc:
            return LeadState.DO_NOT_CONTACT
        if evaluation.explanation.missing_information:
            return LeadState.INCOMPLETE_INFORMATION
        if evaluation.classification in {
            LeadClassification.WARM,
            LeadClassification.HOT,
            LeadClassification.PRIORITY,
        }:
            return LeadState.QUALIFIED
        if lead.state is LeadState.NEW:
            return LeadState.QUALIFYING
        return LeadState.QUALIFYING

    def _create_handoff_if_needed(
        self,
        uow: ConversationUnitOfWork,
        *,
        claim: ProcessingClaim,
        conversation: Conversation,
        lead: Lead,
        extraction: TelecomExtractionResult,
        evaluation: object,
        extraction_execution_id: UUID,
        dnc: bool,
        now: object,
    ) -> UUID | None:
        from datetime import datetime

        from saas_platform.modules.lead_qualification.domain import QualificationResult

        assert isinstance(now, datetime)
        assert isinstance(evaluation, QualificationResult)
        if dnc:
            return None
        required = extraction.accepted.handoff.required or evaluation.handoff.required
        if not required:
            return None
        specialized = (
            extraction.accepted.handoff.kind is HandoffKind.SPECIALIZED
            or evaluation.handoff.specialized
        )
        human_requested = any(
            item.control is SpecialControl.HUMAN_REQUESTED for item in extraction.accepted.controls
        )
        kind = HandoffType.SPECIALIZED if specialized else HandoffType.GENERAL
        priority = 950 if human_requested else 900 if specialized else 850
        reasons = tuple(
            dict.fromkeys(
                extraction.accepted.handoff.reason_codes + evaluation.handoff.reason_codes
            )
        )
        created = uow.handoffs.create(
            handoff_id=self._ids.new(),
            conversation_id=conversation.id,
            lead_id=lead.id,
            kind=kind,
            priority=priority,
            reason=",".join(reasons) or "deterministic_handoff",
            score=evaluation.score,
            classification=evaluation.classification.value,
            summary=extraction.accepted.summary,
            missing_information=evaluation.explanation.missing_information,
            source="customer" if human_requested else "extraction",
            extraction_execution_id=extraction_execution_id,
            now=now,
        )
        if not created.created:
            self._audit(
                uow,
                action="handoff.duplicate_prevented",
                category="handoff",
                target_type="handoff_request",
                target_id=created.value.id,
                claim=claim,
                metadata={"kind": created.value.kind.value},
                now=now,
            )
            return created.value.id
        return created.value.id

    def _update_question(
        self,
        uow: ConversationUnitOfWork,
        *,
        conversation: Conversation,
        lead: Lead,
        extraction: TelecomExtractionResult,
        extraction_execution_id: UUID,
        profile_changed: bool,
        handoff_created: bool,
        dnc: bool,
        now: object,
    ) -> UUID | None:
        from datetime import datetime

        assert isinstance(now, datetime)
        if profile_changed:
            uow.questions.cancel_pending(
                conversation.id,
                reason="profile_updated",
                now=now,
            )
        if dnc or handoff_created:
            uow.questions.cancel_pending(
                conversation.id,
                reason="do_not_contact" if dnc else "handoff_paused",
                now=now,
            )
            return None
        question = extraction.accepted.next_question
        if question is None:
            return None
        created = uow.questions.create(
            question_id=self._ids.new(),
            conversation_id=conversation.id,
            lead_id=lead.id,
            target_field=question.target_field,
            question=question.text,
            reason=question.reason,
            priority=question.priority,
            extraction_execution_id=extraction_execution_id,
            now=now,
        )
        return created.id

    @staticmethod
    def _conversation_target(
        conversation: Conversation,
        *,
        dnc: bool,
        handoff: bool,
        question: bool,
    ) -> tuple[ConversationState, bool]:
        del conversation
        if dnc:
            return ConversationState.DO_NOT_CONTACT, False
        if handoff:
            return ConversationState.WAITING_HUMAN, False
        if question:
            return ConversationState.WAITING_CUSTOMER, True
        return ConversationState.QUALIFYING, True

    def _audit_final_state(
        self,
        uow: ConversationUnitOfWork,
        *,
        claim: ProcessingClaim,
        extraction: TelecomExtractionResult,
        execution_id: UUID,
        evaluation_id: UUID,
        previous_lead: Lead,
        updated_lead: Lead,
        previous_conversation: Conversation,
        updated_conversation: Conversation,
        changed_fields: tuple[object, ...],
        conflict_count: int,
        handoff_id: UUID | None,
        dnc: bool,
        now: object,
    ) -> None:
        from datetime import datetime

        assert isinstance(now, datetime)
        self._audit(
            uow,
            action=(
                "telecom_extraction.completed"
                if extraction.outcome is ExtractionOutcome.COMPLETED
                else "telecom_extraction.completed_degraded"
            ),
            category="extraction",
            target_type="extraction_execution",
            target_id=execution_id,
            claim=claim,
            metadata={
                "outcome": extraction.outcome.value,
                "prompt_version": extraction.prompt_version,
                "schema_version": extraction.schema_version,
                "accepted_field_count": len(extraction.accepted.fields),
            },
            now=now,
        )
        if changed_fields:
            self._audit(
                uow,
                action="lead_profile.updated",
                category="lead_profile",
                target_type="lead",
                target_id=updated_lead.id,
                claim=claim,
                metadata={
                    "fields": [getattr(item, "value", str(item)) for item in changed_fields],
                    "field_count": len(changed_fields),
                },
                now=now,
            )
        if conflict_count:
            self._audit(
                uow,
                action="lead_profile.contradiction_detected",
                category="lead_profile",
                target_type="lead",
                target_id=updated_lead.id,
                claim=claim,
                metadata={"conflict_count": conflict_count},
                now=now,
            )
        self._audit(
            uow,
            action="qualification.score_calculated",
            category="qualification",
            target_type="qualification_evaluation",
            target_id=evaluation_id,
            claim=claim,
            metadata={
                "score": updated_lead.current_score,
                "classification": (
                    updated_lead.current_classification.value
                    if updated_lead.current_classification
                    else None
                ),
                "policy_version": updated_lead.current_policy_version,
                "policy_fingerprint": updated_lead.current_policy_fingerprint,
            },
            now=now,
        )
        if previous_lead.current_classification != updated_lead.current_classification:
            self._audit(
                uow,
                action="lead.classification_changed",
                category="lead",
                target_type="lead",
                target_id=updated_lead.id,
                claim=claim,
                metadata={
                    "from": (
                        previous_lead.current_classification.value
                        if previous_lead.current_classification
                        else None
                    ),
                    "to": (
                        updated_lead.current_classification.value
                        if updated_lead.current_classification
                        else None
                    ),
                },
                now=now,
            )
        if previous_lead.state is not updated_lead.state:
            self._audit(
                uow,
                action="lead.state_changed",
                category="lead",
                target_type="lead",
                target_id=updated_lead.id,
                claim=claim,
                metadata={
                    "from": previous_lead.state.value,
                    "to": updated_lead.state.value,
                },
                now=now,
            )
        if previous_conversation.state is not updated_conversation.state:
            self._audit(
                uow,
                action="conversation.state_changed",
                category="conversation",
                target_type="conversation",
                target_id=updated_conversation.id,
                claim=claim,
                metadata={
                    "from": previous_conversation.state.value,
                    "to": updated_conversation.state.value,
                },
                now=now,
            )
        if dnc:
            self._audit(
                uow,
                action="contact.do_not_contact_registered",
                category="contact_preference",
                target_type="contact",
                target_id=updated_lead.contact_id,
                claim=claim,
                metadata={"source": "explicit_customer_request"},
                now=now,
            )
        if handoff_id is not None:
            self._audit(
                uow,
                action="handoff.requested",
                category="handoff",
                target_type="handoff_request",
                target_id=handoff_id,
                claim=claim,
                metadata={"automation_paused": True},
                now=now,
            )
        if not updated_conversation.automation_active and (
            previous_conversation.automation_active or dnc
        ):
            self._audit(
                uow,
                action="conversation.automation_paused",
                category="conversation",
                target_type="conversation",
                target_id=updated_conversation.id,
                claim=claim,
                metadata={"reason": "do_not_contact" if dnc else "handoff"},
                now=now,
            )

    def _outbox_final_state(
        self,
        uow: ConversationUnitOfWork,
        *,
        claim: ProcessingClaim,
        extraction: TelecomExtractionResult,
        execution_id: UUID,
        evaluation_id: UUID,
        previous_lead: Lead,
        updated_lead: Lead,
        changed_fields: tuple[object, ...],
        conflict_count: int,
        handoff_id: UUID | None,
        dnc: bool,
        automation_paused: bool,
        now: object,
    ) -> None:
        from datetime import datetime

        assert isinstance(now, datetime)
        self._event(
            uow,
            claim=claim,
            event_type="TelecomExtractionAccepted",
            aggregate_type="extraction_execution",
            aggregate_id=execution_id,
            payload={
                "extraction_execution_id": str(execution_id),
                "message_id": str(claim.message_id),
                "outcome": extraction.outcome.value,
                "prompt_version": extraction.prompt_version,
                "schema_version": extraction.schema_version,
            },
            now=now,
        )
        if changed_fields or conflict_count:
            self._event(
                uow,
                claim=claim,
                event_type="LeadProfileUpdated",
                aggregate_type="lead",
                aggregate_id=updated_lead.id,
                payload={
                    "lead_id": str(updated_lead.id),
                    "extraction_execution_id": str(execution_id),
                    "changed_fields": [
                        getattr(item, "value", str(item)) for item in changed_fields
                    ],
                    "conflict_count": conflict_count,
                },
                now=now,
            )
        self._event(
            uow,
            claim=claim,
            event_type="QualificationEvaluationCreated",
            aggregate_type="lead",
            aggregate_id=updated_lead.id,
            payload={
                "lead_id": str(updated_lead.id),
                "evaluation_id": str(evaluation_id),
                "score": updated_lead.current_score,
                "classification": (
                    updated_lead.current_classification.value
                    if updated_lead.current_classification
                    else None
                ),
                "policy_version": updated_lead.current_policy_version,
                "policy_fingerprint": updated_lead.current_policy_fingerprint,
            },
            now=now,
        )
        if previous_lead.current_classification != updated_lead.current_classification:
            self._event(
                uow,
                claim=claim,
                event_type="LeadClassificationChanged",
                aggregate_type="lead",
                aggregate_id=updated_lead.id,
                payload={
                    "lead_id": str(updated_lead.id),
                    "previous": (
                        previous_lead.current_classification.value
                        if previous_lead.current_classification
                        else None
                    ),
                    "current": (
                        updated_lead.current_classification.value
                        if updated_lead.current_classification
                        else None
                    ),
                    "evaluation_id": str(evaluation_id),
                },
                now=now,
            )
        if dnc:
            self._event(
                uow,
                claim=claim,
                event_type="DoNotContactRegistered",
                aggregate_type="contact",
                aggregate_id=updated_lead.contact_id,
                payload={
                    "contact_id": str(updated_lead.contact_id),
                    "conversation_id": str(claim.conversation_id),
                    "source_message_id": str(claim.message_id),
                },
                now=now,
            )
        if handoff_id is not None:
            self._event(
                uow,
                claim=claim,
                event_type="HandoffRequested",
                aggregate_type="conversation",
                aggregate_id=claim.conversation_id,
                payload={
                    "handoff_id": str(handoff_id),
                    "conversation_id": str(claim.conversation_id),
                    "lead_id": str(updated_lead.id),
                },
                now=now,
            )
        if automation_paused:
            self._event(
                uow,
                claim=claim,
                event_type="ConversationAutomationPaused",
                aggregate_type="conversation",
                aggregate_id=claim.conversation_id,
                payload={
                    "conversation_id": str(claim.conversation_id),
                    "reason": "do_not_contact" if dnc else "handoff",
                },
                now=now,
            )

    def _event(
        self,
        uow: ConversationUnitOfWork,
        *,
        claim: ProcessingClaim,
        event_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        payload: dict[str, object],
        now: object,
    ) -> None:
        from datetime import datetime

        assert isinstance(now, datetime)
        event_id = self._ids.new()
        created = uow.outbox.add(
            OutboxEventData(
                event_key=_stable_key(
                    event_type,
                    aggregate_id,
                    claim.message_id,
                    claim.processing_version,
                ),
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                correlation_id=claim.correlation_id,
                causation_id=claim.message_id,
            ),
            event_id=event_id,
            now=now,
        )
        if created:
            self._audit(
                uow,
                action="conversation_outbox.created",
                category="outbox",
                target_type="outbox_event",
                target_id=event_id,
                claim=claim,
                metadata={"event_type": event_type},
                now=now,
            )

    def _audit(
        self,
        uow: ConversationUnitOfWork,
        *,
        action: str,
        category: str,
        target_type: str,
        target_id: UUID,
        claim: ProcessingClaim,
        metadata: dict[str, object],
        now: object,
    ) -> None:
        from datetime import datetime

        assert isinstance(now, datetime)
        uow.audit.append(
            AuditEventData(
                action=action,
                category=category,
                target_type=target_type,
                target_id=target_id,
                actor_type="worker",
                actor_id="telecom-conversation-worker.v1",
                correlation_id=claim.correlation_id,
                causation_id=claim.message_id,
                metadata=metadata,
            ),
            event_id=self._ids.new(),
            now=now,
        )

    def _record_failure(
        self,
        claim: ProcessingClaim,
        error: Exception,
        *,
        retryable: bool,
        invalid: bool = False,
    ) -> ProcessConversationResult:
        now = self._clock.now()
        may_retry = retryable and claim.attempts < self._max_attempts
        if may_retry:
            status = MessageProcessingStatus.RETRYABLE_FAILURE
            outcome = ProcessingOutcome.RETRYABLE_FAILURE
        elif invalid:
            status = MessageProcessingStatus.INVALID_EXTRACTION
            outcome = ProcessingOutcome.INVALID_EXTRACTION
        else:
            status = MessageProcessingStatus.PERMANENT_FAILURE
            outcome = ProcessingOutcome.PERMANENT_FAILURE
        next_attempt = now + timedelta(seconds=min(300, 2**claim.attempts))
        with self._uow_factory.create(claim.tenant_id, worker=True) as uow:
            uow.processing.get_for_claim(claim, lock=True)
            uow.processing.fail(
                claim,
                status=status,
                error_code=getattr(error, "code", type(error).__name__),
                next_attempt_at=next_attempt,
                now=now,
            )
            self._audit(
                uow,
                action="telecom_extraction.failed",
                category="extraction",
                target_type="message_processing",
                target_id=claim.processing_id,
                claim=claim,
                metadata={
                    "error_type": type(error).__name__,
                    "retryable": may_retry,
                    "attempt": claim.attempts,
                },
                now=now,
            )
            uow.commit()
        logger.warning(
            "conversation message processing failed",
            extra={
                "operation": "conversation_message.process",
                "message_id": str(claim.message_id),
                "message_processing_id": str(claim.processing_id),
                "error_type": type(error).__name__,
                "outcome": outcome.value,
                "result": outcome.value,
            },
        )
        return ProcessConversationResult(
            tenant_id=claim.tenant_id,
            processing_id=claim.processing_id,
            message_id=claim.message_id,
            outcome=outcome,
        )

    def _try_record_failure(
        self,
        claim: ProcessingClaim,
        error: Exception,
        *,
        retryable: bool,
    ) -> None:
        try:
            self._record_failure(claim, error, retryable=retryable)
        except Exception:
            logger.warning(
                "stale processing conflict could not update its former lease",
                extra={
                    "operation": "conversation_message.process",
                    "message_id": str(claim.message_id),
                    "message_processing_id": str(claim.processing_id),
                    "error_type": type(error).__name__,
                    "outcome": ProcessingOutcome.CONCURRENCY_CONFLICT.value,
                    "result": "stale_lease",
                },
            )


class GetMessageProcessing:
    def __init__(self, uow_factory: ConversationUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, tenant_id: object, message_id: UUID) -> MessageProcessingView:
        from saas_platform.modules.tenancy.domain import TenantId

        if not isinstance(tenant_id, TenantId):
            raise TypeError("tenant_id must be TenantId")
        with self._uow_factory.create(tenant_id) as uow:
            uow.tenants.require_active()
            message = uow.messages.get(message_id)
            processing = uow.processing.get_by_message(message.id)
            uow.commit()
        return MessageProcessingView(
            message_id=message.id,
            conversation_id=message.conversation_id,
            status=processing.status,
            processing_id=processing.id,
            processing_version=processing.processing_version,
            attempts=processing.attempts,
            extraction_execution_id=processing.extraction_execution_id,
            evaluation_id=processing.evaluation_id,
            last_error_code=processing.last_error_code,
        )
