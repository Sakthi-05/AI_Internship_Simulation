"""Orchestrated entry point for the existing technical review service.

This agent validates ownership and provenance, then delegates evaluation and
the established review lifecycle to ``ReviewService``. It contains no second
review algorithm and does not write review/task state itself.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base_agent import BaseAgent
from app.agents.context_manager import AgentContextManager
from app.agents.contracts import AgentContext, EvidenceReference
from app.agents.registry import AgentRegistry
from app.models.criteria_version import AcceptanceCriteriaVersion
from app.models.enums import ReviewStatus
from app.models.project import Project
from app.models.simulation import Simulation
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task
from app.services.ai.ai_service import AIService, get_ai_service
from app.services.ai.review_service import ReviewService
from app.services.context.context_builder import build_review_context

logger = logging.getLogger(__name__)
_REQUEST_RUNTIME: ContextVar[tuple[Session, Optional[AIService], Any] | None] = ContextVar(
    "technical_review_runtime", default=None
)


@contextmanager
def bind_technical_review_runtime(db: Session, ai_service: Optional[AIService] = None,
                                  orchestrator: Any = None):
    """Bind request-scoped dependencies while reusing the app-owned agent."""
    token = _REQUEST_RUNTIME.set((db, ai_service, orchestrator))
    try:
        yield
    finally:
        _REQUEST_RUNTIME.reset(token)


class TechnicalReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_id: UUID
    student_id: str = Field(min_length=1, max_length=100)
    submission_id: UUID
    task_id: Optional[UUID] = None
    submission_version_id: Optional[UUID] = None


class TechnicalReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: str
    review_id: Optional[UUID] = None
    result: Optional[str] = None
    score: Optional[float] = None
    severity: Optional[str] = None
    summary: str
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    student_id: str
    simulation_id: UUID
    task_id: UUID
    submission_id: UUID
    submission_version_id: UUID
    submission_version_number: int
    criteria_version_id: Optional[UUID] = None
    criteria_version_number: Optional[int] = None
    supporting_evidence: list[EvidenceReference] = Field(default_factory=list)
    evaluation_source: Optional[str] = None
    evidence_level: Optional[str] = None
    stale: bool = False
    # Preserve the established review endpoint payload while adding typed
    # orchestration metadata around it.
    review_response: dict[str, Any] = Field(default_factory=dict)


class TechnicalReviewContextError(ValueError):
    """Requested persisted review context is missing or inconsistent."""


def _criteria_provenance(db: Session, version: SubmissionVersion, task: Task) -> tuple[Optional[UUID], Optional[int]]:
    """Require a saved snapshot or recover the precise referenced version.

    ``build_review_context`` supports older versions whose metadata contains a
    criteria-version identity. It can otherwise fall back to today's criteria;
    this agent explicitly blocks that fallback when historical provenance is
    absent so an old submission is never silently reviewed against new rules.
    """
    metadata = version.version_metadata or {}
    identity = metadata.get("acceptance_criteria_version")
    snapshot = metadata.get("acceptance_criteria_snapshot")
    if isinstance(snapshot, list):
        identity = identity if isinstance(identity, dict) else {}
        raw_id = identity.get("id")
        raw_number = identity.get("version_number")
        criteria_id = UUID(str(raw_id)) if raw_id else None
        row = db.get(AcceptanceCriteriaVersion, criteria_id) if criteria_id else None
        if criteria_id and (row is None or row.task_id != task.id):
            raise TechnicalReviewContextError("Submission criteria snapshot reference is invalid")
        if row is None and raw_number is not None:
            row = db.execute(select(AcceptanceCriteriaVersion).where(
                AcceptanceCriteriaVersion.task_id == task.id,
                AcceptanceCriteriaVersion.version_number == int(raw_number),
            )).scalars().first()
        return (row.id, row.version_number) if row else (None, None)

    row = None
    if isinstance(identity, dict):
        raw_id = identity.get("id")
        if raw_id:
            try:
                row = db.get(AcceptanceCriteriaVersion, UUID(str(raw_id)))
            except (TypeError, ValueError):
                row = None
        if row is None and identity.get("version_number") is not None:
            row = db.execute(select(AcceptanceCriteriaVersion).where(
                AcceptanceCriteriaVersion.task_id == task.id,
                AcceptanceCriteriaVersion.version_number == int(identity["version_number"]),
            )).scalars().first()
    if row is None or row.task_id != task.id:
        raise TechnicalReviewContextError("Submission criteria snapshot is unavailable")
    return row.id, row.version_number


class TechnicalReviewAgent(BaseAgent):
    agent_id = "technical_review"
    description = "Evaluates a persisted submission version using the existing review service."
    responsibility = "Return a criteria-pinned technical review with persisted evidence provenance."
    input_model = TechnicalReviewInput
    output_model = TechnicalReviewOutput

    def __init__(self, db: Optional[Session] = None, *, context_manager: Optional[AgentContextManager] = None,
                 ai_service: Optional[AIService] = None):
        self.db = db
        self.context_manager = context_manager or AgentContextManager()
        self.ai_service = ai_service

    async def execute(self, payload: TechnicalReviewInput,
                      context: Optional[AgentContext] = None) -> TechnicalReviewOutput:
        runtime = _REQUEST_RUNTIME.get()
        db = self.db or (runtime[0] if runtime else None)
        ai_service = self.ai_service or (runtime[1] if runtime else None) or get_ai_service()
        orchestrator = runtime[2] if runtime else None
        if db is None:
            raise TechnicalReviewContextError("Review database context is unavailable")
        # Owner scoped context construction also validates simulation/company
        # access and task membership before submission rows are evaluated.
        shared = self.context_manager.build(
            db, simulation_id=payload.simulation_id,
            student_id=payload.student_id, profile="technical_review",
            task_id=payload.task_id,
        )
        simulation = db.get(Simulation, payload.simulation_id)
        if simulation is None or str(simulation.student_id) != payload.student_id:
            raise TechnicalReviewContextError("Simulation is unavailable to this student")

        submission = db.get(Submission, payload.submission_id)
        if submission is None or str(submission.student_id) != payload.student_id:
            raise TechnicalReviewContextError("Submission is unavailable to this student")
        task = db.get(Task, submission.task_id)
        if task is None or (payload.task_id is not None and task.id != payload.task_id):
            raise TechnicalReviewContextError("Submission task does not match the request")
        if shared.current_task_id != task.id:
            raise TechnicalReviewContextError("Submission task is outside this simulation context")

        sprint = db.get(Sprint, task.sprint_id)
        project = db.get(Project, sprint.project_id) if sprint else None
        if project is None or project.id != simulation.project_id:
            raise TechnicalReviewContextError("Submission project does not match this simulation")

        # Lock the submission row before selecting its version. The review
        # service still performs its own idempotency and stale-version checks.
        db.refresh(submission, with_for_update=True)
        version = (db.get(SubmissionVersion, payload.submission_version_id)
                   if payload.submission_version_id else db.execute(
                       select(SubmissionVersion).where(
                           SubmissionVersion.submission_id == submission.id,
                           SubmissionVersion.version_number == submission.current_version,
                       )).scalars().first())
        if version is None or version.submission_id != submission.id:
            raise TechnicalReviewContextError("Submission version is unavailable")
        # Match the existing review API's row lock so concurrent callers
        # serialize on the same version claim before the service is entered.
        db.refresh(version, with_for_update=True)
        if version.version_number != submission.current_version:
            existing = db.execute(select(AIReview).where(
                AIReview.version_id == version.id,
                AIReview.status == ReviewStatus.COMPLETED,
            )).scalars().first()
            if existing is None:
                raise TechnicalReviewContextError("Submission version is stale and cannot affect task progression")

        criteria_id, criteria_number = _criteria_provenance(db, version, task)
        review_context = build_review_context(db, version)
        # A shared profile may have selected the latest version; pin and verify
        # this invocation's exact immutable version for traceability.
        shared.submission_id = submission.id
        shared.task_id = task.id
        shared.criteria_version_id = criteria_id
        matching_refs = [ref for ref in shared.evidence
                         if ref.submission_version_id == version.id]
        if not matching_refs:
            # The version is real and has just been read under the verified
            # submission; retain explicit provenance even when bounded shared
            # history did not include this older version.
            shared.evidence.append(EvidenceReference(
                evidence_type="submission_version", evidence_record_id=version.id,
                student_id=payload.student_id, simulation_id=simulation.id,
                task_id=task.id, submission_id=submission.id,
                submission_version_id=version.id, criteria_version_id=criteria_id,
                occurred_at=version.submitted_at, source="submission_versions",
            ))
        evidence = [ref for ref in shared.evidence if
                    ref.submission_version_id == version.id or
                    (criteria_id is not None and ref.criteria_version_id == criteria_id)]

        # The established service owns provider evaluation, review-row writes,
        # progression, notifications, failure recovery and idempotency.
        result = await ReviewService(ai_service, orchestrator=orchestrator).review_submission_version(db, version)
        raw = result.get("raw") or {}
        criteria_entries = raw.get("acceptance_criteria", []) if isinstance(raw, dict) else []
        findings = [entry for entry in criteria_entries if isinstance(entry, dict)]
        review_id = UUID(str(result["review_id"])) if result.get("review_id") else None
        if review_id is not None and str(result.get("status", "")).lower() == "completed":
            evidence.append(EvidenceReference(
                evidence_type="technical_review", evidence_record_id=review_id,
                student_id=payload.student_id, simulation_id=simulation.id,
                task_id=task.id, submission_id=submission.id,
                submission_version_id=version.id, criteria_version_id=criteria_id,
                source="ai_reviews",
            ))
        return TechnicalReviewOutput(
            review_status=str(result.get("status", "unknown")),
            review_id=review_id,
            result=result.get("result"), score=result.get("score"),
            severity=result.get("severity"), summary=str(result.get("summary") or ""),
            strengths=list(result.get("strengths") or []), risks=list(result.get("risks") or []),
            actions=list(result.get("actions") or []), findings=findings,
            student_id=payload.student_id, simulation_id=simulation.id, task_id=task.id,
            submission_id=submission.id, submission_version_id=version.id,
            submission_version_number=version.version_number,
            criteria_version_id=criteria_id, criteria_version_number=criteria_number,
            supporting_evidence=evidence,
            evaluation_source=raw.get("evaluation_source") if isinstance(raw, dict) else None,
            evidence_level=review_context.get("evidence_level"),
            stale=version.version_number != submission.current_version,
            review_response=dict(result),
        )


def register_technical_review_agent(registry: AgentRegistry, db: Optional[Session] = None, *,
                                    context_manager: Optional[AgentContextManager] = None,
                                    ai_service: Optional[AIService] = None) -> TechnicalReviewAgent:
    agent = TechnicalReviewAgent(db, context_manager=context_manager, ai_service=ai_service)
    registry.register(agent)
    return agent


def create_technical_review_orchestrator(db: Optional[Session] = None, *,
                                         context_manager: Optional[AgentContextManager] = None,
                                         ai_service: Optional[AIService] = None):
    from app.agents.orchestrator import AgentOrchestrator
    registry = AgentRegistry()
    register_technical_review_agent(registry, db, context_manager=context_manager, ai_service=ai_service)
    return AgentOrchestrator(registry)
