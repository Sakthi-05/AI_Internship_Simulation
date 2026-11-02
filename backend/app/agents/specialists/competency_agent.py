"""Evidence-only consolidation of the project's existing competency records."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents.base_agent import BaseAgent
from app.agents.context_manager import AgentContextManager
from app.agents.contracts import AgentContext, EvidenceReference
from app.agents.registry import AgentRegistry
from app.services.ai.ai_service import AIService
from app.services.intelligence_service import COMPETENCIES


_REQUEST_DB: ContextVar[Optional[Session]] = ContextVar("competency_agent_db", default=None)


@contextmanager
def bind_competency_runtime(db: Session):
    """Bind request-scoped persistence while the app reuses its agent instance."""
    token = _REQUEST_DB.set(db)
    try:
        yield
    finally:
        _REQUEST_DB.reset(token)


class CompetencyAgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_id: UUID
    student_id: str = Field(min_length=1, max_length=100)
    task_id: Optional[UUID] = None


class DimensionAssessment(BaseModel):
    dimension: str
    current_score: Optional[float] = Field(default=None, ge=0, le=100)
    evidence_count: int = Field(ge=0)
    evidence_status: str
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    supporting_evidence: list[EvidenceReference] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class CompetencyAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: str
    simulation_id: UUID
    task_id: Optional[UUID] = None
    dimensions: list[DimensionAssessment]
    assessment_history_count: int = Field(ge=0)
    evidence_reference_count: int = Field(ge=0)
    persisted: bool = False
    source: str = "existing_assessment_records"


class CompetencyContextError(ValueError):
    """The requested simulation context does not belong to the student."""


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score if 0 <= score <= 100 else None


def _time_key(value: Optional[datetime]) -> float:
    if value is None:
        return float("-inf")
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return normalized.timestamp()


def _ref_index(context: AgentContext, payload: CompetencyAgentInput) -> dict[tuple[str, UUID], EvidenceReference]:
    index = {}
    for ref in context.evidence:
        if str(ref.student_id) != payload.student_id:
            continue
        if ref.simulation_id is not None and ref.simulation_id != payload.simulation_id:
            continue
        if payload.task_id is not None and ref.task_id is not None and ref.task_id != payload.task_id:
            continue
        index[(ref.evidence_type, ref.evidence_record_id)] = ref
    return index


class CompetencyAgent(BaseAgent):
    agent_id = "competency"
    description = "Consolidates persisted competency assessments with evidence provenance."
    responsibility = "Explain recorded competency scores and identify evidence gaps without rescoring."
    input_model = CompetencyAgentInput
    output_model = CompetencyAgentOutput

    def __init__(self, db: Optional[Session] = None, *,
                 context_manager: Optional[AgentContextManager] = None,
                 ai_service: Optional[AIService] = None):
        # ai_service is accepted for a consistent specialist construction
        # contract. This evidence consolidator deliberately makes no LLM call.
        self.db = db
        self.context_manager = context_manager or AgentContextManager()

    async def execute(self, payload: CompetencyAgentInput,
                      context: Optional[AgentContext] = None) -> CompetencyAgentOutput:
        db = self.db or _REQUEST_DB.get()
        if db is None:
            raise CompetencyContextError("Competency assessment context is unavailable")
        shared = self.context_manager.build(
            db, simulation_id=payload.simulation_id, student_id=payload.student_id,
            profile="competency", task_id=payload.task_id,
        )
        if shared.simulation_id != payload.simulation_id or str(shared.student_id) != payload.student_id:
            raise CompetencyContextError("Competency context does not match the requested student and simulation")

        refs = _ref_index(shared, payload)
        # Per dimension, retain distinct persisted record scores. Repeated
        # references to the same row cannot increase evidence counts or weight.
        score_records: dict[str, dict[tuple[str, UUID], tuple[float, Optional[datetime], EvidenceReference]]] = {
            key: {} for key in COMPETENCIES
        }
        unscored_refs: dict[str, dict[tuple[str, UUID], EvidenceReference]] = {
            key: {} for key in COMPETENCIES
        }
        conflicts: dict[str, set[tuple[str, UUID]]] = {key: set() for key in COMPETENCIES}

        def add_score(dimension: str, evidence_type: str, record_id: Any, value: Any,
                      occurred_at: Optional[datetime]) -> None:
            if dimension not in score_records:
                return
            try:
                identity = (evidence_type, UUID(str(record_id)))
            except (TypeError, ValueError):
                return
            ref = refs.get(identity)
            score = _number(value)
            if ref is None or score is None:
                return
            prior = score_records[dimension].get(identity)
            if prior is not None:
                if prior[0] != score:
                    conflicts[dimension].add(identity)
                return
            score_records[dimension][identity] = (score, occurred_at or ref.occurred_at, ref)

        for attempt in shared.assessment_history:
            attempt_id = attempt.get("id")
            scores = attempt.get("competencies") or {}
            for dimension, value in scores.items():
                add_score(dimension, "assessment_attempt", attempt_id, value, attempt.get("completed_at"))

        interaction_confidence: dict[tuple[str, UUID], Optional[float]] = {}
        recommendations_by_dimension: dict[str, list[str]] = {key: [] for key in COMPETENCIES}
        for interaction in shared.interaction_history:
            evaluation = interaction.get("evaluation") or {}
            evaluation_id = evaluation.get("id")
            values = evaluation.get("competencies") or {}
            for dimension, value in values.items():
                add_score(dimension, "interaction_evaluation", evaluation_id, value,
                          interaction.get("occurred_at"))
                if dimension in recommendations_by_dimension:
                    for recommendation in evaluation.get("recommendations", []) or []:
                        recommendation = str(recommendation).strip()
                        if recommendation and recommendation not in recommendations_by_dimension[dimension]:
                            recommendations_by_dimension[dimension].append(recommendation)
                    try:
                        confidence = float(evaluation.get("confidence"))
                        interaction_confidence[("interaction_evaluation", UUID(str(evaluation_id)))] = (
                            confidence if 0 <= confidence <= 1 else None
                        )
                    except (TypeError, ValueError):
                        pass

        observations = (shared.competencies or {}).get("observations", [])
        for observation in observations:
            dimension = observation.get("competency")
            record_id = observation.get("id")
            identity = None
            try:
                identity = ("soft_skill_evidence", UUID(str(record_id)))
            except (TypeError, ValueError):
                continue
            ref = refs.get(identity)
            if dimension not in score_records or ref is None:
                continue
            score = _number(observation.get("score"))
            if observation.get("status") == "scored" and score is not None:
                add_score(dimension, identity[0], identity[1], score, ref.occurred_at)
            else:
                unscored_refs[dimension][identity] = ref

        # Supporting artifacts without a persisted dimension score count as
        # evidence of activity, never as a negative or positive score.
        for review in shared.technical_reviews:
            if review.get("status") != "completed":
                continue
            review_id = review.get("id")
            try:
                review_ref = refs.get(("technical_review", UUID(str(review_id)))) if review_id else None
            except (TypeError, ValueError):
                review_ref = None
            if review_ref is None:
                continue
            version_id = review.get("version_id")
            submission_record = next((item for item in shared.submission_history
                                      if str(item.get("version_id")) == str(version_id)
                                      and str(item.get("student_id")) == payload.student_id), None)
            version_ref = next((ref for ref in refs.values()
                                if ref.evidence_type == "submission_version"
                                and str(ref.submission_version_id) == str(version_id)), None)
            criteria_ref_present = (review_ref.criteria_version_id is None or any(
                ref.evidence_type == "acceptance_criteria_version"
                and ref.evidence_record_id == review_ref.criteria_version_id
                for ref in refs.values()
            ))
            if (submission_record is None or version_ref is None
                    or review_ref.submission_version_id != version_ref.submission_version_id
                    or review_ref.criteria_version_id != version_ref.criteria_version_id
                    or not criteria_ref_present):
                continue
            for dimension in ("technical_knowledge", "problem_solving", "critical_thinking"):
                unscored_refs[dimension][("technical_review", review_ref.evidence_record_id)] = review_ref

        # Recorded scope-change events and later submission artifacts support
        # adaptability context, but do not independently receive a score.
        requirement_criteria_ids = set()
        requirement_event_ids = set()
        for event in shared.workplace_events:
            if event.get("event_type") != "requirement_change":
                continue
            try:
                requirement_event_ids.add(UUID(str(event.get("id"))))
                if event.get("criteria_version_id"):
                    requirement_criteria_ids.add(UUID(str(event["criteria_version_id"])))
            except (TypeError, ValueError):
                continue
        for ref in refs.values():
            if ref.evidence_type == "workflow_event" and ref.evidence_record_id in requirement_event_ids:
                unscored_refs["adaptability"][(ref.evidence_type, ref.evidence_record_id)] = ref
            elif (ref.evidence_type in {"submission_version", "acceptance_criteria_version"}
                  and ref.criteria_version_id in requirement_criteria_ids):
                unscored_refs["adaptability"][(ref.evidence_type, ref.evidence_record_id)] = ref

        own_messages = [item for item in shared.manager_communications
                        if item.get("sender_type") == "student"
                        and str(item.get("sender_id")) == payload.student_id]
        for message in own_messages:
            try:
                ref = refs.get(("manager_communication", UUID(str(message.get("id")))))
            except (TypeError, ValueError):
                ref = None
            if ref is not None:
                unscored_refs["communication"][(ref.evidence_type, ref.evidence_record_id)] = ref

        profile = shared.competencies or {}
        strengths_by_name = {str(value).strip().lower().replace(" ", "_")
                             for value in profile.get("strengths", [])}
        gaps_by_name = {str(value).strip().lower().replace(" ", "_")
                        for value in profile.get("weaknesses", [])}
        dimensions = []
        all_used_refs: dict[tuple[str, UUID], EvidenceReference] = {}
        for dimension in COMPETENCIES:
            scored = score_records[dimension]
            unscored = unscored_refs[dimension]
            supporting = {**unscored, **{identity: row[2] for identity, row in scored.items()}}
            for identity, ref in supporting.items():
                all_used_refs[identity] = ref
            if conflicts[dimension]:
                state = "conflicting_evidence"
                current_score = None
            elif scored:
                state = "sufficient_evidence"
                latest = max(scored.values(), key=lambda row: _time_key(row[1]))
                current_score = latest[0]
            elif supporting:
                state = "insufficient_evidence"
                current_score = None
            else:
                state = "missing_evidence"
                current_score = None

            normalized = dimension.lower()
            strengths = [dimension] if normalized in strengths_by_name else []
            gaps = [dimension] if normalized in gaps_by_name else []
            recommendations = recommendations_by_dimension[dimension][:5]
            if state in {"missing_evidence", "insufficient_evidence"}:
                recommendations.append("Collect a recorded, dimension-specific assessment before assigning a score.")
            if conflicts[dimension]:
                recommendations.append("Review the conflicting persisted records; no score was selected.")
            # The existing profile strength/gap labels are ordered lists, not
            # dimension-specific recommendations; do not infer extra findings.
            latest_identity = max(scored, key=lambda key: _time_key(scored[key][1])) if scored else None
            confidence = interaction_confidence.get(latest_identity) if latest_identity else None
            dimensions.append(DimensionAssessment(
                dimension=dimension, current_score=current_score,
                evidence_count=len(supporting), evidence_status=state,
                confidence=confidence, supporting_evidence=list(supporting.values()),
                strengths=strengths, gaps=gaps, recommendations=recommendations,
            ))

        return CompetencyAgentOutput(
            student_id=payload.student_id, simulation_id=payload.simulation_id,
            task_id=payload.task_id or shared.current_task_id,
            dimensions=dimensions,
            assessment_history_count=len(shared.assessment_history),
            evidence_reference_count=len(all_used_refs),
        )


def register_competency_agent(registry: AgentRegistry, db: Optional[Session] = None, *,
                              context_manager: Optional[AgentContextManager] = None,
                              ai_service: Optional[AIService] = None) -> CompetencyAgent:
    agent = CompetencyAgent(db, context_manager=context_manager, ai_service=ai_service)
    registry.register(agent)
    return agent


async def invoke_competency_agent(orchestrator, db: Session, *, simulation_id: UUID,
                                  student_id: str, task_id: Optional[UUID] = None,
                                  timeout_seconds: float = 30):
    """Invoke the registered agent through the shared application orchestrator."""
    from app.agents.contracts import AgentRequest
    from app.agents.specialists.competency_agent import bind_competency_runtime

    with bind_competency_runtime(db):
        return await orchestrator.execute(AgentRequest(
            agent_id="competency", simulation_id=simulation_id,
            payload={"simulation_id": str(simulation_id), "student_id": student_id,
                     "task_id": str(task_id) if task_id else None},
            timeout_seconds=timeout_seconds,
        ))
