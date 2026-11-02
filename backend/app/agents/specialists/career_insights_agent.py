"""Evidence-grounded career insights assembled from existing simulation records."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.agents.base_agent import BaseAgent
from app.agents.context_manager import AgentContextManager
from app.agents.contracts import AgentContext, EvidenceReference
from app.agents.registry import AgentRegistry
from app.agents.specialists.competency_agent import (
    CompetencyAgent, CompetencyAgentInput, CompetencyContextError,
)
from app.services.ai.ai_service import AIService


class CareerInsightsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: str = Field(min_length=1, max_length=100)
    simulation_id: UUID
    recommendations: list[str] = Field(default_factory=list)
    readiness: Optional[dict[str, Any]] = None
    competency_assessment: Optional[dict[str, Any]] = None


class CompetencyTrend(BaseModel):
    dimension: str
    direction: str
    observations: int = Field(ge=2)
    first_score: float
    latest_score: float
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


class CareerInsightsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: str
    simulation_id: UUID
    status: str
    strengths: list[dict[str, Any]] = Field(default_factory=list)
    competency_gaps: list[dict[str, Any]] = Field(default_factory=list)
    trends: list[CompetencyTrend] = Field(default_factory=list)
    trend_status: str
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    readiness: Optional[dict[str, Any]] = None
    sources: dict[str, str] = Field(default_factory=lambda: {
        "competencies": "competency_agent",
        "recommendations": "existing_interaction_evaluations",
        "readiness": "existing_readiness_snapshot",
        "explanations": "career_insights_agent",
    })


def _score(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        return None
    return float(value)


class CareerInsightsAgent(BaseAgent):
    agent_id = "career_insights"
    description = "Summarizes existing simulation evidence and longitudinal competency records."
    responsibility = "Explain supported strengths and gaps without scoring or changing readiness."
    input_model = CareerInsightsInput
    output_model = CareerInsightsOutput

    def __init__(self, db: Optional[Session] = None, *,
                 context_manager: Optional[AgentContextManager] = None,
                 ai_service: Optional[AIService] = None):
        self.db = db
        self.context_manager = context_manager or AgentContextManager()
        self.competency_agent = CompetencyAgent(db, context_manager=self.context_manager, ai_service=ai_service)

    async def execute(self, payload: CareerInsightsInput,
                      context: Optional[AgentContext] = None) -> CareerInsightsOutput:
        from app.agents.specialists.competency_agent import _REQUEST_DB
        db = self.db or _REQUEST_DB.get()
        if db is None:
            raise CompetencyContextError("Career insights context is unavailable")
        shared = self.context_manager.build(
            db, simulation_id=payload.simulation_id, student_id=payload.student_id,
            profile="career_insights",
        )
        if shared.simulation_id != payload.simulation_id or str(shared.student_id) != payload.student_id:
            raise CompetencyContextError("Career insights context does not match the requested student and simulation")
        if payload.competency_assessment is not None:
            from app.agents.specialists.competency_agent import CompetencyAgentOutput
            assessed = CompetencyAgentOutput.model_validate(payload.competency_assessment)
        else:
            assessed = await self.competency_agent.execute(CompetencyAgentInput(
                simulation_id=payload.simulation_id, student_id=payload.student_id,
            ))
        refs = {(r.evidence_type, r.evidence_record_id): r for r in shared.evidence
                if str(r.student_id) == payload.student_id
                and r.simulation_id in (None, payload.simulation_id)}
        strengths, gaps, trends, used = [], [], [], {}
        dimensions = {item.dimension: item for item in assessed.dimensions}
        history: dict[str, list[tuple[Any, float, EvidenceReference]]] = {}
        for interaction in shared.interaction_history:
            evaluation = interaction.get("evaluation") or {}
            for dimension, raw in (evaluation.get("competencies") or {}).items():
                value = _score(raw)
                try:
                    ref = refs.get(("interaction_evaluation", UUID(str(evaluation.get("id")))))
                except (TypeError, ValueError):
                    ref = None
                if (value is not None and ref is not None
                        and ref.simulation_id == payload.simulation_id
                        and ref.occurred_at is not None):
                    history.setdefault(dimension, []).append((ref.occurred_at, value, ref))
        for dimension, result in dimensions.items():
            for ref in result.supporting_evidence:
                if (ref.evidence_type, ref.evidence_record_id) in refs:
                    used[(ref.evidence_type, ref.evidence_record_id)] = ref
            item = {"dimension": dimension, "score": result.current_score,
                    "evidence_status": result.evidence_status,
                    "evidence_references": result.supporting_evidence}
            if result.current_score is not None and result.evidence_status == "sufficient_evidence":
                if result.strengths:
                    strengths.append(item)
                if result.gaps:
                    gaps.append(item)
            records = history.get(dimension, [])
            # Two independently timestamped assessments are the minimum for a trend.
            distinct = {record[0]: record for record in records}
            ordered = sorted(distinct.values(), key=lambda record: record[0])
            if len(ordered) >= 2:
                first, latest = ordered[0], ordered[-1]
                direction = "improving" if latest[1] > first[1] else "declining" if latest[1] < first[1] else "stable"
                trends.append(CompetencyTrend(dimension=dimension, direction=direction,
                    observations=len(ordered), first_score=first[1], latest_score=latest[1],
                    evidence_references=[first[2], latest[2]]))
                used[(first[2].evidence_type, first[2].evidence_record_id)] = first[2]
                used[(latest[2].evidence_type, latest[2].evidence_record_id)] = latest[2]
        has_records = any(d.evidence_count for d in assessed.dimensions)
        return CareerInsightsOutput(
            student_id=payload.student_id, simulation_id=payload.simulation_id,
            status="available" if has_records else "insufficient_evidence",
            strengths=strengths, competency_gaps=gaps, trends=trends,
            trend_status="available" if trends else "insufficient_history",
            evidence_references=list(used.values()),
            recommendations=list(dict.fromkeys(payload.recommendations)),
            readiness=payload.readiness,
        )


def register_career_insights_agent(registry: AgentRegistry, db: Optional[Session] = None, *,
                                   context_manager: Optional[AgentContextManager] = None,
                                   ai_service: Optional[AIService] = None) -> CareerInsightsAgent:
    agent = CareerInsightsAgent(db, context_manager=context_manager, ai_service=ai_service)
    registry.register(agent)
    return agent

