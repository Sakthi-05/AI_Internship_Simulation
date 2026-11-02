"""Read-only, owner-scoped and responsibility-filtered simulation context."""
from __future__ import annotations

from typing import Any, Iterable, Optional
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agents.contracts import AgentContext, EvidenceReference
from app.models.assessment import AssessmentAttempt, StudentSkill
from app.models.company import Company
from app.models.criteria_version import AcceptanceCriteriaVersion
from app.models.event import Event
from app.models.enums import SimulationStatus
from app.models.intelligence import InteractionEvaluation, StudentProfile
from app.models.message import Message
from app.models.manager import Manager
from app.models.project import Project
from app.models.project_state import ProjectState
from app.models.simulation import Simulation, WorkflowEvent
from app.models.simulation_interaction import SimulationInteraction
from app.models.soft_skill import SoftSkillEvidence
from app.models.sprint import Sprint
from app.models.submission import AIReview, Submission, SubmissionVersion
from app.models.task import Task
from app.models.task_path import SimulationTaskPath


class ContextAccessDenied(PermissionError):
    """The authenticated student cannot read the requested simulation."""


class AgentContextManager:
    """Build bounded context from persisted rows; never loads or mutates history wholesale."""

    PROFILES = {
        "scenario": {"task", "criteria", "workflow_events", "project_events", "communications", "submissions", "reviews", "competencies", "interactions"},
        "manager": {"task", "criteria", "communications", "workflow_events", "submissions", "reviews", "competencies", "manager_profile", "project_state", "interactions"},
        "technical_review": {"task", "criteria", "submissions", "reviews"},
        "competency": {"task", "submissions", "reviews", "workflow_events", "communications", "competencies", "assessments", "soft_skills", "interactions"},
        "adaptation": {"task", "criteria", "submissions", "reviews", "workflow_events", "competencies", "assessments", "paths", "soft_skills", "interactions"},
        "career_insights": {"competencies", "assessments", "paths", "simulations", "reviews", "interactions"},
        "full": {"task", "criteria", "communications", "workflow_events", "project_events", "submissions", "reviews", "competencies", "assessments", "paths", "simulations", "soft_skills", "interactions"},
    }
    HISTORY_LIMIT = 12

    def __init__(self, submission_context_loader=None):
        # Reuse the existing review context builder for safe file extraction and
        # immutable criteria recovery. Injectable to keep context tests isolated.
        self.submission_context_loader = submission_context_loader

    def build(self, db: Session, *, simulation_id: UUID, student_id: str,
              profile: str = "full", task_id: Optional[UUID] = None) -> AgentContext:
        """Load only records owned by ``student_id`` and requested by a known profile."""
        if profile not in self.PROFILES:
            raise ValueError(f"Unknown context profile '{profile}'")
        simulation = db.execute(select(Simulation).where(Simulation.id == simulation_id)).scalar_one_or_none()
        if simulation is None or str(simulation.student_id) != str(student_id):
            raise ContextAccessDenied("Simulation is unavailable to this student")

        company = db.execute(select(Company).where(Company.id == simulation.company_id)).scalar_one_or_none()
        if company is None or str(company.student_id) != str(student_id):
            raise ContextAccessDenied("Simulation ownership could not be verified")
        project = db.execute(select(Project).where(Project.id == simulation.project_id,
                                                    Project.company_id == company.id)).scalar_one_or_none()
        if project is None:
            raise ContextAccessDenied("Simulation project could not be verified")

        include = self.PROFILES[profile]
        context = AgentContext(simulation_id=simulation.id, student_id=str(student_id),
                               company_id=company.id, profile=profile)
        refs: list[EvidenceReference] = []

        selected_task_id = task_id or simulation.current_task_id
        task = None
        path = None
        if "task" in include and selected_task_id:
            path = db.execute(select(SimulationTaskPath).where(
                SimulationTaskPath.simulation_id == simulation.id,
                SimulationTaskPath.task_id == selected_task_id,
            )).scalar_one_or_none()
            if path is not None:
                task = db.execute(select(Task).where(Task.id == selected_task_id)).scalar_one_or_none()
                if task is not None:
                    sprint = db.execute(select(Sprint).where(Sprint.id == task.sprint_id)).scalar_one_or_none()
                    if sprint is not None and sprint.project_id == project.id:
                        context.current_task = {"id": task.id, "title": task.title,
                                                "description": task.description,
                                                "status": _value(path.status),
                                                "task_status": _value(task.status),
                                                "difficulty": _value(task.difficulty),
                                                "started_at": getattr(task, "started_at", None),
                                                "deadline": getattr(task, "deadline", None),
                                                "acceptance_criteria": list(task.acceptance_criteria or [])}
                        context.current_sprint = {"id": sprint.id, "number": sprint.sprint_number,
                                                  "name": sprint.name, "status": _value(sprint.status)}
                        context.current_task_id = task.id
                        context.task_id = task.id
                        refs.append(_evidence("task", task, student_id, simulation.id, task.id, "tasks"))
                        task_id = task.id
                    else:
                        task = path = None
                else:
                    path = None
            if context.current_task is None:
                task = None

        if "task" in include:
            context.current_company = {"id": company.id, "name": company.name,
                                       "industry": company.industry}
            context.current_project = {"id": project.id, "title": project.title,
                                       "role": project.role, "status": _value(project.status)}

        if "manager_profile" in include:
            manager = db.execute(select(Manager).where(Manager.company_id == company.id)).scalar_one_or_none()
            if manager is not None:
                context.manager_profile = {"id": manager.id, "name": manager.name,
                                          "title": manager.title,
                                          "personality": _value(manager.personality)}
        if "project_state" in include:
            state = db.execute(select(ProjectState).where(
                ProjectState.project_id == project.id
            )).scalar_one_or_none()
            if state is not None:
                context.project_state = {
                    "completed_tasks": state.completed_tasks,
                    "pending_tasks": state.pending_tasks,
                    "missed_deadlines": state.missed_deadlines,
                    "bug_count": state.bug_count,
                    "manager_satisfaction": state.manager_satisfaction,
                    "team_satisfaction": state.team_satisfaction,
                    "stress_level": state.stress_level,
                    "current_sprint_number": state.current_sprint_number,
                }

        if "criteria" in include and task is not None:
            versions = _rows(db, select(AcceptanceCriteriaVersion).where(
                AcceptanceCriteriaVersion.task_id == task.id
            ).order_by(AcceptanceCriteriaVersion.version_number.desc()).limit(self.HISTORY_LIMIT))
            active = next((v for v in versions if v.status == "active"), None)
            context.active_criteria = _criteria(active) if active else {
                "id": None, "version_number": None, "status": "implicit",
                "items": [{"text": text, "state": "active"} for text in task.acceptance_criteria or []],
                "implicit": True,
            }
            context.criteria_version_id = active.id if active else None
            context.previous_criteria = [_criteria(v) for v in versions if v is not active]
            if active:
                refs.append(_evidence("acceptance_criteria", active, student_id, simulation.id,
                                      task.id, "acceptance_criteria_versions",
                                      criteria_version_id=active.id))
            refs.extend(_evidence("acceptance_criteria_version", v, student_id, simulation.id,
                                  task.id, "acceptance_criteria_versions",
                                  criteria_version_id=v.id) for v in versions if v is not active)

        if "workflow_events" in include:
            event_filters = [WorkflowEvent.simulation_id == simulation.id]
            if task is not None:
                event_filters.append(or_(WorkflowEvent.task_id == task.id, WorkflowEvent.task_id.is_(None)))
            events = _rows(db, select(WorkflowEvent).where(*event_filters)
                           .order_by(WorkflowEvent.occurred_at.desc()).limit(self.HISTORY_LIMIT))
            context.workplace_events.extend(_workflow_event(e) for e in events)
            context.scenario_history = [_workflow_event(e) for e in events]
            if events and task is not None:
                context.workflow_event_id = events[0].id
            refs.extend(_evidence("workflow_event", e, student_id, simulation.id,
                                  e.task_id, "workflow_events", workplace_event_id=e.id)
                        for e in events)

        if "project_events" in include:
            events = _rows(db, select(Event).where(Event.project_id == project.id)
                           .order_by(Event.created_at.desc()).limit(self.HISTORY_LIMIT))
            context.workplace_events.extend(_project_event(e) for e in events)
            refs.extend(_evidence("project_event", e, student_id, simulation.id,
                                  task.id if task else None, "events") for e in events)

        if "communications" in include:
            messages = _rows(db, select(Message).where(Message.company_id == company.id)
                             .order_by(Message.created_at.desc()).limit(self.HISTORY_LIMIT))
            context.manager_communications = [_message(m) for m in messages]
            refs.extend(_evidence("manager_communication", m, student_id, simulation.id,
                                  task.id if task else None, "messages") for m in messages)

        task_ids: list[UUID] = [task.id] if task is not None else []
        paths: list[SimulationTaskPath] = []
        if "paths" in include or "submissions" in include:
            paths = _rows(db, select(SimulationTaskPath).where(
                SimulationTaskPath.simulation_id == simulation.id
            ).order_by(SimulationTaskPath.sequence.desc()).limit(self.HISTORY_LIMIT))
            paths.reverse()
            for entry in paths:
                if entry.task_id not in task_ids:
                    task_ids.append(entry.task_id)
            context.progression_history = [{"task_id": p.task_id, "sequence": p.sequence,
                                            "status": _value(p.status), "started_at": p.started_at,
                                            "completed_at": p.completed_at} for p in paths]
            refs.extend(_evidence("task_progression", p, student_id, simulation.id,
                                  p.task_id, "simulation_task_paths") for p in paths)

        submissions: list[Submission] = []
        versions: list[SubmissionVersion] = []
        if "submissions" in include and task_ids:
            submissions = _rows(db, select(Submission).where(
                Submission.task_id.in_(task_ids), Submission.student_id == str(student_id)
            ).order_by(Submission.created_at.desc()).limit(self.HISTORY_LIMIT))
            for sub in submissions:
                sub_versions = _rows(db, select(SubmissionVersion).where(
                    SubmissionVersion.submission_id == sub.id
                ).order_by(SubmissionVersion.version_number.desc()).limit(self.HISTORY_LIMIT))
                sub_versions.reverse()
                versions.extend(sub_versions)
                for version in sub_versions:
                    criteria_snapshot = _submission_criteria(version)
                    record = {"id": sub.id, "task_id": sub.task_id,
                              "student_id": sub.student_id, "status": _value(sub.status),
                              "version_id": version.id, "version_number": version.version_number,
                              "version_status": _value(version.status), "files": version.files or [],
                              "metadata": version.version_metadata or {},
                              "submitted_at": version.submitted_at,
                              "criteria_snapshot": criteria_snapshot}
                    context.submission_history.append(record)
                    criteria_version_id = _criteria_id(version)
                    refs.append(_evidence("submission_version", version, student_id, simulation.id,
                                          sub.task_id, "submission_versions", submission_id=sub.id,
                                          submission_version_id=version.id,
                                          criteria_version_id=criteria_version_id))
                    refs.append(_evidence("submission", sub, student_id, simulation.id,
                                          sub.task_id, "submissions", submission_id=sub.id))

            if profile == "technical_review" and task is not None:
                current_versions = [v for v in versions if any(s.id == v.submission_id for s in submissions)
                                    and any(s.task_id == task.id and s.id == v.submission_id for s in submissions)]
                latest = max(current_versions, key=lambda v: v.version_number, default=None)
                if latest is not None:
                    # For review, expose the criteria snapshot saved with this exact version.
                    context.active_criteria = _submission_criteria(latest)
                    context.criteria_version_id = _criteria_id(latest)
                    context.submission_id = next((s.id for s in submissions if s.task_id == task.id
                                                  and s.id == latest.submission_id), None)
                    try:
                        loader = self.submission_context_loader
                        if loader is None:
                            from app.services.context.context_builder import build_review_context
                            loader = build_review_context
                        review_context = loader(db, latest)
                        context.submission_content = review_context.get("submission_content")
                        context.submission_evidence_level = review_context.get("evidence_level")
                        exact_criteria = review_context.get("acceptance_criteria")
                        exact_identity = review_context.get("acceptance_criteria_version") or {}
                        if isinstance(exact_criteria, list):
                            context.active_criteria = {
                                "id": _uuid_or_none(exact_identity.get("id")),
                                "version_number": exact_identity.get("version_number"),
                                "status": exact_identity.get("status"),
                                "items": [{"text": item, "state": "active"}
                                          for item in exact_criteria],
                                "implicit": bool(exact_identity.get("implicit")),
                                "snapshot_available": True,
                            }
                            context.criteria_version_id = _uuid_or_none(exact_identity.get("id"))
                    except Exception:
                        # Missing files or an incomplete historical relationship
                        # must not replace the stored criteria snapshot or block context.
                        pass

        if "reviews" in include and versions:
            review_rows = _rows(db, select(AIReview).where(
                AIReview.version_id.in_([v.id for v in versions])
            ).order_by(AIReview.created_at.desc()).limit(self.HISTORY_LIMIT * 3))
            context.technical_reviews = [_review(review) for review in review_rows]
            version_map = {v.id: v for v in versions}
            submission_map = {s.id: s for s in submissions}
            for review in review_rows:
                version = version_map.get(review.version_id)
                sub = submission_map.get(version.submission_id) if version else None
                refs.append(_evidence("technical_review", review, student_id, simulation.id,
                                      sub.task_id if sub else None, "ai_reviews",
                                      submission_id=sub.id if sub else None,
                                      submission_version_id=version.id if version else None,
                                      criteria_version_id=_criteria_id(version) if version else None))

        if "competencies" in include:
            profile_row = db.execute(select(StudentProfile).where(
                StudentProfile.student_id == str(student_id)
            )).scalar_one_or_none()
            skills = _rows(db, select(StudentSkill).where(
                StudentSkill.student_id == str(student_id)
            ).limit(self.HISTORY_LIMIT * 4))
            context.competencies = {
                "baseline": dict(profile_row.baseline_competencies or {}) if profile_row else {},
                "current": dict(profile_row.current_competencies or {}) if profile_row else {},
                "strengths": list(profile_row.strengths or []) if profile_row else [],
                "weaknesses": list(profile_row.weaknesses or []) if profile_row else [],
                "skills": [{"student_skill_id": s.id, "skill_id": s.skill_id,
                            "self_rating": s.self_rating} for s in skills],
                "evidence_available": bool(profile_row or skills),
            }
            if profile_row:
                refs.append(_evidence("competency_profile", profile_row, student_id,
                                      None, task.id if task else None, "student_profiles"))
            refs.extend(_evidence("student_skill", s, student_id, None,
                                  task.id if task else None, "student_skills") for s in skills)

        if "assessments" in include:
            attempts = _rows(db, select(AssessmentAttempt).where(
                AssessmentAttempt.student_id == str(student_id),
                AssessmentAttempt.status == "completed",
            ).order_by(AssessmentAttempt.completed_at.desc()).limit(self.HISTORY_LIMIT))
            context.assessment_history = [{"id": a.id, "status": a.status,
                                           "overall_score": a.overall_score,
                                           "competencies": a.competencies or {},
                                           "started_at": a.started_at,
                                           "completed_at": a.completed_at} for a in attempts]
            refs.extend(_evidence("assessment_attempt", a, student_id, None,
                                  None, "assessment_attempts", occurred_at=a.completed_at) for a in attempts)

        if "soft_skills" in include:
            soft_rows = _rows(db, select(SoftSkillEvidence).where(
                SoftSkillEvidence.simulation_id == simulation.id,
                SoftSkillEvidence.student_id == str(student_id),
            ).order_by(SoftSkillEvidence.created_at.desc()).limit(self.HISTORY_LIMIT))
            refs.extend(_evidence("soft_skill_evidence", e, student_id, simulation.id,
                                  e.task_id, "soft_skill_evidence", workplace_event_id=e.event_id)
                        for e in soft_rows)
            # Included as separate evidence records without turning missing scores into zeroes.
            context.competencies = context.competencies or {
                "baseline": {}, "current": {}, "strengths": [], "weaknesses": [],
                "skills": [], "evidence_available": False,
            }
            context.competencies["observations"] = [
                {"id": e.id, "competency": e.competency, "status": e.status,
                 "score": e.score, "summary": e.summary, "evidence": e.evidence}
                for e in soft_rows
            ]

        if "interactions" in include:
            interactions = _rows(db, select(SimulationInteraction).where(
                SimulationInteraction.simulation_id == simulation.id,
                SimulationInteraction.student_id == str(student_id),
            ).order_by(SimulationInteraction.sequence.desc()).limit(self.HISTORY_LIMIT))
            interaction_ids = [item.id for item in interactions]
            evaluations = _rows(db, select(InteractionEvaluation).where(
                InteractionEvaluation.simulation_id == simulation.id,
                InteractionEvaluation.student_id == str(student_id),
                InteractionEvaluation.interaction_id.in_(interaction_ids or [UUID(int=0)]),
            ).limit(self.HISTORY_LIMIT))
            evaluations_by_id = {evaluation.interaction_id: evaluation for evaluation in evaluations}
            context.interaction_history = [
                {"interaction_id": i.id, "occurred_at": i.occurred_at,
                 "interaction_type": i.interaction_type,
                 "decision": i.decision,
                 "evaluation": _interaction_evaluation(evaluations_by_id[i.id]) if i.id in evaluations_by_id else None}
                for i in interactions
            ]
            refs.extend(_evidence("simulation_interaction", i, student_id, simulation.id,
                                  i.task_id, "simulation_interactions", occurred_at=i.occurred_at)
                        for i in interactions)
            refs.extend(_evidence("interaction_evaluation", e, student_id, simulation.id,
                                  None, "interaction_evaluations") for e in evaluations)

        if "simulations" in include:
            completed = _rows(db, select(Simulation).where(
                Simulation.student_id == str(student_id), Simulation.status == SimulationStatus.COMPLETED,
                Simulation.id != simulation.id,
            ).order_by(Simulation.completed_at.desc()).limit(self.HISTORY_LIMIT))
            context.simulation_outcomes = [{"id": s.id, "title": s.title, "status": _value(s.status),
                                            "progress": s.progress, "completed_at": s.completed_at}
                                           for s in completed]
            refs.extend(_evidence("completed_simulation", s, student_id, s.id,
                                  None, "simulations", occurred_at=s.completed_at) for s in completed)

        context.evidence = _dedupe_refs(refs)
        try:
            return AgentContext.model_validate(context.model_dump())
        except ValidationError as exc:
            raise ValueError("Persisted simulation context contains an invalid record") from exc

    def build_references(self, *, simulation_id: UUID, student_id: str,
                         company_id: Optional[UUID] = None, task_id: Optional[UUID] = None,
                         criteria_version_id: Optional[UUID] = None,
                         submission_id: Optional[UUID] = None,
                         workflow_event_id: Optional[UUID] = None) -> AgentContext:
        """Compatibility helper for constructing reference-only contracts (no evidence claim)."""
        return AgentContext(simulation_id=simulation_id, student_id=student_id,
                            company_id=company_id, current_task_id=task_id,
                            criteria_version_id=criteria_version_id,
                            submission_id=submission_id, workflow_event_id=workflow_event_id)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _rows(db: Session, statement: Any) -> list[Any]:
    return list(db.execute(statement).scalars().all())


def _criteria(version: AcceptanceCriteriaVersion) -> dict[str, Any]:
    return {"id": version.id, "version_number": version.version_number,
            "status": version.status, "workflow_event_id": version.workflow_event_id,
            "items": list(version.items or []), "implicit": False}


def _criteria_id(version: Optional[SubmissionVersion]) -> Optional[UUID]:
    if version is None:
        return None
    identity = (version.version_metadata or {}).get("acceptance_criteria_version") or {}
    value = identity.get("id") if isinstance(identity, dict) else None
    try:
        return _uuid_or_none(value)
    except (TypeError, ValueError):
        return None


def _uuid_or_none(value: Any) -> Optional[UUID]:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _submission_criteria(version: SubmissionVersion) -> dict[str, Any]:
    metadata = version.version_metadata or {}
    identity = metadata.get("acceptance_criteria_version") or {}
    snapshot = metadata.get("acceptance_criteria_snapshot")
    return {"id": _criteria_id(version),
            "version_number": identity.get("version_number") if isinstance(identity, dict) else None,
            "status": identity.get("status") if isinstance(identity, dict) else None,
            "items": [{"text": text, "state": "active"} for text in snapshot]
            if isinstance(snapshot, list) else [],
            "implicit": bool(isinstance(identity, dict) and identity.get("implicit")),
            "snapshot_available": isinstance(snapshot, list)}


def _workflow_event(event: WorkflowEvent) -> dict[str, Any]:
    metadata = dict(getattr(event, "metadata_json", None) or {})
    return {"id": event.id, "task_id": event.task_id, "event_type": _value(event.event_type),
            "title": event.title, "message": event.message, "description": event.description,
            "original_requirement": getattr(event, "original_requirement", None),
            "updated_requirement": getattr(event, "updated_requirement", None),
            "status": _value(event.status), "action_required": event.action_required,
            "scenario_key": metadata.get("scenario_key"),
            "criteria_version_id": metadata.get("criteria_version_id"),
            "criteria_version_number": metadata.get("criteria_version_number"),
            "manager_message_id": metadata.get("manager_message_id"),
            "manager_response_message_id": metadata.get("manager_response_message_id"),
            "occurred_at": event.occurred_at, "response": event.response,
            "responded_at": event.responded_at}


def _project_event(event: Event) -> dict[str, Any]:
    return {"id": event.id, "event_type": _value(event.event_type),
            "description": event.description, "created_at": event.created_at}


def _message(message: Message) -> dict[str, Any]:
    return {"id": message.id, "sender_type": _value(message.sender_type),
            "sender_id": message.sender_id, "content": message.content,
            "created_at": message.created_at}


def _review(review: AIReview) -> dict[str, Any]:
    return {"id": review.id, "version_id": review.version_id,
            "status": _value(review.status), "result": _value(review.result),
            "severity": _value(review.severity), "score": review.score,
            "summary": review.summary, "strengths": list(review.strengths or []),
            "risks": list(review.risks or []), "actions": list(review.actions or []),
            "completed_at": review.completed_at}


def _interaction_evaluation(evaluation: InteractionEvaluation) -> dict[str, Any]:
    return {"id": evaluation.id, "overall_score": evaluation.overall_score,
            "competencies": evaluation.competencies or {}, "feedback": evaluation.feedback,
            "strengths": evaluation.strengths or [], "weaknesses": evaluation.weaknesses or [],
            "recommendations": getattr(evaluation, "recommendations", None) or [],
            "confidence": getattr(evaluation, "confidence", None)}


def _evidence(evidence_type: str, record: Any, student_id: str, simulation_id: Optional[UUID],
              task_id: Optional[UUID], source: str, *, submission_id: Optional[UUID] = None,
              submission_version_id: Optional[UUID] = None,
              workplace_event_id: Optional[UUID] = None,
              criteria_version_id: Optional[UUID] = None,
              occurred_at: Any = None) -> EvidenceReference:
    return EvidenceReference(evidence_type=evidence_type, evidence_record_id=record.id,
                             student_id=str(student_id), simulation_id=simulation_id,
                             task_id=task_id, submission_id=submission_id,
                             submission_version_id=submission_version_id,
                             workplace_event_id=workplace_event_id,
                             criteria_version_id=criteria_version_id,
                             occurred_at=occurred_at or getattr(record, "occurred_at", None)
                             or getattr(record, "submitted_at", None)
                             or getattr(record, "completed_at", None)
                             or getattr(record, "created_at", None), source=source)


def _dedupe_refs(references: Iterable[EvidenceReference]) -> list[EvidenceReference]:
    unique: dict[tuple[str, UUID], EvidenceReference] = {}
    for reference in references:
        unique[(reference.evidence_type, reference.evidence_record_id)] = reference
    return list(unique.values())
