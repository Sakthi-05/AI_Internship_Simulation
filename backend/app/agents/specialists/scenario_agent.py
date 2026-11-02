"""Proposal-only Scenario Agent over existing workplace records and lifecycle."""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents.base_agent import BaseAgent
from app.agents.context_manager import AgentContextManager
from app.agents.contracts import AgentContext, EvidenceReference
from app.agents.registry import AgentRegistry
from app.services.ai.ai_service import AIService, MockAIService, get_ai_service
from app.services.workplace_scenarios import SCENARIO_CATALOG

logger = logging.getLogger(__name__)


class ScenarioCategory(str, Enum):
    REQUIREMENT_CHANGE = "requirement_change"
    COLLABORATION = "collaboration"
    TIME_MANAGEMENT = "time_management"
    PRIORITIZATION = "prioritization"
    OWNERSHIP = "ownership"
    FEEDBACK_RESPONSE = "feedback_response"


class ScenarioAgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_id: UUID
    student_id: str = Field(min_length=1, max_length=100)
    task_id: Optional[UUID] = None
    trigger_activity: str = Field(min_length=1, max_length=80)
    trigger_evidence_id: Optional[UUID] = None
    preferred_category: Optional[ScenarioCategory] = None


class ScenarioProposal(BaseModel):
    category: ScenarioCategory
    title: str = Field(min_length=3, max_length=160)
    situation: str = Field(min_length=20, max_length=2000)
    student_action: str = Field(min_length=10, max_length=1000)
    relevance: str = Field(min_length=10, max_length=1000)


class ScenarioAgentOutput(BaseModel):
    decision: str
    reason_code: str
    reason: str
    simulation_id: UUID
    task_id: Optional[UUID] = None
    criteria_version_id: Optional[UUID] = None
    scenario_category: Optional[ScenarioCategory] = None
    scenario_proposal: Optional[ScenarioProposal] = None
    competency_dimensions: list[str] = Field(default_factory=list)
    supporting_evidence: list[EvidenceReference] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    generation_source: str


_ACTIVITY_CATEGORY = {
    "recorded_work": ScenarioCategory.REQUIREMENT_CHANGE,
    "approach_note": ScenarioCategory.REQUIREMENT_CHANGE,
    "teammate_request": ScenarioCategory.COLLABORATION,
    "deadline_warning": ScenarioCategory.TIME_MANAGEMENT,
    "competing_work": ScenarioCategory.PRIORITIZATION,
    "unowned_issue": ScenarioCategory.OWNERSHIP,
    "review_feedback": ScenarioCategory.FEEDBACK_RESPONSE,
}

_CATEGORY_TRIGGER_EVIDENCE = {
    ScenarioCategory.REQUIREMENT_CHANGE: {"submission_version", "simulation_interaction"},
    ScenarioCategory.COLLABORATION: {"manager_communication"},
    ScenarioCategory.TIME_MANAGEMENT: {"workflow_event"},
    ScenarioCategory.PRIORITIZATION: {"workflow_event"},
    ScenarioCategory.OWNERSHIP: {"workflow_event"},
    ScenarioCategory.FEEDBACK_RESPONSE: {"technical_review"},
}
_CATEGORY_ACTIVITY = {
    ScenarioCategory.REQUIREMENT_CHANGE: {"recorded_work", "approach_note"},
    ScenarioCategory.COLLABORATION: {"teammate_request"},
    ScenarioCategory.TIME_MANAGEMENT: {"deadline_warning"},
    ScenarioCategory.PRIORITIZATION: {"competing_work"},
    ScenarioCategory.OWNERSHIP: {"unowned_issue"},
    ScenarioCategory.FEEDBACK_RESPONSE: {"review_feedback"},
}

_FALLBACK_PROPOSALS = {
    ScenarioCategory.REQUIREMENT_CHANGE: (
        "Acceptance criteria update", "A newly recorded task activity means the acceptance criteria need review. "
        "Compare the active criteria with the earlier version and identify what changed before updating your work.",
        "Explain how you will adapt the in-progress task to satisfy the new criterion.",
        "The proposal is tied to the active task and its persisted criteria history; it exercises adaptation and communication."),
    ScenarioCategory.COLLABORATION: (
        "Coordinate shared work", "A teammate has asked for coordination on work connected to the current task. "
        "Clarify the dependency, agree on an owner, and share a concise next step.",
        "Respond with a clear ownership and coordination plan.",
        "The recorded teammate request relates directly to the task and exercises collaboration."),
    ScenarioCategory.TIME_MANAGEMENT: (
        "Re-plan around a deadline", "A recorded deadline warning affects the current task. "
        "Review the remaining work and identify the highest-value next step.",
        "Describe a realistic sequence for the remaining work and any trade-off you would communicate.",
        "The deadline warning is persisted for this task and exercises time management."),
    ScenarioCategory.PRIORITIZATION: (
        "Resolve competing priorities", "A recorded priority conflict affects the active work. "
        "Compare urgency, impact, and dependencies before deciding what to handle first.",
        "State your priority choice and explain how you will communicate its impact.",
        "The persisted priority event creates a concrete decision point for prioritization."),
    ScenarioCategory.OWNERSHIP: (
        "Take ownership of an open issue", "A persisted issue has no confirmed owner and affects the current task. "
        "Clarify the impact, propose an owner, and state the next action.",
        "Explain whether you will take ownership or coordinate an owner, with a specific next step.",
        "The recorded issue is related to the task and exercises professional ownership."),
    ScenarioCategory.FEEDBACK_RESPONSE: (
        "Respond to review feedback", "A persisted review identifies a change needed in the latest submission. "
        "Compare the feedback with the submitted work and plan a focused improvement.",
        "Summarize the feedback you will address and the evidence you will provide in the revision.",
        "The proposal uses an actual review outcome and exercises feedback response and communication."),
}


class ScenarioAgent(BaseAgent):
    agent_id = "scenario"
    description = "Creates evidence-grounded workplace scenario proposals."
    responsibility = "Assess workflow eligibility and propose one relevant workplace situation."
    input_model = ScenarioAgentInput
    output_model = ScenarioAgentOutput

    def __init__(self, db: Session, *, context_manager: Optional[AgentContextManager] = None,
                 ai_service: Optional[AIService] = None):
        self.db = db
        self.context_manager = context_manager or AgentContextManager()
        self.ai_service = ai_service or get_ai_service()

    async def execute(self, payload: ScenarioAgentInput,
                      context: Optional[AgentContext] = None) -> ScenarioAgentOutput:
        # Always resolve persisted records under the requesting student's identity.
        simulation_context = self.context_manager.build(
            self.db, simulation_id=payload.simulation_id, student_id=payload.student_id,
            profile="scenario", task_id=payload.task_id,
        )
        task = simulation_context.current_task
        if task is None:
            return self._no_scenario(payload, None, "task_unavailable",
                                     "The requested task is not available in this simulation.")

        category = payload.preferred_category or _ACTIVITY_CATEGORY.get(payload.trigger_activity.strip().lower())
        if category is None:
            return self._no_scenario(payload, task, "unsupported_activity",
                                     "No scenario category is associated with this recorded activity.")
        evidence = next((reference for reference in simulation_context.evidence
                         if reference.evidence_record_id == payload.trigger_evidence_id
                         and reference.evidence_type in _CATEGORY_TRIGGER_EVIDENCE[category]
                         and reference.student_id == payload.student_id
                         and reference.simulation_id == payload.simulation_id
                         and (reference.task_id is None or reference.task_id == task["id"])
                         and self._evidence_matches(category, reference.evidence_record_id,
                                                    simulation_context)), None)
        if evidence is None:
            return self._no_scenario(payload, task, "trigger_evidence_missing",
                                     "A matching persisted trigger record is required before proposing a scenario.")

        eligibility = self._eligibility(category, payload, simulation_context)
        if eligibility is not None:
            return self._no_scenario(payload, task, eligibility[0], eligibility[1])

        duplicate = self._duplicate_or_conflict(category, simulation_context)
        if duplicate is not None:
            return self._no_scenario(payload, task, duplicate[0], duplicate[1])

        proposal_data, source, confidence = await self._generate(category, task, simulation_context)
        spec = next(item for item in SCENARIO_CATALOG if item.key == category.value)
        return ScenarioAgentOutput(
            decision="proposed", reason_code="eligible_trigger",
            reason=(f"{spec.summary} Trigger evidence {evidence.evidence_type} is recorded, "
                    "and the task is in a compatible workflow state."),
            simulation_id=payload.simulation_id, task_id=task["id"],
            criteria_version_id=simulation_context.criteria_version_id,
            scenario_category=category, scenario_proposal=proposal_data,
            competency_dimensions=list(spec.competencies),
            supporting_evidence=[evidence], confidence=confidence,
            generation_source=source,
        )

    def _eligibility(self, category: ScenarioCategory, payload: ScenarioAgentInput,
                     context: AgentContext) -> Optional[tuple[str, str]]:
        task = context.current_task or {}
        status = str(task.get("task_status") or task.get("status") or "")
        if payload.trigger_activity.strip().lower() not in _CATEGORY_ACTIVITY[category]:
            return "activity_not_eligible", "The recorded activity does not match the requested scenario category."
        if category == ScenarioCategory.REQUIREMENT_CHANGE:
            if status != "in_progress" or task.get("started_at") is None:
                return "task_not_started", "Requirement changes require a started, in-progress task."
            if payload.trigger_activity.strip().lower() not in {"recorded_work", "approach_note"}:
                return "activity_not_eligible", "Requirement changes follow recorded work, not a status-only activity."
            if not context.active_criteria:
                return "criteria_unavailable", "The active criteria snapshot is unavailable."
        elif category in {ScenarioCategory.COLLABORATION, ScenarioCategory.TIME_MANAGEMENT,
                          ScenarioCategory.PRIORITIZATION, ScenarioCategory.OWNERSHIP}:
            if status not in {"in_progress", "blocked"}:
                return "task_not_active", "This scenario requires active or blocked task work."
        elif category == ScenarioCategory.FEEDBACK_RESPONSE:
            if not context.technical_reviews:
                return "review_unavailable", "No persisted technical review is available to respond to."
        return None

    @staticmethod
    def _evidence_matches(category: ScenarioCategory, evidence_id: UUID,
                          context: AgentContext) -> bool:
        if category == ScenarioCategory.COLLABORATION:
            return any(message.get("id") == evidence_id and
                       str(message.get("sender_type")) in {"team_member", "teammate"}
                       for message in context.manager_communications)
        if category in {ScenarioCategory.TIME_MANAGEMENT, ScenarioCategory.PRIORITIZATION,
                        ScenarioCategory.OWNERSHIP}:
            expected_type = {
                ScenarioCategory.TIME_MANAGEMENT: "deadline_warning",
                ScenarioCategory.PRIORITIZATION: "priority_change",
                ScenarioCategory.OWNERSHIP: "bug_report",
            }[category]
            return any(event.get("id") == evidence_id and event.get("event_type") == expected_type
                       for event in context.scenario_history)
        if category == ScenarioCategory.FEEDBACK_RESPONSE:
            return any(review.get("id") == evidence_id and
                       str(review.get("result")) in {"changes_required", "rejected"}
                       for review in context.technical_reviews)
        if category == ScenarioCategory.REQUIREMENT_CHANGE:
            return (any(item.get("version_id") == evidence_id for item in context.submission_history)
                    or any(item.get("interaction_id") == evidence_id and item.get("interaction_type") in {
                        "approach_note", "work_note", "task_work",
                    } for item in context.interaction_history))
        return False

    @staticmethod
    def _duplicate_or_conflict(category: ScenarioCategory, context: AgentContext) -> Optional[tuple[str, str]]:
        open_statuses = {"pending", "acknowledged"}
        for event in context.scenario_history:
            event_type = str(event.get("event_type") or "")
            scenario_key = event.get("scenario_key")
            unresolved = str(event.get("status") or "") in open_statuses
            if unresolved and event_type == "requirement_change":
                code = "unresolved_requirement_change" if category == ScenarioCategory.REQUIREMENT_CHANGE else "conflicting_unresolved_scenario"
                return code, "An unresolved requirement change already exists for this task."
            if unresolved and (scenario_key == category.value or event_type == category.value):
                return "duplicate_unresolved_scenario", "An unresolved scenario of this category already exists."
        return None

    async def _generate(self, category: ScenarioCategory, task: dict[str, Any],
                        context: AgentContext) -> tuple[ScenarioProposal, str, float]:
        fallback = _FALLBACK_PROPOSALS[category]
        if isinstance(self.ai_service, MockAIService):
            # The existing mock provider has a scenario-specific deterministic response.
            generated = await self.ai_service.generate_json(
                _generation_prompt(category, task, context),
                system_prompt="Generate a concise scenario proposal as JSON.",
            )
            try:
                return ScenarioProposal(category=category, **generated), "mock_provider", 0.72
            except Exception:
                return ScenarioProposal(category=category, title=fallback[0], situation=fallback[1],
                                        student_action=fallback[2], relevance=fallback[3]), "deterministic_fallback", 0.55

        try:
            generated = await self.ai_service.generate_json(
                _generation_prompt(category, task, context),
                system_prompt=("Generate a concise workplace scenario proposal grounded only in the supplied "
                               "persisted context. Return JSON with title, situation, student_action, relevance. "
                               "Do not claim an event was persisted or invent student evidence."),
            )
            proposal = ScenarioProposal(category=category, **generated)
            return proposal, "ai_provider", 0.82
        except Exception as exc:
            logger.warning("Scenario language generation failed category=%s error_type=%s",
                           category.value, type(exc).__name__)
            return ScenarioProposal(category=category, title=fallback[0], situation=fallback[1],
                                    student_action=fallback[2], relevance=fallback[3]), "deterministic_fallback", 0.55

    @staticmethod
    def _no_scenario(payload: ScenarioAgentInput, task: Optional[dict[str, Any]],
                     reason_code: str, reason: str) -> ScenarioAgentOutput:
        return ScenarioAgentOutput(decision="not_proposed", reason_code=reason_code,
                                   reason=reason, simulation_id=payload.simulation_id,
                                   task_id=task.get("id") if task else None,
                                   confidence=1.0, generation_source="deterministic")


def register_scenario_agent(registry: AgentRegistry, db: Session, *,
                            context_manager: Optional[AgentContextManager] = None,
                            ai_service: Optional[AIService] = None) -> ScenarioAgent:
    """Register one database-scoped Scenario Agent in an explicitly owned registry."""
    agent = ScenarioAgent(db, context_manager=context_manager, ai_service=ai_service)
    registry.register(agent)
    return agent


def create_scenario_orchestrator(db: Session, *,
                                 context_manager: Optional[AgentContextManager] = None,
                                 ai_service: Optional[AIService] = None):
    """Convenience factory; each call owns a fresh registry and orchestrator."""
    from app.agents.orchestrator import AgentOrchestrator

    registry = AgentRegistry()
    register_scenario_agent(registry, db, context_manager=context_manager, ai_service=ai_service)
    return AgentOrchestrator(registry)


def _generation_prompt(category: ScenarioCategory, task: dict[str, Any],
                       context: AgentContext) -> str:
    criteria = context.active_criteria or {}
    compact_context = {
        "scenario_category": category.value,
        "task_title": task.get("title"),
        "task_description": task.get("description"),
        "task_status": task.get("task_status"),
        "sprint": context.current_sprint,
        "active_criteria": criteria.get("items", []),
        "previous_criteria": context.previous_criteria[:3],
        "competency_gaps": (context.competencies or {}).get("weaknesses", []),
        "review_outcomes": [{"result": item.get("result"), "summary": item.get("summary")}
                            for item in context.technical_reviews[:3]],
        "recent_workplace_events": [{"event_type": event.get("event_type"),
                                      "status": event.get("status"),
                                      "title": event.get("title"),
                                      "message": str(event.get("message") or "")[:400]}
                                     for event in context.scenario_history[:4]],
        "recent_communications": [{"sender_type": item.get("sender_type"),
                                   "content": str(item.get("content") or "")[:300]}
                                  for item in context.manager_communications[:3]],
    }
    return "Create one realistic proposal. Return only JSON. Context:\n" + json.dumps(
        compact_context, default=str, ensure_ascii=True
    )
