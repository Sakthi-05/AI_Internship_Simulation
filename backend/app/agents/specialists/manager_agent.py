"""Manager Agent response proposals using the established manager voice/provider."""
from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from collections import OrderedDict
from enum import Enum
from types import SimpleNamespace
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents.base_agent import BaseAgent
from app.agents.context_manager import AgentContextManager
from app.agents.contracts import AgentContext, EvidenceReference
from app.agents.registry import AgentRegistry
from app.models.enums import ManagerPersonality, MessageSenderType
from app.services.ai.ai_manager import generate_manager_reply
from app.services.ai.ai_service import AIService, get_ai_service

logger = logging.getLogger(__name__)
_REQUEST_RUNTIME: ContextVar[tuple[Session, Optional[AIService]] | None] = ContextVar(
    "manager_agent_runtime", default=None,
)


@contextmanager
def bind_manager_runtime(db: Session, ai_service: Optional[AIService] = None):
    """Bind request-owned DB/provider dependencies to the shared manager agent."""
    token = _REQUEST_RUNTIME.set((db, ai_service))
    try:
        yield
    finally:
        _REQUEST_RUNTIME.reset(token)


class ManagerCommunicationCategory(str, Enum):
    CONTEXTUAL_MESSAGE = "contextual_message"
    TASK_CLARIFICATION = "task_clarification"
    WORKPLACE_FEEDBACK = "workplace_feedback"
    REQUIREMENT_CHANGE = "requirement_change"
    PROGRESS_CHECKIN = "progress_checkin"
    FOLLOW_UP = "follow_up"
    ANSWER_QUESTION = "answer_question"


class ManagerAgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_id: UUID
    student_id: str = Field(min_length=1, max_length=100)
    task_id: Optional[UUID] = None
    student_message_id: Optional[UUID] = None
    workplace_event_id: Optional[UUID] = None
    trigger_evidence_id: Optional[UUID] = None
    category: Optional[ManagerCommunicationCategory] = None


class ManagerResponseMetadata(BaseModel):
    generation_source: str
    idempotency_key: str
    history_messages_used: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)


class ManagerAgentOutput(BaseModel):
    decision: str
    reason_code: str
    manager_message: Optional[str] = None
    communication_category: Optional[ManagerCommunicationCategory] = None
    simulation_id: UUID
    task_id: Optional[UUID] = None
    student_message_id: Optional[UUID] = None
    workplace_event_id: Optional[UUID] = None
    criteria_version_id: Optional[UUID] = None
    supporting_evidence: list[EvidenceReference] = Field(default_factory=list)
    response_metadata: Optional[ManagerResponseMetadata] = None


class ManagerAgent(BaseAgent):
    agent_id = "manager"
    description = "Creates contextual, in-character manager response proposals."
    responsibility = "Respond to a persisted student message, workplace event, or interaction."
    input_model = ManagerAgentInput
    output_model = ManagerAgentOutput

    def __init__(self, db: Optional[Session] = None, *, context_manager: Optional[AgentContextManager] = None,
                 ai_service: Optional[AIService] = None):
        self.db = db
        self.context_manager = context_manager or AgentContextManager()
        self.ai_service = ai_service
        self._response_cache: OrderedDict[str, ManagerAgentOutput] = OrderedDict()
        self._response_cache_limit = 128

    async def execute(self, payload: ManagerAgentInput,
                      context: Optional[AgentContext] = None) -> ManagerAgentOutput:
        runtime = _REQUEST_RUNTIME.get()
        db = self.db or (runtime[0] if runtime else None)
        ai_service = (runtime[1] if runtime else None) or self.ai_service or get_ai_service()
        if db is None:
            return self._skip(payload, "database_context_unavailable", "Manager context is unavailable.")
        # This read also enforces simulation and company ownership.
        manager_context = self.context_manager.build(
            db, simulation_id=payload.simulation_id, student_id=payload.student_id,
            profile="manager",
        )
        task = manager_context.current_task
        if task is None or manager_context.current_task_id is None:
            return self._skip(payload, "task_unavailable", "There is no current task context.")
        if payload.task_id is not None and payload.task_id != manager_context.current_task_id:
            return self._skip(payload, "stale_task", "The requested task is no longer the current task.", task_id=task["id"])

        event = None
        evidence: list[EvidenceReference] = []
        if payload.workplace_event_id is not None:
            event = next((row for row in manager_context.workplace_events
                          if row.get("id") == payload.workplace_event_id
                          and row.get("task_id") in (None, task["id"])), None)
            if event is None:
                return self._skip(payload, "event_unavailable", "The event is not available for this task.", task_id=task["id"])
            status = str(event.get("status") or "")
            if status in {"resolved", "dismissed"}:
                return self._skip(payload, "event_closed", "The workplace event is already closed.",
                                  task_id=task["id"], event_id=payload.workplace_event_id)
            if (event.get("manager_response_message_id") or
                    (payload.student_message_id is None and event.get("manager_message_id"))):
                return self._skip(payload, "duplicate_request", "A manager response is already recorded for this event.",
                                  task_id=task["id"], event_id=payload.workplace_event_id)
            evidence.extend(ref for ref in manager_context.evidence
                            if ref.evidence_record_id == payload.workplace_event_id
                            and ref.evidence_type == "workflow_event")

        student_message = None
        if payload.student_message_id is not None:
            student_message = next((item for item in manager_context.manager_communications
                                    if item.get("id") == payload.student_message_id
                                    and item.get("sender_type") == "student"
                                    and item.get("sender_id") == payload.student_id), None)
            if student_message is None:
                return self._skip(payload, "message_unavailable", "The student message is not available in this simulation.",
                                  task_id=task["id"], event_id=payload.workplace_event_id)
            evidence.extend(ref for ref in manager_context.evidence
                            if ref.evidence_record_id == payload.student_message_id
                            and ref.evidence_type == "manager_communication")
            if self._message_already_answered(student_message, manager_context.manager_communications):
                return self._skip(payload, "duplicate_request", "A manager response is already recorded for this message.",
                                  task_id=task["id"], event_id=payload.workplace_event_id)

        interaction = None
        if payload.trigger_evidence_id is not None:
            interaction = next((item for item in manager_context.interaction_history
                                if item.get("interaction_id") == payload.trigger_evidence_id), None)
            if interaction is None or not any(ref.evidence_record_id == payload.trigger_evidence_id
                                              and ref.evidence_type == "simulation_interaction"
                                              and ref.simulation_id == payload.simulation_id
                                              and ref.task_id in (None, task["id"])
                                              for ref in manager_context.evidence):
                return self._skip(payload, "trigger_unavailable", "The progress trigger is not persisted for this task.",
                                  task_id=task["id"], event_id=payload.workplace_event_id)
            if self._interaction_already_answered(interaction, manager_context.manager_communications):
                return self._skip(payload, "duplicate_request", "A manager check-in is already recorded for this activity.",
                                  task_id=task["id"], event_id=payload.workplace_event_id)
            evidence.extend(ref for ref in manager_context.evidence
                            if ref.evidence_record_id == payload.trigger_evidence_id
                            and ref.evidence_type == "simulation_interaction")

        if not any((student_message, event, interaction)):
            return self._skip(payload, "trigger_required",
                              "A persisted student message, workplace event, or interaction is required.",
                              task_id=task["id"])

        category = self._category(payload.category, event, student_message, interaction,
                                  manager_context)
        if category == ManagerCommunicationCategory.REQUIREMENT_CHANGE and event is None:
            return self._skip(payload, "event_required", "Requirement-change communication must reference its persisted event.",
                              task_id=task["id"])
        if event and str(event.get("event_type")) == "requirement_change":
            category = ManagerCommunicationCategory.REQUIREMENT_CHANGE

        idempotency_source = (str(payload.student_message_id) if payload.student_message_id else
                              str(payload.workplace_event_id) if payload.workplace_event_id else
                              str(payload.trigger_evidence_id))
        idempotency_key = hashlib.sha256(
            f"{payload.simulation_id}:{idempotency_source}:{category.value}".encode()
        ).hexdigest()
        cached = self._response_cache.get(idempotency_key)
        if cached is not None:
            self._response_cache.move_to_end(idempotency_key)
            return cached

        manager_profile = manager_context.manager_profile
        project_info = manager_context.current_project
        if not manager_profile or not project_info:
            return self._skip(payload, "manager_context_missing", "Manager or project details are unavailable.",
                              task_id=task["id"], event_id=payload.workplace_event_id)

        history = self._history_objects(manager_context.manager_communications)
        student_text = str(student_message.get("content")) if student_message else ""
        workplace_context = _workplace_context(manager_context, task, event, category)
        manager = SimpleNamespace(
            id=manager_profile.get("id"), name=manager_profile.get("name"),
            title=manager_profile.get("title"),
            personality=SimpleNamespace(value=manager_profile.get("personality", "corporate")),
        )
        project = SimpleNamespace(title=project_info.get("title", "the project"))
        state = SimpleNamespace(**(manager_context.project_state or {})) if manager_context.project_state else None
        source = "ai_provider"
        confidence = 0.82
        try:
            response = await generate_manager_reply(
                ai_service, manager, project, state, history, student_text,
                workplace_context=workplace_context,
            )
            response = _clean_response(response)
            if not response:
                raise ValueError("empty manager response")
        except Exception as exc:
            logger.warning("Manager response generation failed category=%s error_type=%s",
                           category.value, type(exc).__name__)
            response = _fallback_message(category, event, task)
            source = "deterministic_fallback"
            confidence = 0.55

        event_criteria_id = _uuid_or_none(event.get("criteria_version_id")) if event else None
        criteria_version_id = event_criteria_id or manager_context.criteria_version_id
        evidence.extend(ref for ref in manager_context.evidence
                        if ref.evidence_type == "acceptance_criteria"
                        and ref.criteria_version_id == criteria_version_id)
        if category == ManagerCommunicationCategory.WORKPLACE_FEEDBACK:
            evidence.extend(ref for ref in manager_context.evidence
                            if ref.evidence_type == "technical_review")
        output = ManagerAgentOutput(
            decision="respond", reason_code="response_generated", manager_message=response,
            communication_category=category, simulation_id=payload.simulation_id,
            task_id=task["id"], student_message_id=payload.student_message_id,
            workplace_event_id=payload.workplace_event_id,
            criteria_version_id=criteria_version_id,
            supporting_evidence=_unique_evidence(evidence),
            response_metadata=ManagerResponseMetadata(
                generation_source=source, idempotency_key=idempotency_key,
                history_messages_used=min(len(history), 8), confidence=confidence,
            ),
        )
        self._response_cache[idempotency_key] = output
        self._response_cache.move_to_end(idempotency_key)
        while len(self._response_cache) > self._response_cache_limit:
            self._response_cache.popitem(last=False)
        return output

    @staticmethod
    def _history_objects(messages: list[dict[str, Any]]) -> list[SimpleNamespace]:
        ordered = sorted(messages, key=lambda item: (item.get("created_at") or "", str(item.get("id"))))
        return [SimpleNamespace(
            id=item.get("id"),
            sender_type=MessageSenderType(str(item.get("sender_type"))),
            sender_id=item.get("sender_id"), content=str(item.get("content", ""))[:2000],
            created_at=item.get("created_at"),
        ) for item in ordered if item.get("sender_type") in {"student", "manager"}]

    @staticmethod
    def _message_already_answered(student_message: dict[str, Any], messages: list[dict[str, Any]]) -> bool:
        ordered = sorted(messages, key=lambda item: (item.get("created_at") or "", str(item.get("id"))))
        try:
            index = next(i for i, item in enumerate(ordered) if item.get("id") == student_message.get("id"))
        except StopIteration:
            return False
        for item in ordered[index + 1:]:
            if item.get("sender_type") == "student":
                return False
            if item.get("sender_type") == "manager":
                return True
        return False

    @staticmethod
    def _interaction_already_answered(interaction: dict[str, Any], messages: list[dict[str, Any]]) -> bool:
        occurred_at = interaction.get("occurred_at")
        if occurred_at is None:
            return False
        for message in messages:
            if message.get("sender_type") == "manager" and message.get("created_at") and message["created_at"] >= occurred_at:
                return True
        return False

    @staticmethod
    def _category(requested, event, student_message, interaction,
                  context: AgentContext) -> ManagerCommunicationCategory:
        if event and str(event.get("event_type")) == "requirement_change":
            return ManagerCommunicationCategory.REQUIREMENT_CHANGE
        if requested is not None:
            return requested
        if event is not None:
            return ManagerCommunicationCategory.WORKPLACE_FEEDBACK
        if interaction is not None and student_message is None:
            return ManagerCommunicationCategory.PROGRESS_CHECKIN
        text = str(student_message.get("content") if student_message else "").lower()
        previous_student_messages = [item for item in context.manager_communications
                                     if item.get("sender_type") == "student"]
        if len(previous_student_messages) > 1:
            latest_student = max(previous_student_messages,
                                 key=lambda item: item.get("created_at") or "")
            if latest_student.get("id") == student_message.get("id") and any(
                    item.get("sender_type") == "manager" and
                    (item.get("created_at") or "") < (student_message.get("created_at") or "")
                    for item in context.manager_communications):
                return ManagerCommunicationCategory.FOLLOW_UP
        if "?" in text:
            return ManagerCommunicationCategory.ANSWER_QUESTION
        if any(token in text for token in ("clarify", "acceptance criteria", "requirement")):
            return ManagerCommunicationCategory.TASK_CLARIFICATION
        if context.technical_reviews and any(token in text for token in ("feedback", "review", "changes")):
            return ManagerCommunicationCategory.WORKPLACE_FEEDBACK
        return ManagerCommunicationCategory.CONTEXTUAL_MESSAGE

    @staticmethod
    def _skip(payload: ManagerAgentInput, code: str, reason: str, *,
              task_id: Optional[UUID] = None,
              event_id: Optional[UUID] = None) -> ManagerAgentOutput:
        return ManagerAgentOutput(decision="not_responded", reason_code=code,
                                  simulation_id=payload.simulation_id, task_id=task_id,
                                  workplace_event_id=event_id)


def register_manager_agent(registry: AgentRegistry, db: Optional[Session] = None, *,
                           context_manager: Optional[AgentContextManager] = None,
                           ai_service: Optional[AIService] = None) -> ManagerAgent:
    agent = ManagerAgent(db, context_manager=context_manager, ai_service=ai_service)
    registry.register(agent)
    return agent


def create_manager_orchestrator(db: Session, *,
                                context_manager: Optional[AgentContextManager] = None,
                                ai_service: Optional[AIService] = None):
    from app.agents.orchestrator import AgentOrchestrator

    registry = AgentRegistry()
    register_manager_agent(registry, db, context_manager=context_manager, ai_service=ai_service)
    return AgentOrchestrator(registry)


async def invoke_manager_agent(db: Session, ai_service: AIService, *,
                               orchestrator=None,
                               simulation_id: UUID, student_id: str,
                               task_id: Optional[UUID] = None,
                               student_message_id: Optional[UUID] = None,
                               workplace_event_id: Optional[UUID] = None,
                               trigger_evidence_id: Optional[UUID] = None) -> Optional[ManagerAgentOutput]:
    """Run one response through a fresh instance-owned registry and orchestrator."""
    from app.agents.orchestrator import AgentOrchestrator
    from app.agents.contracts import AgentRequest

    if orchestrator is None:
        registry = AgentRegistry()
        register_manager_agent(registry, db, ai_service=ai_service)
        orchestrator = AgentOrchestrator(registry)
    request = ManagerAgentInput(
        simulation_id=simulation_id, student_id=student_id, task_id=task_id,
        student_message_id=student_message_id, workplace_event_id=workplace_event_id,
        trigger_evidence_id=trigger_evidence_id,
    )
    with bind_manager_runtime(db, ai_service):
        response = await orchestrator.execute(
            AgentRequest(agent_id=ManagerAgent.agent_id, simulation_id=simulation_id,
                         payload=request.model_dump(mode="json"))
        )
    if response.status.value != "succeeded" or not response.result:
        return None
    output = ManagerAgentOutput.model_validate(response.result)
    return output if output.decision == "respond" and output.manager_message else None


def _workplace_context(context: AgentContext, task: dict[str, Any], event: Optional[dict[str, Any]],
                       category: ManagerCommunicationCategory) -> str:
    parts = [f"Communication category: {category.value}",
             f"Current task: {task.get('title')}. Status: {task.get('task_status') or task.get('status')}.",
             f"Task description: {str(task.get('description') or '')[:400]}"]
    if context.current_sprint:
        parts.append(f"Sprint: {context.current_sprint.get('name')} ({context.current_sprint.get('status')}).")
    if context.active_criteria:
        criteria = [item.get("text", "") for item in context.active_criteria.get("items", [])
                    if isinstance(item, dict)]
        parts.append("Active acceptance criteria: " + "; ".join(criteria[:8]))
    if event:
        parts.append(
            f"Workplace event {event.get('id')} ({event.get('event_type')}, {event.get('status')}): "
            f"{str(event.get('message') or '')[:500]}"
        )
        if event.get("updated_requirement"):
            parts.append(f"Updated requirement: {str(event['updated_requirement'])[:500]}")
        if event.get("criteria_version_number"):
            parts.append(f"Criteria version: {event['criteria_version_number']}")
    if context.technical_reviews:
        review = context.technical_reviews[0]
        parts.append(f"Latest review: {review.get('result')}; {str(review.get('summary') or '')[:500]}")
    if context.competencies:
        gaps = context.competencies.get("weaknesses") or []
        if gaps:
            parts.append("Relevant coaching dimensions: " + ", ".join(map(str, gaps[:5])))
    parts.append("Respond only to the known context. Do not invent performance or claim completion.")
    return "\n".join(parts)


def _fallback_message(category: ManagerCommunicationCategory,
                      event: Optional[dict[str, Any]], task: dict[str, Any]) -> str:
    if category == ManagerCommunicationCategory.REQUIREMENT_CHANGE and event:
        criterion = str(event.get("updated_requirement") or "the updated acceptance criteria")
        return (f"Please review the recorded requirement change for {task.get('title')}. "
                f"The updated criterion is: {criterion} If anything is unclear, ask before you adapt the work.")
    if category == ManagerCommunicationCategory.TASK_CLARIFICATION:
        return "I can clarify the task. Please point me to the specific criterion or step that is unclear, and I’ll address it directly."
    if category == ManagerCommunicationCategory.WORKPLACE_FEEDBACK:
        return "I’ve noted the recorded feedback. Review it against the task criteria, and let me know which point needs clarification before you revise."
    if category == ManagerCommunicationCategory.PROGRESS_CHECKIN:
        return f"How is the work on {task.get('title')} progressing? Share your current blocker or next step so I can help."
    if category == ManagerCommunicationCategory.FOLLOW_UP:
        return "Thanks for the follow-up. Which part of the next step should we clarify first?"
    if category == ManagerCommunicationCategory.ANSWER_QUESTION:
        return "Thanks for asking. I’ll check the task details and acceptance criteria, then give you a specific answer."
    return "Thanks for the update. I’ve noted it against the current task; tell me if you need help with a specific next step."


def _clean_response(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:2000]


def _uuid_or_none(value: Any) -> Optional[UUID]:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _unique_evidence(references: list[EvidenceReference]) -> list[EvidenceReference]:
    result = {}
    for reference in references:
        result[(reference.evidence_type, reference.evidence_record_id)] = reference
    return list(result.values())
