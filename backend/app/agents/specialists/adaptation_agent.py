"""Competency-aware task-focus recommendation, with no workflow writes."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents.base_agent import BaseAgent
from app.agents.context_manager import AgentContextManager
from app.agents.contracts import AgentContext, EvidenceReference
from app.agents.orchestrator import AgentOrchestrator
from app.agents.registry import AgentRegistry
from app.models.enums import TaskType
from app.services.ai.ai_service import AIService
from app.services.intelligence_service import COMPETENCIES
from app.services.recommendation import BASELINE_FOCUS_THRESHOLD, BASELINE_STRENGTH_THRESHOLD
from app.services.engines.task_engine import _FOCUS_TASKS

logger = logging.getLogger(__name__)
_REQUEST_DB: ContextVar[Optional[Session]] = ContextVar("adaptation_agent_db", default=None)


@contextmanager
def bind_adaptation_runtime(db: Session):
    """Bind a request-owned session for the app-owned adaptation agent."""
    token = _REQUEST_DB.set(db)
    try:
        yield
    finally:
        _REQUEST_DB.reset(token)


class AdaptationAgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_id: UUID
    student_id: str = Field(min_length=1, max_length=100)
    task_id: UUID
    review_id: UUID
    technical_adaptation: dict[str, Any] = Field(default_factory=dict)


class AdaptationAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str
    simulation_id: UUID
    student_id: str
    task_id: UUID
    review_id: UUID
    focus_competencies: list[str] = Field(default_factory=list)
    strength_competencies: list[str] = Field(default_factory=list)
    proposed_task_type: Optional[str] = None
    task_focus: Optional[str] = None
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    existing_next_difficulty: Optional[str] = None
    difficulty_changed_by_agent: bool = False


class AdaptationContextError(ValueError):
    """The adaptation request does not match persisted simulation evidence."""


class AdaptationAgent(BaseAgent):
    agent_id = "adaptation"
    description = "Recommends an evidence-backed competency focus for an eligible next task."
    responsibility = "Use existing competency assessments to focus future practice without changing progression state."
    input_model = AdaptationAgentInput
    output_model = AdaptationAgentOutput

    def __init__(self, db: Optional[Session] = None, *,
                 context_manager: Optional[AgentContextManager] = None,
                 orchestrator: Optional[AgentOrchestrator] = None,
                 ai_service: Optional[AIService] = None):
        self.db = db
        self.context_manager = context_manager or AgentContextManager()
        self.orchestrator = orchestrator
        # Accepted to match specialist wiring; evidence-based adaptation is
        # deterministic and must not ask a model to invent competency scores.
        self.ai_service = ai_service

    async def execute(self, payload: AdaptationAgentInput,
                      context: Optional[AgentContext] = None) -> AdaptationAgentOutput:
        db = self.db or _REQUEST_DB.get()
        common = {
            "simulation_id": payload.simulation_id,
            "student_id": payload.student_id,
            "task_id": payload.task_id,
            "review_id": payload.review_id,
            "existing_next_difficulty": payload.technical_adaptation.get("next_difficulty"),
            "difficulty_changed_by_agent": False,
        }
        if db is None:
            return AdaptationAgentOutput(status="fallback", reason="database_context_unavailable", **common)

        try:
            shared = self.context_manager.build(
                db, simulation_id=payload.simulation_id, student_id=payload.student_id,
                profile="adaptation", task_id=payload.task_id,
            )
        except Exception as exc:
            logger.warning("Adaptation context unavailable error_type=%s", type(exc).__name__)
            return AdaptationAgentOutput(status="fallback", reason="simulation_context_unavailable", **common)
        if shared.simulation_id != payload.simulation_id or str(shared.student_id) != payload.student_id:
            raise AdaptationContextError("Adaptation context does not match the requested student and simulation")
        if shared.current_task_id != payload.task_id:
            return AdaptationAgentOutput(status="fallback", reason="task_context_stale", **common)

        review = next((item for item in shared.technical_reviews
                       if str(item.get("id")) == str(payload.review_id)
                       and item.get("status") == "completed"), None)
        if review is None:
            return AdaptationAgentOutput(status="fallback", reason="completed_review_not_available", **common)
        review_ref = next((ref for ref in shared.evidence
                           if ref.evidence_type == "technical_review"
                           and ref.evidence_record_id == payload.review_id
                           and ref.student_id == payload.student_id
                           and ref.simulation_id == payload.simulation_id), None)
        version_ref = next((ref for ref in shared.evidence
                            if ref.evidence_type == "submission_version"
                            and str(ref.submission_version_id) == str(review.get("version_id"))
                            and ref.student_id == payload.student_id
                            and ref.simulation_id == payload.simulation_id), None)
        if (not any(str(item.get("version_id")) == str(review.get("version_id"))
                    and str(item.get("student_id")) == payload.student_id
                    for item in shared.submission_history)
                or review_ref is None or version_ref is None
                or review_ref.criteria_version_id != version_ref.criteria_version_id):
            return AdaptationAgentOutput(status="fallback", reason="review_submission_provenance_unavailable", **common)

        if self.orchestrator is None:
            return AdaptationAgentOutput(status="fallback", reason="competency_agent_unavailable", **common)
        from app.agents.specialists.competency_agent import invoke_competency_agent

        competency_response = await invoke_competency_agent(
            self.orchestrator, db, simulation_id=payload.simulation_id,
            student_id=payload.student_id, task_id=payload.task_id,
        )
        if competency_response.result is None or competency_response.status.value != "succeeded":
            return AdaptationAgentOutput(status="fallback", reason="competency_evidence_unavailable", **common)

        dimensions = competency_response.result.get("dimensions", [])
        reliable = []
        strengths = []
        for dimension in dimensions:
            name = dimension.get("dimension")
            score = dimension.get("current_score")
            refs = dimension.get("supporting_evidence") or []
            if name not in COMPETENCIES or dimension.get("evidence_status") != "sufficient_evidence":
                continue
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not refs:
                continue
            relevant_refs = []
            for raw_ref in refs:
                try:
                    ref = EvidenceReference.model_validate(raw_ref)
                except Exception:
                    continue
                if (ref.student_id == payload.student_id
                        and ref.simulation_id in (None, payload.simulation_id)):
                    relevant_refs.append(ref)
            if not relevant_refs:
                continue
            reliable.append((str(name), float(score), relevant_refs))
            if float(score) >= BASELINE_STRENGTH_THRESHOLD:
                strengths.append(str(name))

        weak = [item for item in reliable if item[1] < BASELINE_FOCUS_THRESHOLD]
        weak.sort(key=lambda item: (item[1], COMPETENCIES.index(item[0])))
        # Task engine currently supports at most two focus dimensions. Keep a
        # single targeted practice goal to avoid diluting the sprint objective.
        selected = weak[:1]
        if not selected:
            return AdaptationAgentOutput(
                status="fallback", reason="no_evidence_backed_competency_gap",
                strength_competencies=strengths,
                evidence_references=_unique_refs([ref for _, _, refs in reliable for ref in refs]),
                **common,
            )

        target, score, refs = selected[0]
        task_spec = _FOCUS_TASKS.get(target)
        proposed_task_type = task_spec[0].value if task_spec else None
        focus = f"Practice {target}"
        if proposed_task_type:
            focus += f" through a {proposed_task_type.replace('_', ' ')} task"
        rationale = (
            f"The existing competency assessment records {target} at {score:.1f}/100, "
            f"below the established {BASELINE_FOCUS_THRESHOLD:.0f}-point focus threshold. "
            "The recommendation changes task focus only; technical difficulty remains governed by the existing adaptation service."
        )
        return AdaptationAgentOutput(
            status="recommended", reason=rationale,
            focus_competencies=[target], strength_competencies=strengths,
            proposed_task_type=proposed_task_type, task_focus=focus,
            evidence_references=_unique_refs([*refs, review_ref, version_ref]), **common,
        )


def _unique_refs(refs: list[EvidenceReference]) -> list[EvidenceReference]:
    result = {}
    for ref in refs:
        result[(ref.evidence_type, ref.evidence_record_id)] = ref
    return list(result.values())


def register_adaptation_agent(registry: AgentRegistry, db: Optional[Session] = None, *,
                              context_manager: Optional[AgentContextManager] = None,
                              orchestrator: Optional[AgentOrchestrator] = None,
                              ai_service: Optional[AIService] = None) -> AdaptationAgent:
    agent = AdaptationAgent(db, context_manager=context_manager,
                            orchestrator=orchestrator, ai_service=ai_service)
    registry.register(agent)
    return agent


async def invoke_adaptation_agent(orchestrator: AgentOrchestrator, db: Session, *,
                                  payload: AdaptationAgentInput, timeout_seconds: float = 20):
    from app.agents.contracts import AgentRequest

    with bind_adaptation_runtime(db):
        return await orchestrator.execute(AgentRequest(
            agent_id="adaptation", simulation_id=payload.simulation_id,
            payload=payload.model_dump(mode="json"), timeout_seconds=timeout_seconds,
        ))
